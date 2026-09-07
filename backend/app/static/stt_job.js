(function (root) {
  "use strict";

  function createClientRequestId() {
    var t = Date.now().toString(36);
    var r = "";
    if (root.crypto && typeof root.crypto.randomUUID === "function") {
      r = root.crypto.randomUUID().replace(/-/g, "");
    } else {
      r = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
    }
    return "stt_" + t + "_" + r.slice(0, 16);
  }

  function backoffMs(attemptIndex) {
    var steps = [300, 1000, 2500];
    return steps[Math.min(Math.max(0, attemptIndex), steps.length - 1)];
  }

  var MAX_STT_UPLOAD_BYTES = 25 * 1024 * 1024;

  function shouldRetry(kind, status, nextAttempt, maxAttempts) {
    if (nextAttempt > maxAttempts) return false;
    if (kind === "empty_blob" || kind === "invalid_audio" || kind === "client_4xx" || kind === "audio_too_large") return false;
    if (status === 413) return false;
    if (status >= 400 && status < 500) return false;
    return (
      kind === "network" ||
      kind === "timeout" ||
      kind === "abort" ||
      kind === "server_5xx" ||
      status === 0 ||
      (status >= 500 && status <= 599)
    );
  }

  function validateBlob(blob, durationMs) {
    if (!blob) return { ok: false, stage: "blob_empty", message: "blob is missing", size: 0, mime: "" };
    var size = Number(blob.size || 0);
    var mime = String(blob.type || "");
    if (size <= 0) return { ok: false, stage: "blob_empty", message: "Blob size = 0", size: 0, mime: mime };
    if (size < 200) return { ok: false, stage: "blob_empty", message: "Blob too small: " + size, size: size, mime: mime };
    if (size > MAX_STT_UPLOAD_BYTES) {
      return {
        ok: false,
        stage: "audio_too_large",
        kind: "audio_too_large",
        message: "录音过长，请重新录制",
        size: size,
        mime: mime,
        durationMs: durationMs || 0,
      };
    }
    return { ok: true, stage: "audio_ready", message: "", size: size, mime: mime || "application/octet-stream", durationMs: durationMs || 0 };
  }

  function shouldInsertTranscript(data) {
    var code = String((data && data.code) || "");
    if (code === "prompt_leakage" || code === "empty_transcript") return false;
    return !!(data && String(data.text || "").trim());
  }

  function createDictationJob(clientRequestId) {
    var id = String(clientRequestId || "");
    var stopAccepted = false;
    var uploading = false;
    var uploadStarts = 0;
    return {
      clientRequestId: id,
      requestStop: function () {
        if (stopAccepted) return false;
        stopAccepted = true;
        return true;
      },
      startUpload: function () {
        if (uploading) return false;
        uploading = true;
        uploadStarts += 1;
        return true;
      },
      allowRetry: function () {
        uploading = false;
      },
      stats: function () {
        return {
          clientRequestId: id,
          stopAccepted: stopAccepted,
          uploading: uploading,
          uploadStarts: uploadStarts,
        };
      },
    };
  }

  function uploadOnce(opts) {
    var url = opts.url;
    var blob = opts.blob;
    var clientRequestId = opts.clientRequestId;
    var timeoutMs = opts.timeoutMs || 150000;
    var XHR = opts.XMLHttpRequestImpl || root.XMLHttpRequest;
    return new Promise(function (resolve) {
      var started = Date.now();
      var xhr;
      try {
        xhr = new XHR();
        xhr.open("POST", url, true);
        xhr.timeout = timeoutMs;
        xhr.responseType = "text";
        xhr.withCredentials = true;
        xhr.setRequestHeader("Content-Type", blob.type || "audio/wav");
        xhr.setRequestHeader("X-Client-Request-ID", clientRequestId);
      } catch (err) {
        resolve({
          ok: false,
          status: 0,
          body: "",
          serverRequestId: "",
          clientRequestId: clientRequestId,
          kind: "network",
          stage: "safari_send",
          message: String(err && err.message ? err.message : err),
          elapsedMs: Date.now() - started,
        });
        return;
      }
      xhr.onload = function () {
        var status = xhr.status;
        var kind = "ok";
        if (status >= 500) kind = "server_5xx";
        else if (status === 413) kind = "audio_too_large";
        else if (status >= 400) kind = "client_4xx";
        resolve({
          ok: status >= 200 && status < 300,
          status: status,
          body: xhr.responseText || "",
          serverRequestId: xhr.getResponseHeader("X-Request-ID") || "",
          clientRequestId: xhr.getResponseHeader("X-Client-Request-ID") || clientRequestId,
          kind: kind,
          stage: status >= 200 && status < 300 ? "response" : "http_error",
          message: status >= 200 && status < 300 ? "" : (("HTTP " + status) + (xhr.responseText ? (": " + String(xhr.responseText).slice(0, 180)) : "")),
          elapsedMs: Date.now() - started,
        });
      };
      xhr.onerror = function () {
        resolve({
          ok: false,
          status: 0,
          body: "",
          serverRequestId: xhr.getResponseHeader("X-Request-ID") || "",
          clientRequestId: clientRequestId,
          kind: "network",
          stage: "safari_send",
          message: "xhr.onerror",
          elapsedMs: Date.now() - started,
        });
      };
      xhr.ontimeout = function () {
        resolve({
          ok: false,
          status: 0,
          body: "",
          serverRequestId: "",
          clientRequestId: clientRequestId,
          kind: "timeout",
          stage: "timeout",
          message: "xhr.timeout",
          elapsedMs: Date.now() - started,
        });
      };
      xhr.onabort = function () {
        resolve({
          ok: false,
          status: 0,
          body: "",
          serverRequestId: "",
          clientRequestId: clientRequestId,
          kind: "abort",
          stage: "abort",
          message: "xhr.abort",
          elapsedMs: Date.now() - started,
        });
      };
      try {
        xhr.send(blob);
      } catch (err) {
        resolve({
          ok: false,
          status: 0,
          body: "",
          serverRequestId: "",
          clientRequestId: clientRequestId,
          kind: "network",
          stage: "safari_send",
          message: String(err && err.message ? err.message : err),
          elapsedMs: Date.now() - started,
        });
      }
    });
  }

  async function runUploadWithRetry(opts) {
    var maxAttempts = opts.maxAttempts || 3;
    var last = null;
    var clientRequestId = opts.clientRequestId;
    var blob = opts.blob;
    var sized = validateBlob(blob, 0);
    if (!sized.ok && sized.stage === "audio_too_large") {
      return {
        ok: false,
        status: 413,
        body: "",
        serverRequestId: "",
        clientRequestId: clientRequestId,
        kind: "audio_too_large",
        stage: "audio_too_large",
        message: "录音过长，请重新录制",
        elapsedMs: 0,
        attempt: 0,
      };
    }
    for (var attempt = 0; attempt < maxAttempts; attempt++) {
      last = await uploadOnce({
        url: opts.url,
        blob: blob,
        clientRequestId: clientRequestId,
        timeoutMs: opts.timeoutMs,
        XMLHttpRequestImpl: opts.XMLHttpRequestImpl,
      });
      last.attempt = attempt + 1;
      last.clientRequestId = clientRequestId;
      if (last.ok) return last;
      if (!shouldRetry(last.kind, last.status, attempt + 2, maxAttempts)) return last;
      await new Promise(function (resolve) { setTimeout(resolve, backoffMs(attempt)); });
    }
    return last;
  }

  root.EnpratoStt = {
    createClientRequestId: createClientRequestId,
    createDictationJob: createDictationJob,
    backoffMs: backoffMs,
    shouldRetry: shouldRetry,
    validateBlob: validateBlob,
    shouldInsertTranscript: shouldInsertTranscript,
    uploadOnce: uploadOnce,
    runUploadWithRetry: runUploadWithRetry,
    MAX_STT_UPLOAD_BYTES: MAX_STT_UPLOAD_BYTES,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);

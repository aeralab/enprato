import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const code = fs.readFileSync(path.join(here, "..", "backend", "app", "static", "stt_job.js"), "utf8");
const sandbox = {
  Math,
  Date,
  setTimeout,
  crypto: { randomUUID: () => "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" },
};
sandbox.globalThis = sandbox;
vm.runInNewContext(code, sandbox);
const Stt = sandbox.EnpratoStt;

function fakeXHR(script) {
  const calls = [];
  function XHR() {
    this.status = 0;
    this.responseText = "";
    this.timeout = 0;
    this.withCredentials = false;
    this.responseType = "";
    this._headers = {};
    this._respHeaders = {};
  }
  XHR.prototype.open = function (method, url) {
    this.method = method;
    this.url = url;
  };
  XHR.prototype.setRequestHeader = function (key, value) {
    this._headers[String(key).toLowerCase()] = value;
  };
  XHR.prototype.getResponseHeader = function (key) {
    return this._respHeaders[String(key).toLowerCase()] || null;
  };
  XHR.prototype.send = function (body) {
    const step = script[Math.min(calls.length, script.length - 1)];
    calls.push({
      method: this.method,
      url: this.url,
      headers: { ...this._headers },
      body,
      clientRequestId: this._headers["x-client-request-id"],
    });
    const self = this;
    setTimeout(() => {
      if (step.mode === "error") {
        self.onerror();
        return;
      }
      if (step.mode === "timeout") {
        self.ontimeout();
        return;
      }
      if (step.mode === "abort") {
        self.onabort();
        return;
      }
      self.status = step.status;
      self.responseText = step.body || "";
      self._respHeaders = {
        "x-request-id": step.serverId || "srv1",
        "x-client-request-id": this._headers["x-client-request-id"] || "",
        ...(step.headers || {}),
      };
      self.onload();
    }, 0);
  };
  return { XHR, calls };
}

test("createClientRequestId format is stable prefix", () => {
  const id = Stt.createClientRequestId();
  assert.match(id, /^stt_[a-z0-9]+_[a-z0-9]+$/i);
});

test("stop only once and retry keeps the same client_request_id", () => {
  const id = Stt.createClientRequestId();
  const job = Stt.createDictationJob(id);
  assert.equal(job.requestStop(), true);
  assert.equal(job.requestStop(), false);
  assert.equal(job.startUpload(), true);
  assert.equal(job.startUpload(), false);
  assert.equal(job.stats().uploadStarts, 1);
  job.allowRetry();
  assert.equal(job.startUpload(), true);
  assert.equal(job.stats().clientRequestId, id);
  assert.equal(job.stats().uploadStarts, 2);
});

test("quick consecutive stop does not create a second job id", () => {
  const first = Stt.createDictationJob("stt_one");
  const second = Stt.createDictationJob("stt_two");
  assert.equal(first.requestStop(), true);
  assert.equal(first.requestStop(), false);
  assert.equal(first.stats().clientRequestId, "stt_one");
  assert.equal(second.stats().clientRequestId, "stt_two");
});

test("validateBlob rejects size 0", () => {
  const r = Stt.validateBlob({ size: 0, type: "audio/wav" }, 1000);
  assert.equal(r.ok, false);
  assert.equal(r.stage, "blob_empty");
});

test("shouldRetry: network/timeout/5xx yes, 4xx and empty blob no", () => {
  assert.equal(Stt.shouldRetry("network", 0, 2, 3), true);
  assert.equal(Stt.shouldRetry("timeout", 0, 2, 3), true);
  assert.equal(Stt.shouldRetry("server_5xx", 500, 2, 3), true);
  assert.equal(Stt.shouldRetry("client_4xx", 400, 2, 3), false);
  assert.equal(Stt.shouldRetry("empty_blob", 0, 2, 3), false);
  assert.equal(Stt.shouldRetry("network", 0, 4, 3), false);
});

test("network first fail then success keeps client id", async () => {
  const id = "stt_retry_net";
  const { XHR, calls } = fakeXHR([{ mode: "error" }, { mode: "ok", status: 200, body: '{"text":"hi"}', serverId: "s2" }]);
  const result = await Stt.runUploadWithRetry({
    url: "/api/stt",
    blob: { size: 400, type: "audio/wav" },
    clientRequestId: id,
    timeoutMs: 1000,
    maxAttempts: 3,
    XMLHttpRequestImpl: XHR,
  });
  assert.equal(result.ok, true);
  assert.equal(calls.length, 2);
  assert.ok(calls.every((c) => c.clientRequestId === id));
  assert.equal(result.clientRequestId, id);
  assert.equal(calls[0].method, "POST");
});

test("first two network errors then success", async () => {
  const id = "stt_retry_net3";
  const { XHR, calls } = fakeXHR([
    { mode: "error" },
    { mode: "error" },
    { mode: "ok", status: 200, body: '{"text":"hi"}' },
  ]);
  const result = await Stt.runUploadWithRetry({
    url: "/api/stt",
    blob: { size: 400, type: "audio/wav" },
    clientRequestId: id,
    timeoutMs: 1000,
    maxAttempts: 3,
    XMLHttpRequestImpl: XHR,
  });
  assert.equal(result.ok, true);
  assert.equal(calls.length, 3);
  assert.ok(calls.every((c) => c.clientRequestId === id));
});

test("timeout then success", async () => {
  const { XHR, calls } = fakeXHR([{ mode: "timeout" }, { mode: "ok", status: 200, body: "{}" }]);
  const result = await Stt.runUploadWithRetry({
    url: "/api/stt",
    blob: { size: 400, type: "audio/wav" },
    clientRequestId: "stt_to",
    timeoutMs: 1000,
    maxAttempts: 3,
    XMLHttpRequestImpl: XHR,
  });
  assert.equal(result.ok, true);
  assert.equal(calls.length, 2);
});

test("server 500 then success", async () => {
  const { XHR, calls } = fakeXHR([
    { mode: "ok", status: 500, body: "boom" },
    { mode: "ok", status: 200, body: '{"text":"ok"}' },
  ]);
  const result = await Stt.runUploadWithRetry({
    url: "/api/stt",
    blob: { size: 400, type: "audio/wav" },
    clientRequestId: "stt_500",
    timeoutMs: 1000,
    maxAttempts: 3,
    XMLHttpRequestImpl: XHR,
  });
  assert.equal(result.ok, true);
  assert.equal(calls.length, 2);
});

test("server 400 is not retried", async () => {
  const { XHR, calls } = fakeXHR([{ mode: "ok", status: 400, body: "bad" }]);
  const result = await Stt.runUploadWithRetry({
    url: "/api/stt",
    blob: { size: 400, type: "audio/wav" },
    clientRequestId: "stt_400",
    timeoutMs: 1000,
    maxAttempts: 3,
    XMLHttpRequestImpl: XHR,
  });
  assert.equal(result.ok, false);
  assert.equal(result.kind, "client_4xx");
  assert.equal(calls.length, 1);
});

test("response abort is retryable then success", async () => {
  const { XHR, calls } = fakeXHR([{ mode: "abort" }, { mode: "ok", status: 200, body: "{}" }]);
  const result = await Stt.runUploadWithRetry({
    url: "/api/stt",
    blob: { size: 400, type: "audio/wav" },
    clientRequestId: "stt_abort",
    timeoutMs: 1000,
    maxAttempts: 3,
    XMLHttpRequestImpl: XHR,
  });
  assert.equal(result.ok, true);
  assert.equal(calls.length, 2);
});

test("413 is audio_too_large not network_error", async () => {
  const { XHR, calls } = fakeXHR([{ mode: "ok", status: 413, body: "too large" }]);
  const result = await Stt.runUploadWithRetry({
    url: "/api/stt",
    blob: { size: 400, type: "audio/wav" },
    clientRequestId: "stt_413",
    timeoutMs: 1000,
    maxAttempts: 3,
    XMLHttpRequestImpl: XHR,
  });
  assert.equal(result.ok, false);
  assert.equal(result.status, 413);
  assert.equal(result.kind, "audio_too_large");
  assert.notEqual(result.kind, "network");
  assert.equal(calls.length, 1);
});

test("oversized blob is audio_too_large before xhr", async () => {
  const { XHR, calls } = fakeXHR([{ mode: "ok", status: 200, body: '{"text":"nope"}' }]);
  const result = await Stt.runUploadWithRetry({
    url: "/api/stt",
    blob: { size: 25 * 1024 * 1024 + 1000, type: "audio/wav" },
    clientRequestId: "stt_huge_client",
    timeoutMs: 1000,
    maxAttempts: 3,
    XMLHttpRequestImpl: XHR,
  });
  assert.equal(result.ok, false);
  assert.equal(result.kind, "audio_too_large");
  assert.equal(result.stage, "audio_too_large");
  assert.equal(calls.length, 0);
});

test("validateBlob rejects oversize as audio_too_large", () => {
  const r = Stt.validateBlob({ size: 33 * 1024 * 1024, type: "audio/wav" }, 1000);
  assert.equal(r.ok, false);
  assert.equal(r.stage, "audio_too_large");
});

test("shouldRetry: 413 / audio_too_large no", () => {
  assert.equal(Stt.shouldRetry("audio_too_large", 413, 2, 3), false);
  assert.equal(Stt.shouldRetry("client_4xx", 413, 2, 3), false);
});

test("shouldInsertTranscript blocks prompt leakage even if text present", () => {
  assert.equal(Stt.shouldInsertTranscript({ text: "No extra sentences.", code: "prompt_leakage" }), false);
  assert.equal(Stt.shouldInsertTranscript({ text: "", code: "empty_transcript" }), false);
  assert.equal(Stt.shouldInsertTranscript({ text: "That is to be expected." }), true);
  assert.equal(Stt.shouldInsertTranscript({ text: "Come on." }), true);
});

test("same recording cannot start two overlapping uploads", () => {
  const job = Stt.createDictationJob("stt_one_asr");
  job.requestStop();
  assert.equal(job.startUpload(), true);
  assert.equal(job.startUpload(), false);
  assert.equal(job.stats().uploadStarts, 1);
});

export type SessionMediaStatus = "preparing" | "ready" | "failed" | "audio";

export type SessionMedia = {
  has_video: boolean;
  status?: SessionMediaStatus;
  session_id?: string;
};

export function shouldAcceptMediaPoll(boundSessionId: string, payloadSessionId?: string): boolean {
  const bound = (boundSessionId || "").trim();
  const incoming = (payloadSessionId || "").trim();
  if (!bound) return false;
  if (!incoming) return true;
  return bound === incoming;
}

export function inferInitialMediaStatus(hasVideo: boolean | undefined, sourceUrl?: string): SessionMediaStatus {
  if (hasVideo) return "ready";
  const src = (sourceUrl || "").toLowerCase();
  if (src.includes("bilibili.com") || src.includes("b23.tv")) return "preparing";
  return "audio";
}

export function audioOnlyPanel(status: SessionMediaStatus | undefined, hasVideo: boolean): { title: string; body: string } {
  if (hasVideo) return { title: "", body: "" };
  if (status === "failed") {
    return {
      title: "暂时无法加载视频画面",
      body: "音频学习不受影响。",
    };
  }
  if (status === "audio") {
    return {
      title: "仅音频",
      body: "本课没有视频画面，可使用音频听写、跟读。",
    };
  }
  return {
    title: "视频画面正在准备中",
    body: "你可以先开始听写，画面准备好后会自动显示。",
  };
}

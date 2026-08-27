export type VideoFitBox = {
  left: number;
  top: number;
  width: number;
  height: number;
};

export function computeVideoFitBox(
  containerWidth: number,
  containerHeight: number,
  videoWidth: number,
  videoHeight: number,
): VideoFitBox | null {
  if (!containerWidth || !containerHeight || !videoWidth || !videoHeight) return null;
  const containerAR = containerWidth / containerHeight;
  const videoAR = videoWidth / videoHeight;
  if (videoAR > containerAR) {
    const width = containerWidth;
    const height = containerWidth / videoAR;
    return { left: 0, top: (containerHeight - height) / 2, width, height };
  }
  const height = containerHeight;
  const width = containerHeight * videoAR;
  return { left: (containerWidth - width) / 2, top: 0, width, height };
}

export function burnWipeRatio(orientation: "landscape" | "portrait"): number {
  return orientation === "portrait" ? 0.44 : 0.42;
}

export function applyBurnWipeLayout(
  monitor: HTMLElement | null,
  video: HTMLVideoElement | null,
  orientation: "landscape" | "portrait",
): void {
  if (!monitor || !video) return;
  const containerWidth = monitor.clientWidth;
  const containerHeight = monitor.clientHeight;
  const box = computeVideoFitBox(
    containerWidth,
    containerHeight,
    video.videoWidth,
    video.videoHeight,
  );
  if (!box) return;
  const wipeH = box.height * burnWipeRatio(orientation);
  const wipeTop = box.top + box.height - wipeH;
  const captionBottom = containerHeight - box.top - box.height * 0.97;
  monitor.style.setProperty("--burn-left", `${box.left}px`);
  monitor.style.setProperty("--burn-top", `${wipeTop}px`);
  monitor.style.setProperty("--burn-width", `${box.width}px`);
  monitor.style.setProperty("--burn-height", `${wipeH}px`);
  monitor.style.setProperty("--caption-bottom", `${Math.max(8, captionBottom)}px`);
  monitor.classList.add("burn-layout-ready");
}

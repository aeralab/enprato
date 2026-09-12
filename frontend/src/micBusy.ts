export function shouldLockMicForServerAsr(hasLiveDraft: boolean): boolean {
  return !hasLiveDraft;
}

export function micBusyAfterServerAsr(): boolean {
  return false;
}

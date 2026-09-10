export const DICTATION_LOCKED_MESSAGE =
  "免费深度学习的 5 个素材已用完。开通会员后可继续学习新的素材。";

type UserLike = {
  membership?: { status?: string; active?: boolean };
  trial?: { remaining?: number };
} | null;

export function canEnterDictation(input: {
  requireAuth: boolean;
  user: UserLike;
  licenseActive?: boolean | null;
  sessionCanDeepStudy?: boolean | null;
}): boolean {
  if (input.sessionCanDeepStudy === true) return true;
  const member =
    input.user?.membership?.status === "active" || input.user?.membership?.active === true;
  if (member) return true;
  const remaining = Number(input.user?.trial?.remaining ?? 0);
  if (input.user && remaining > 0) return true;
  if (input.requireAuth) return false;
  if (input.user) return false;
  if (input.licenseActive == null) return true;
  return Boolean(input.licenseActive);
}

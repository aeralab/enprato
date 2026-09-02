export type CaptionMode = "off" | "en" | "bi";
export type Orientation = "landscape" | "portrait";

export type Phase =
  | "import"
  | "preparing"
  | "listen"
  | "dictate"
  | "check"
  | "shadow"
  | "result";

export type Sentence = {
  id: number;
  start: number;
  end: number;
  text: string;
};

export type WordSense = {
  word: string;
  phonetic: string;
  audio: string;
  defs_en: string[];
  defs_zh: string[];
  error?: string;
};

export type ShadowScore = {
  overall: number;
  pitch: number;
  speed: number;
  rhythm: number;
  content: number;
  orig_duration: number;
  user_duration: number;
  transcript: string;
  wer: number;
};

export type Highlight = {
  sentenceId: number;
  word: string;
};

export type LicenseStatus = {
  active: boolean;
  plan: "trial" | "monthly" | "lifetime" | "expired" | string;
  licensed: boolean;
  trial_active: boolean;
  trial_uses: number;
  trial_uses_limit: number;
  trial_imports: number;
  trial_imports_limit: number;
  trial_days: number;
  trial_ends_at: string;
  expires_at: string;
  email: string;
  pay_monthly_url?: string;
  pay_lifetime_url?: string;
  mock_pay_enabled?: boolean;
};

export type UpdateInfo = {
  version: string;
  download_url: string;
  notes?: string;
  published_at?: string;
  min_version?: string;
};

export type SessionSummary = {
  session_id: string;
  title: string;
  source_url: string;
  source_kind: string;
  updated_at: string;
  phase: Phase;
  index: number;
  count: number;
  done: number;
  duration: number;
  has_video?: boolean;
  thumbnail_url?: string;
  cover_url?: string;
};

export type SessionDetail = SessionSummary & {
  sentences: Sentence[];
  drafts: Record<string, string>;
  highlights: Highlight[];
  score: ShadowScore | null;
  orientation: Orientation;
  video_url: string;
  audio_url?: string;
  has_video?: boolean;
  created_at: string;
};

export type MembershipStatus = {
  status: "active" | "expired" | "none" | string;
  active: boolean;
  expires_at: string;
  plan?: string;
  plan_name?: string;
};

export type CurrentUser = {
  id: string;
  email: string;
  status: string;
  membership: MembershipStatus;
  trial: { limit: number; used: number; remaining: number };
};

export type Order = { id?: string; order_no: string; plan_code?: string; amount_fen: number; currency?: string; status: string; expires_at: string; payment?: { provider: string; code_url?: string } };

export type CuratedLesson = {
  id: string;
  lesson: number;
  title: string;
  source_url: string;
  series?: string;
};

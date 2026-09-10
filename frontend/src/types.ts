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
  progress_floor?: number;
  progress_anchor_count?: number;
  can_deep_study?: boolean;
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
  email: string | null;
  login_label?: string;
  status: string;
  membership: MembershipStatus;
  trial: { limit: number; used: number; remaining: number };
};

export type LearningRecord = {
  id: string;
  session_id: string;
  learning_date: string;
  language: string;
  title: string;
  source_kind: string;
  completed_at: string;
  duration_seconds: number;
  duration_minutes: number;
  sentence_count: number;
  completed_sentence_count: number;
  dictation_words?: number;
  evaluated_words?: number;
  accuracy: number | null;
  score: number | null;
  completion_ratio: number;
  completion_percent: number;
};

export type ProgressMilestone = {
  key: string;
  label: string;
  achieved: boolean;
  achieved_at: string | null;
};

export type ProgressSummary = {
  days_learned: number;
  day_one?: boolean;
  total_duration_seconds: number;
  total_duration_minutes: number;
  total_sentences: number;
  total_dictation_words?: number;
  current_streak: number;
  longest_streak: number;
  recent_accuracy: number | null;
  starting_accuracy: number | null;
  window_accuracy?: number | null;
  today?: {
    date: string;
    active: boolean;
    dictation_words: number;
    evaluated_words: number;
    correct_words?: number;
    accuracy: number | null;
    completed_units: number;
    audio_seconds: number;
  };
  records: LearningRecord[];
  milestones: ProgressMilestone[];
  comparison: {
    has_history: boolean;
    previous_count: number;
    accuracy_delta: number | null;
    sentence_delta: number | null;
  };
};

export type Order = { id?: string; order_no: string; plan?: string; plan_code?: string; amount_fen: number; currency?: string; status: string; expires_at: string; payment?: { provider: string; code_url?: string } };

export type CuratedLesson = {
  id: string;
  lesson: number;
  title: string;
  source_url: string;
  series?: string;
};

export type ImportJobStatus = {
  status: "queued" | "processing" | "ready" | "failed";
  stage?: string;
  message?: string;
  job_id?: string;
  session_id?: string;
  error_kind?: string;
  error?: string;
};

CREATE TABLE IF NOT EXISTS usage_quotas (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  trial_limit INTEGER NOT NULL DEFAULT 5,
  trial_used INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remote_tokens (
  token_hash TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES learning_sessions(session_id) ON DELETE CASCADE,
  owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_remote_tokens_session ON remote_tokens(session_id);

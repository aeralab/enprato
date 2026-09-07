CREATE TABLE IF NOT EXISTS learning_records (
  id TEXT PRIMARY KEY,
  owner_key TEXT NOT NULL,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL,
  learning_date TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT 'en',
  title TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL DEFAULT 'file',
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  duration_seconds INTEGER NOT NULL DEFAULT 0,
  sentence_count INTEGER NOT NULL DEFAULT 0,
  completed_sentence_count INTEGER NOT NULL DEFAULT 0,
  accuracy REAL,
  score REAL,
  error_word_count INTEGER,
  completion_ratio REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(owner_key, session_id)
);

CREATE INDEX IF NOT EXISTS idx_learning_records_owner_date
  ON learning_records(owner_key, learning_date, completed_at);

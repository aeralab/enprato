CREATE TABLE IF NOT EXISTS learning_events (
  id TEXT PRIMARY KEY,
  owner_key TEXT NOT NULL,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL,
  unit_key TEXT NOT NULL,
  parent_id TEXT,
  created_at TEXT NOT NULL,
  learning_date TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT 'en',
  material TEXT NOT NULL DEFAULT '',
  evaluated_words INTEGER NOT NULL DEFAULT 0,
  correct_words INTEGER NOT NULL DEFAULT 0,
  dictation_words INTEGER NOT NULL DEFAULT 0,
  audio_seconds REAL NOT NULL DEFAULT 0,
  splitter_version TEXT,
  UNIQUE(owner_key, session_id, unit_key)
);

CREATE INDEX IF NOT EXISTS idx_learning_events_owner_date
  ON learning_events(owner_key, learning_date, created_at);

CREATE TABLE IF NOT EXISTS learning_daily (
  owner_key TEXT NOT NULL,
  learning_date TEXT NOT NULL,
  evaluated_words INTEGER NOT NULL DEFAULT 0,
  correct_words INTEGER NOT NULL DEFAULT 0,
  dictation_words INTEGER NOT NULL DEFAULT 0,
  audio_seconds REAL NOT NULL DEFAULT 0,
  completed_units INTEGER NOT NULL DEFAULT 0,
  accuracy REAL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_key, learning_date)
);

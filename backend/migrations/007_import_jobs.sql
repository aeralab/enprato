CREATE TABLE IF NOT EXISTS import_jobs (
  job_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  source_url TEXT NOT NULL,
  url_key TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  error_kind TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_import_jobs_user_status ON import_jobs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_import_jobs_user_url ON import_jobs(user_id, url_key, status);
CREATE INDEX IF NOT EXISTS idx_import_jobs_session ON import_jobs(session_id);

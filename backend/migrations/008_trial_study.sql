ALTER TABLE learning_sessions ADD COLUMN active_study_seconds INTEGER NOT NULL DEFAULT 0;
ALTER TABLE learning_sessions ADD COLUMN trial_consumed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE learning_sessions ADD COLUMN last_study_at TEXT;

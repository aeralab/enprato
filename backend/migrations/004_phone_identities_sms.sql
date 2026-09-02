PRAGMA foreign_keys=OFF;
CREATE TABLE users_new (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE,
  password_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  last_login_at TEXT
);
INSERT INTO users_new(id,email,password_hash,status,created_at,last_login_at)
SELECT id,email,password_hash,status,created_at,last_login_at FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS user_identities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  provider_user_id TEXT NOT NULL,
  verified_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(provider, provider_user_id)
);
CREATE INDEX IF NOT EXISTS idx_user_identities_user ON user_identities(user_id);

CREATE TABLE IF NOT EXISTS sms_challenges (
  id TEXT PRIMARY KEY,
  phone TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  request_ip TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sms_challenges_phone_created ON sms_challenges(phone, created_at);
CREATE INDEX IF NOT EXISTS idx_sms_challenges_ip_created ON sms_challenges(request_ip, created_at);

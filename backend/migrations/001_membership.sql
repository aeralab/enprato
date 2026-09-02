CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  price_fen INTEGER NOT NULL,
  duration_days INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memberships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_id INTEGER NOT NULL REFERENCES plans(id),
  starts_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  source_order_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL REFERENCES users(id),
  plan_id INTEGER NOT NULL REFERENCES plans(id),
  amount_fen INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  provider TEXT,
  provider_trade_no TEXT,
  created_at TEXT NOT NULL,
  paid_at TEXT
);

CREATE TABLE IF NOT EXISTS payment_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  event_id TEXT NOT NULL,
  order_no TEXT,
  payload_hash TEXT NOT NULL,
  verified_at TEXT,
  processed_at TEXT,
  result TEXT,
  UNIQUE(provider, event_id)
);

CREATE TABLE IF NOT EXISTS learning_sessions (
  session_id TEXT PRIMARY KEY,
  owner_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memberships_user_expiry ON memberships(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_learning_sessions_owner ON learning_sessions(owner_user_id);

INSERT OR IGNORE INTO plans(code, name, price_fen, duration_days) VALUES
  ('monthly_30d', 'Enprato 月度会员 30 天', 1990, 30);

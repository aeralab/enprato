INSERT OR IGNORE INTO plans(code, name, price_fen, duration_days) VALUES
  ('yearly_365d', 'Enprato 年度会员 365 天', 19900, 365);

CREATE TABLE IF NOT EXISTS redeem_codes (
  code TEXT PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plans(id),
  plan_code TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'unused',
  redeemed_by TEXT REFERENCES users(id),
  redeemed_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_redeem_codes_status ON redeem_codes(status);

ALTER TABLE memberships ADD COLUMN started_at TEXT;
ALTER TABLE orders ADD COLUMN expires_at TEXT;
CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,provider TEXT NOT NULL,mode TEXT NOT NULL DEFAULT 'one_time',status TEXT NOT NULL DEFAULT 'inactive',current_period_start TEXT,current_period_end TEXT,auto_renew INTEGER NOT NULL DEFAULT 0,external_contract_id TEXT,next_billing_at TEXT,cancel_at_period_end INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_provider_trade_no ON orders(provider, provider_trade_no) WHERE provider_trade_no IS NOT NULL AND provider_trade_no <> '';
UPDATE memberships SET started_at=starts_at WHERE started_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_orders_status_expiry ON orders(status, expires_at);

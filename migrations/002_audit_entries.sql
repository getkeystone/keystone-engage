-- Keystone Engage: audit entries in PostgreSQL
-- Run against keystone_engage database on AnchorNode

CREATE TABLE IF NOT EXISTS audit_entries (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    prev_hash TEXT NOT NULL,
    curr_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_entries (event_type);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_entries (timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_curr_hash ON audit_entries (curr_hash);

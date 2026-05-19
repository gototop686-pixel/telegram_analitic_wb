-- Migration 007: Strategy proposals + market routing for publishes

ALTER TABLE publishes ADD COLUMN IF NOT EXISTS obsidian_path TEXT;
ALTER TABLE publishes ADD COLUMN IF NOT EXISTS market TEXT;

CREATE TABLE IF NOT EXISTS strategy_proposals (
    id              SERIAL PRIMARY KEY,
    raw_event_id    INT,
    draft_id        INT REFERENCES drafts(id),
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    category        TEXT DEFAULT 'general',
    status          TEXT DEFAULT 'pending',  -- 'pending', 'approved', 'rejected'
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

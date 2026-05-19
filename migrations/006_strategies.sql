-- Migration 006: Strategies storage for GoToTop
CREATE TABLE IF NOT EXISTS strategies (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    category    TEXT DEFAULT 'general',  -- 'sales', 'seo', 'competitor', 'promotion', 'general'
    created_by  BIGINT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

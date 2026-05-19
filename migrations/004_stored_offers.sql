-- Migration 004: Stored base offers for comparison
CREATE TABLE IF NOT EXISTS stored_offers (
    id          SERIAL PRIMARY KEY,
    locale      TEXT NOT NULL UNIQUE,  -- 'ru', 'hy', 'kz', 'en' etc.
    version     TEXT,                  -- user label e.g. "Оферта апрель 2025"
    text_content TEXT NOT NULL,
    filename    TEXT,
    uploaded_by BIGINT,
    uploaded_at TIMESTAMPTZ DEFAULT NOW()
);

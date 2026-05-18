-- ============================================================
-- Migration 001: Initial schema
-- Run in Supabase SQL Editor
-- ============================================================

-- Source registry: TG channels, YouTube keywords, web URLs
CREATE TABLE IF NOT EXISTS source_registry (
    id          SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,         -- 'telegram', 'youtube', 'rss', 'web'
    source_tier TEXT NOT NULL,         -- 'official', 'media', 'blog'
    locale      TEXT NOT NULL,         -- 'ru', 'hy', 'any'
    identifier  TEXT NOT NULL UNIQUE,  -- @channel, keyword, URL
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Raw incoming events (never mutated after insert)
CREATE TABLE IF NOT EXISTS raw_events (
    id           SERIAL PRIMARY KEY,
    source_id    INT REFERENCES source_registry(id),
    source_type  TEXT NOT NULL,
    external_id  TEXT,                 -- message_id, video_id, etc.
    url          TEXT,
    title        TEXT,
    body         TEXT,
    lang_detected TEXT,
    content_hash TEXT UNIQUE,          -- for deduplication
    fetched_at   TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ           -- NULL = not yet processed
);

-- Normalized documents (after LLM classification)
CREATE TABLE IF NOT EXISTS normalized_documents (
    id                  SERIAL PRIMARY KEY,
    raw_event_id        INT REFERENCES raw_events(id),
    primary_label       TEXT,
    confidence          FLOAT,
    summary_ru          TEXT,
    summary_hy          TEXT,
    entities            JSONB,
    source_tier         TEXT,
    incident_cluster_id INT,
    corroboration_summary TEXT,
    confidence_band     TEXT,          -- 'confirmed_official','single_weak','conflicting'
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Incident clusters (grouped by topic)
CREATE TABLE IF NOT EXISTS incident_clusters (
    id          SERIAL PRIMARY KEY,
    topic_label TEXT,
    first_seen  TIMESTAMPTZ DEFAULT NOW(),
    last_seen   TIMESTAMPTZ DEFAULT NOW(),
    alert_tier  INT DEFAULT 2          -- 1=critical, 2=digest
);

-- Drafts waiting for moderator approval
CREATE TABLE IF NOT EXISTS drafts (
    id            SERIAL PRIMARY KEY,
    cluster_id    INT REFERENCES incident_clusters(id),
    body_ru       TEXT,
    body_hy       TEXT,
    status        TEXT DEFAULT 'pending', -- 'pending','approved','rejected','published'
    tg_message_id INT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    approved_at   TIMESTAMPTZ,
    approved_by   BIGINT
);

-- Published posts log
CREATE TABLE IF NOT EXISTS publishes (
    id         SERIAL PRIMARY KEY,
    draft_id   INT REFERENCES drafts(id),
    channel_id BIGINT NOT NULL,
    locale     TEXT NOT NULL,
    tg_message_id INT,
    published_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Channel routing (locale -> list of channel_ids for publishing)
CREATE TABLE IF NOT EXISTS channel_routing (
    id         SERIAL PRIMARY KEY,
    locale     TEXT NOT NULL,          -- 'ru', 'hy'
    channel_id BIGINT NOT NULL UNIQUE,
    label      TEXT,
    active     BOOLEAN DEFAULT TRUE
);

-- RBAC users
CREATE TABLE IF NOT EXISTS rbac_users (
    id         SERIAL PRIMARY KEY,
    tg_user_id BIGINT UNIQUE NOT NULL,
    username   TEXT,
    role       TEXT DEFAULT 'moderator', -- 'admin','moderator','viewer'
    active     BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- LLM cost telemetry
CREATE TABLE IF NOT EXISTS llm_cost_log (
    id           SERIAL PRIMARY KEY,
    operation    TEXT,
    input_tokens INT,
    output_tokens INT,
    model        TEXT,
    cost_usd     FLOAT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- Seed: initial sources
-- ============================================================

INSERT INTO source_registry (source_type, source_tier, locale, identifier) VALUES
-- RU Telegram channels
('telegram', 'media', 'ru', '@wbofficialchat'),
('telegram', 'media', 'ru', '@globalwb'),
('telegram', 'blog',  'ru', '@namarketplacewithleo'),
('telegram', 'blog',  'ru', '@kudahiko'),
('telegram', 'blog',  'ru', '@Azizinmarketplace'),
('telegram', 'blog',  'ru', '@t_zamaraev'),
('telegram', 'blog',  'ru', '@ekspertmp'),
-- HY Telegram channels
('telegram', 'blog',  'hy', '@GarikMkrtchyann'),
('telegram', 'media', 'hy', '@infografikawbchat'),
('telegram', 'official', 'hy', '@WBArmeniaOfficial'),
-- YouTube keywords
('youtube', 'media', 'ru', 'Wildberries'),
('youtube', 'media', 'ru', 'WB комиссия'),
('youtube', 'media', 'ru', 'WB оферта'),
('youtube', 'media', 'ru', 'маркетплейс Wildberries'),
('youtube', 'media', 'ru', 'WB продавцы'),
('youtube', 'media', 'ru', 'WB логистика'),
('youtube', 'media', 'ru', 'WB SEO'),
('youtube', 'media', 'ru', 'выкупы WB'),
('youtube', 'media', 'any', 'WB Армения'),
('youtube', 'media', 'any', 'WB ЕАЭС'),
('youtube', 'media', 'ru', 'ФАС маркетплейс'),
('youtube', 'media', 'ru', 'маркетплейс регуляторика'),
('youtube', 'media', 'ru', 'Wildberries изменения'),
('youtube', 'media', 'ru', 'селлеры WB'),
-- Official web/RSS
('rss', 'official', 'ru', 'https://seller.wildberries.ru/news'),
('web', 'official', 'ru', 'https://www.wildberries.ru/services/seller')
ON CONFLICT (identifier) DO NOTHING;

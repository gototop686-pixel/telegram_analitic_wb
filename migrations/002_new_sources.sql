-- Migration 002: Add law/regulatory sources and first admin
-- Run in Railway PostgreSQL → Data → Query

-- New RSS/web sources for laws and regulatory monitoring
INSERT INTO source_registry (source_type, source_tier, locale, identifier) VALUES
-- Официальные новости РФ (законы)
('rss', 'official', 'ru', 'http://kremlin.ru/acts/news/feed'),
('rss', 'official', 'ru', 'https://fas.gov.ru/news.rss'),
('rss', 'official', 'ru', 'https://www.nalog.gov.ru/rss/'),
-- WB официальный блог/новости
('web', 'official', 'ru', 'https://seller.wildberries.ru/dynamic-content-feed'),
-- Армения официальные источники
('rss', 'official', 'hy', 'https://www.gov.am/ru/rss/'),
('rss', 'official', 'hy', 'https://www.parliament.am/news.php?lang=rus&NewsType=2'),
-- Отраслевые СМИ
('rss', 'media', 'ru', 'https://oborot.ru/rss.xml'),
('rss', 'media', 'ru', 'https://vc.ru/rss/tag/wildberries')
ON CONFLICT (identifier) DO NOTHING;

-- Add first admin (replace with your actual Telegram ID if different)
INSERT INTO rbac_users (tg_user_id, username, role)
VALUES (8224677283, 'gototop_wb', 'admin')
ON CONFLICT (tg_user_id) DO UPDATE SET role = 'admin', active = TRUE;

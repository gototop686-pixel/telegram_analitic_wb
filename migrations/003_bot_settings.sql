-- Migration 003: Bot settings table for runtime configuration
CREATE TABLE IF NOT EXISTS bot_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Default settings
INSERT INTO bot_settings (key, value) VALUES
('min_confidence', '0.45'),
('relevant_labels', 'Регуляторика_RU,Регуляторика_AM,Таможня_ЕАЭС,Маркетплейс_политика_WB,Изменение_оферты,Коммуникации_WB,Антимонопольное_ФАС,Комиссии_логистика'),
('gototop_context', 'Ты аналитик компании GoToTop — консалтинговой компании для продавцов на Wildberries. Аудитория: предприниматели из стран ЕАЭС (Армения, Казахстан, Кыргызстан, Беларусь, Россия), работающие с WB и другими маркетплейсами. Важно: изменения комиссий и тарифов WB, изменения оферты, таможенные правила ЕАЭС, регуляторика РФ/АМ/КЗ, ФАС, блокировки продавцов, логистика, кросс-бордер торговля с Китаем.'),
('post_style', 'Профессиональный, без воды. 150-250 слов. Структура: заголовок → суть → что это значит для продавца → вывод. HTML-форматирование для Telegram.')
ON CONFLICT (key) DO NOTHING;

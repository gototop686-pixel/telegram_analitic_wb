-- Migration 005: Move filter keywords from hardcode to bot_settings
-- CORE keywords: at least 1 required in any text before Claude is called
-- CONTEXT keywords: 2+ required if no CORE found (soft filter for media/telegram sources)

INSERT INTO bot_settings (key, value) VALUES
(
  'filter_core_keywords',
  'wildberries,вайлдберриз,seller.wildberries,маркетплейс,маркетплейсы,ozon,озон,kaspi,каспи,селлер,wb продавец,продавец wb,еаэс,таможня,фас '
),
(
  'filter_context_keywords',
  'комиссия,тариф,логистика,штраф,оферта,ввоз товар,импорт товар,поставщик,карточка товара,ранжирование,торговля,кросс-бордер'
),
(
  'filter_gov_strict',
  'true'
)
ON CONFLICT (key) DO NOTHING;

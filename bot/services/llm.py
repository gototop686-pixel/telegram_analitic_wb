import os
import anthropic

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


GOTOTOP_CONTEXT = """Ты аналитик компании GoToTop — консалтинговой компании для продавцов на Wildberries.
Аудитория: армянские предприниматели, работающие с WB в рамках ЕАЭС.
Особо важно для них:
- Изменения комиссий, тарифов, логистики WB
- Изменения оферты WB (влияют на все продажи)
- Таможенные правила ЕАЭС для торговли Армения-Россия
- Регуляторные изменения РФ и Армении для маркетплейсов
- Действия ФАС против WB
- Блокировки и штрафы продавцов

КРИТИЧНО (tier 1): изменения оферты, штрафы/блокировки, таможенные законы, новые требования к продавцам, решения ФАС.
ДАЙДЖЕСТ (tier 2): советы, SEO, статистика, общие новости рынка."""

TAXONOMY = [
    "Регуляторика_RU", "Регуляторика_AM", "Таможня_ЕАЭС",
    "Маркетплейс_политика_WB", "Изменение_оферты", "Коммуникации_WB",
    "Антимонопольное_ФАС", "Стратегия_продаж", "Выбор_карточки",
    "Анализ_ниши", "Финопереключатели", "Комиссии_логистика", "SEO_карточки",
]

CLASSIFY_PROMPT = """{context}

Получи текст и:
1. Определи тему из списка: {{taxonomy}}
2. Напиши краткое резюме на русском (2-3 предложения)
3. Напиши краткое резюме на армянском (2-3 предложения)
4. Оцени уровень важности: 1 (критично, срочно) или 2 (дайджест)

Ответь строго в формате JSON:
{{{{
  "label": "...",
  "confidence": 0.0-1.0,
  "summary_ru": "...",
  "summary_hy": "...",
  "alert_tier": 1 или 2,
  "entities": ["ключевые сущности"]
}}}}

Текст:
{{text}}""".format(context=GOTOTOP_CONTEXT)


async def classify_and_summarize(text: str) -> dict:
    import json
    from bot.db import queries as db_queries
    client = get_client()
    # Load context from DB (admin can edit via /menu)
    context = await db_queries.get_setting("gototop_context", GOTOTOP_CONTEXT)
    prompt = CLASSIFY_PROMPT.format(
        taxonomy=", ".join(TAXONOMY),
        text=text[:3000],
    ).replace(GOTOTOP_CONTEXT, context, 1)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    await _log_cost("classify", response, "claude-haiku-4-5-20251001")
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


async def _log_cost(operation: str, response, model: str) -> None:
    try:
        from bot.db import queries
        inp = response.usage.input_tokens
        out = response.usage.output_tokens
        # Approximate costs per million tokens
        rates = {
            "claude-haiku-4-5-20251001": (0.80, 4.00),
            "claude-sonnet-4-6": (3.00, 15.00),
        }
        in_rate, out_rate = rates.get(model, (3.00, 15.00))
        cost = (inp * in_rate + out * out_rate) / 1_000_000
        await queries.log_llm_cost(operation, inp, out, model, cost)
    except Exception:
        pass


DRAFT_PROMPT = """Ты редактор аналитического Telegram-канала GoToTop про Wildberries для армянских продавцов.
Напиши пост на основе аналитики ниже.

Тема: {label}
Резюме: {summary_ru}
Сущности: {entities}
Уровень уверенности: {confidence_band}

Требования к посту:
- Стиль: профессиональный, без воды
- Длина: 150-250 слов
- Структура: заголовок → суть → что это значит для продавца → вывод
- HTML-форматирование для Telegram (<b>, <i>, допустимы эмодзи)

Ответь в формате JSON: {{"body_ru": "...", "body_hy": "..."}}
Для body_hy переведи на армянский язык."""


OFFER_PROMPT = """Ты юридический аналитик по маркетплейсу Wildberries. Тебе передан текст оферты/документа WB.

Сделай детальный анализ:
1. Ключевые изменения или важные пункты
2. Риски для продавцов (что стало хуже)
3. Возможности (что стало лучше или нейтрально)
4. Конкретные цифры: комиссии, штрафы, сроки, лимиты
5. Что нужно сделать продавцу прямо сейчас

Документ:
{text}

Ответь в формате JSON:
{{
  "title": "краткое название документа",
  "key_changes": ["изменение 1", "изменение 2"],
  "risks": ["риск 1", "риск 2"],
  "opportunities": ["возможность 1"],
  "numbers": ["конкретная цифра 1", "конкретная цифра 2"],
  "action_required": "что делать продавцу",
  "summary_ru": "общее резюме 3-4 предложения",
  "summary_hy": "резюме на армянском 3-4 предложения",
  "urgency": 1 или 2
}}"""


async def analyze_offer(text: str) -> dict:
    client = get_client()
    import json
    prompt = OFFER_PROMPT.format(text=text[:8000])
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    # Trim to last closing brace in case of truncation
    last_brace = raw.rfind("}")
    if last_brace != -1:
        raw = raw[:last_brace + 1]
    return json.loads(raw)


async def generate_post(label: str, summary_ru: str, entities: list, confidence_band: str) -> dict:
    client = get_client()
    prompt = DRAFT_PROMPT.format(
        label=label,
        summary_ru=summary_ru,
        entities=", ".join(entities) if entities else "—",
        confidence_band=confidence_band,
    )
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    import json
    await _log_cost("generate_post", response, "claude-sonnet-4-6")
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)

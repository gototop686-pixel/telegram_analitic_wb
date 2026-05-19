import os
import json
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
3. Напиши краткое резюме на русском — специально для армянских продавцов (как это влияет на ЕАЭС/Армению, 2-3 предложения)
4. Оцени уровень важности: 1 (критично, срочно) или 2 (дайджест)
5. Определи рынок: "RU" (только Россия), "HY" (только Армения), "both" (оба рынка), "unclear" (непонятно)
6. Найди стратегию/тактику для продавца если есть (конкретный приём, лайфхак, обходной путь)

Ответь строго в формате JSON:
{{{{
  "label": "...",
  "confidence": 0.0-1.0,
  "summary_ru": "...",
  "summary_hy": "резюме на русском для армянских продавцов",
  "alert_tier": 1 или 2,
  "market": "RU" или "HY" или "both" или "unclear",
  "entities": ["ключевые сущности"],
  "has_strategy": true или false,
  "strategy_title": "краткое название стратегии или пусто",
  "strategy_body": "описание стратегии/тактики или пусто"
}}}}

Текст:
{{text}}""".format(context=GOTOTOP_CONTEXT)


async def classify_and_summarize(text: str) -> dict:
    from bot.db import queries as db_queries
    client = get_client()
    context = await db_queries.get_setting("gototop_context", GOTOTOP_CONTEXT)
    prompt = CLASSIFY_PROMPT.format(
        taxonomy=", ".join(TAXONOMY),
        text=text[:3000],
    ).replace(GOTOTOP_CONTEXT, context, 1)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
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
        rates = {
            "claude-haiku-4-5-20251001": (0.80, 4.00),
            "claude-sonnet-4-6": (3.00, 15.00),
        }
        in_rate, out_rate = rates.get(model, (3.00, 15.00))
        cost = (inp * in_rate + out * out_rate) / 1_000_000
        await queries.log_llm_cost(operation, inp, out, model, cost)
    except Exception:
        pass


DRAFT_PROMPT = """Ты редактор аналитического Telegram-канала GoToTop про Wildberries для продавцов.
Напиши пост на основе аналитики ниже.

Тема: {label}
Резюме: {summary_ru}
Сущности: {entities}
Уверенность: {confidence_band}

{strategies_block}

Структура поста (строго):
1. <b>Заголовок</b> — суть новости одной строкой
2. Основной текст — что произошло (2-3 предложения)
3. 🇷🇺 <b>Российские продавцы:</b> как это влияет конкретно на них
4. 🇦🇲 <b>Армянские продавцы:</b> как это влияет конкретно на них
5. 💡 <b>Что делать:</b> конкретные шаги, лазейки, обходные пути

Требования:
- HTML-форматирование для Telegram (<b>, <i>, допустимы эмодзи)
- Длина: 200-300 слов
- Никакой воды — только факты и конкретика
- Если есть стратегии GoToTop ниже — учитывай нашу позицию

Ответь в формате JSON:
{{"body_ru": "полный пост на русском", "body_hy": "полный пост на армянском"}}"""


CLUSTER_PROMPT = """Вот список новостей (индекс: заголовок). Найди те, которые описывают одно и то же событие или тему.

{titles}

Правила:
- Объединяй только если новости реально об одном и том же событии (не просто похожая тема)
- Каждая уникальная новость — отдельный кластер из одного элемента

Ответь JSON: {{"clusters": [[0,1], [2], [3,4,5]]}}
Индексы = позиции в исходном списке."""


async def cluster_events(events: list[dict]) -> list[list[int]]:
    """Group similar events. Returns list of index clusters."""
    if len(events) <= 1:
        return [[i] for i in range(len(events))]

    titles = "\n".join(f"{i}: {e.get('title', e.get('body', ''))[:80]}" for i, e in enumerate(events))
    client = get_client()
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": CLUSTER_PROMPT.format(titles=titles)}],
    )
    await _log_cost("cluster", response, "claude-haiku-4-5-20251001")
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw)
    return data.get("clusters", [[i] for i in range(len(events))])


COMPARE_PROMPT = """Ты юридический аналитик WB. Сравни СТАРУЮ и НОВУЮ версии оферты Wildberries.

СТАРАЯ ВЕРСИЯ:
{old_text}

НОВАЯ ВЕРСИЯ:
{new_text}

Найди конкретные отличия и ответь в JSON:
{{
  "has_changes": true/false,
  "added": ["новый пункт 1"],
  "removed": ["удалённый пункт 1"],
  "changed": ["было: X → стало: Y"],
  "critical_changes": ["изменение критичное для продавца"],
  "numbers_changed": ["старая цифра → новая цифра"],
  "summary_ru": "резюме изменений 3-4 предложения",
  "summary_hy": "резюме на русском для армянских продавцов",
  "urgency": 1 или 2
}}"""


async def compare_offers(old_text: str, new_text: str) -> dict:
    client = get_client()
    prompt = COMPARE_PROMPT.format(old_text=old_text[:4000], new_text=new_text[:4000])
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    await _log_cost("compare_offers", response, "claude-sonnet-4-6")
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    last_brace = raw.rfind("}")
    if last_brace != -1:
        raw = raw[:last_brace + 1]
    return json.loads(raw)


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
  "key_changes": ["изменение 1"],
  "risks": ["риск 1"],
  "opportunities": ["возможность 1"],
  "numbers": ["конкретная цифра 1"],
  "action_required": "что делать продавцу",
  "summary_ru": "общее резюме 3-4 предложения",
  "summary_hy": "резюме на русском для армянских продавцов 3-4 предложения",
  "urgency": 1 или 2
}}"""


async def analyze_offer(text: str) -> dict:
    client = get_client()
    prompt = OFFER_PROMPT.format(text=text[:8000])
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    await _log_cost("analyze_offer", response, "claude-sonnet-4-6")
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    last_brace = raw.rfind("}")
    if last_brace != -1:
        raw = raw[:last_brace + 1]
    return json.loads(raw)


async def generate_post(
    label: str,
    summary_ru: str,
    entities: list,
    confidence_band: str,
    strategies: list[dict] | None = None,
) -> dict:
    client = get_client()
    if strategies:
        lines = ["СТРАТЕГИИ КОМПАНИИ GOTOTOP (учитывай при написании поста):"]
        for s in strategies:
            lines.append(f"\n### {s['title']} [{s.get('category', '')}]\n{s['body'][:400]}")
        strategies_block = "\n".join(lines)
    else:
        strategies_block = ""
    prompt = DRAFT_PROMPT.format(
        label=label,
        summary_ru=summary_ru,
        entities=", ".join(entities) if entities else "—",
        confidence_band=confidence_band,
        strategies_block=strategies_block,
    )
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    await _log_cost("generate_post", response, "claude-sonnet-4-6")
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)

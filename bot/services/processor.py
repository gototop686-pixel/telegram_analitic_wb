from aiogram import Bot

from bot.db import queries
from bot.services import llm
from bot.services.publisher import send_draft_to_moderators

# CORE keywords — must have at least one for any tier
_CORE = [
    "wildberries", "вайлдберриз", "seller.wildberries",
    "wb ", " wb,", " wb.", "вб ", "вб,",
    "маркетплейс", "маркетплейсы",
    "оферта wb", "оферта вб",
    "озон", "ozon", "kaspi", "каспи",
    "селлер", "продавец на wb", "продавец на маркетплейс",
]

# CONTEXT keywords — support CORE; for weak sources, 2+ required without CORE
_CONTEXT = [
    "комиссия", "тариф", "логистика", "штраф за",
    "таможня", "таможенн", "еаэс",
    "фас ", "антимонопол",
    "оферта", "личный кабинет продавца",
    "поставщик", "карточка товара", "ранжирование",
    "импорт товар", "ввоз товар", "торговля арм",
]


def _keyword_passes(text: str, strict: bool = False) -> bool:
    """
    strict=True (for government/regulatory sources): requires at least 1 CORE keyword.
    strict=False (for telegram/media): 1 CORE OR 2+ CONTEXT is enough.
    """
    low = text.lower()
    has_core = any(kw in low for kw in _CORE)
    if has_core:
        return True
    if strict:
        return False
    context_count = sum(1 for kw in _CONTEXT if kw in low)
    return context_count >= 2


async def process_unprocessed_events(bot: Bot, processing_tier: str = "all") -> int:
    max_events = int(await queries.get_setting("max_events_per_run", "10"))

    if processing_tier == "all":
        events = await queries.get_unprocessed_events(limit=max_events)
    else:
        events = await queries.get_unprocessed_events_tiered(processing_tier, limit=max_events)

    min_confidence = float(await queries.get_setting("min_confidence", "0.45"))
    relevant_labels_str = await queries.get_setting("relevant_labels", "")
    relevant_labels = set(relevant_labels_str.split(",")) if relevant_labels_str else set()

    # Government RSS sources get strict keyword filtering (must have a CORE keyword)
    strict_filter = processing_tier == "weekly"

    processed = 0
    for event in events:
        try:
            text = f"{event.get('title', '')}\n{event.get('body', '')}"
            if len(text.strip()) < 30:
                await queries.mark_event_processed(event["id"])
                continue

            if not _keyword_passes(text, strict=strict_filter):
                print(f"[processor] Keyword skip {event['id']} (tier={processing_tier})")
                await queries.mark_event_processed(event["id"])
                continue

            classification = await llm.classify_and_summarize(text)
            confidence = classification.get("confidence", 0)
            label = classification.get("label", "")
            alert_tier = classification.get("alert_tier", 2)

            is_relevant = (
                confidence >= min_confidence
                and (not relevant_labels or label in relevant_labels or alert_tier == 1)
            )

            if not is_relevant:
                print(f"[processor] Low relevance {event['id']}: label={label}, conf={confidence:.2f}")
                await queries.mark_event_processed(event["id"])
                continue

            # Save RAW classified data to Obsidian (regardless of draft approval)
            try:
                from bot.services.obsidian import save_raw_to_obsidian
                await save_raw_to_obsidian(
                    source_type=event.get("source_type", ""),
                    source_url=event.get("url", ""),
                    title=event.get("title", ""),
                    body=event.get("body", ""),
                    classification=classification,
                    event_id=event["id"],
                )
            except Exception as obs_err:
                print(f"[obsidian] RAW save failed: {obs_err}")

            confidence_band = _get_confidence_band(classification)
            post = await llm.generate_post(
                label=label,
                summary_ru=classification.get("summary_ru", ""),
                entities=classification.get("entities", []),
                confidence_band=confidence_band,
            )

            draft_id = await queries.create_draft(
                body_ru=post.get("body_ru", ""),
                body_hy=post.get("body_hy", ""),
            )

            tier_emoji = "🔴 КРИТИЧНО" if alert_tier == 1 else "🟡 Дайджест"
            source_info = _format_source(event)

            await send_draft_to_moderators(
                draft_id=draft_id,
                body_ru=post.get("body_ru", ""),
                body_hy=post.get("body_hy", ""),
                bot=bot,
                tier_label=tier_emoji,
                source_info=source_info,
                label=label,
                confidence=confidence,
            )

            await queries.mark_event_processed(event["id"])
            processed += 1

        except Exception as e:
            print(f"[processor] Error processing event {event['id']}: {e}")

    return processed


def _format_source(event: dict) -> str:
    source_type = event.get("source_type", "")
    url = event.get("url", "")
    title = event.get("title", "")
    type_map = {"telegram": "Telegram", "rss": "RSS", "youtube": "YouTube", "offer": "Оферта"}
    t = type_map.get(source_type, source_type.upper())
    if url:
        return f"<a href='{url}'>{t}: {title[:60] or url[:60]}</a>"
    return f"{t}: {title[:60]}"


def _get_confidence_band(classification: dict) -> str:
    confidence = classification.get("confidence", 0.5)
    if confidence >= 0.8:
        return "confirmed_official"
    elif confidence >= 0.5:
        return "single_weak"
    else:
        return "conflicting"

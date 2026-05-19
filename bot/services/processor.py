from aiogram import Bot

from bot.db import queries
from bot.services import llm
from bot.services.publisher import send_draft_to_moderators


async def process_unprocessed_events(bot: Bot) -> int:
    events = await queries.get_unprocessed_events(limit=20)

    # Load settings from DB
    min_confidence = float(await queries.get_setting("min_confidence", "0.45"))
    relevant_labels_str = await queries.get_setting("relevant_labels", "")
    relevant_labels = set(relevant_labels_str.split(",")) if relevant_labels_str else set()

    processed = 0
    for event in events:
        try:
            text = f"{event.get('title', '')}\n{event.get('body', '')}"
            if len(text.strip()) < 30:
                await queries.mark_event_processed(event["id"])
                continue

            classification = await llm.classify_and_summarize(text)
            confidence = classification.get("confidence", 0)
            label = classification.get("label", "")
            alert_tier = classification.get("alert_tier", 2)

            # Relevance filter: skip low-confidence or off-topic events
            is_relevant = (
                confidence >= min_confidence
                and (not relevant_labels or label in relevant_labels or alert_tier == 1)
            )

            if not is_relevant:
                print(f"[processor] Skipped event {event['id']}: label={label}, confidence={confidence:.2f}")
                await queries.mark_event_processed(event["id"])
                continue

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

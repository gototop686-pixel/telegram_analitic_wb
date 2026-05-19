from aiogram import Bot

from bot.db import queries
from bot.services import llm
from bot.services.publisher import send_draft_to_moderators


async def process_unprocessed_events(bot: Bot) -> int:
    events = await queries.get_unprocessed_events(limit=20)
    processed = 0
    for event in events:
        try:
            text = f"{event.get('title', '')}\n{event.get('body', '')}"
            if len(text.strip()) < 30:
                await queries.mark_event_processed(event["id"])
                continue

            classification = await llm.classify_and_summarize(text)
            confidence_band = _get_confidence_band(classification)

            post = await llm.generate_post(
                label=classification.get("label", "Общее"),
                summary_ru=classification.get("summary_ru", ""),
                entities=classification.get("entities", []),
                confidence_band=confidence_band,
            )

            draft_id = await queries.create_draft(
                body_ru=post.get("body_ru", ""),
                body_hy=post.get("body_hy", ""),
            )

            alert_tier = classification.get("alert_tier", 2)
            tier_emoji = "🔴 КРИТИЧНО" if alert_tier == 1 else "🟡 Дайджест"
            await send_draft_to_moderators(
                draft_id=draft_id,
                body_ru=post.get("body_ru", ""),
                body_hy=post.get("body_hy", ""),
                bot=bot,
                tier_label=tier_emoji,
            )

            await queries.mark_event_processed(event["id"])
            processed += 1

        except Exception as e:
            print(f"[processor] Error processing event {event['id']}: {e}")

    return processed


def _get_confidence_band(classification: dict) -> str:
    confidence = classification.get("confidence", 0.5)
    if confidence >= 0.8:
        return "confirmed_official"
    elif confidence >= 0.5:
        return "single_weak"
    else:
        return "conflicting"

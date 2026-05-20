import asyncio
import time

from aiogram import Bot

from bot.db import queries
from bot.services import llm
from bot.services.publisher import send_draft_to_moderators, auto_publish_by_market

# Fallback defaults (used if DB has no keywords yet)
_DEFAULT_CORE = [
    "wildberries", "вайлдберриз", "seller.wildberries",
    "маркетплейс", "маркетплейсы", "ozon", "озон",
    "kaspi", "каспи", "селлер", "еаэс", "таможня", "фас ",
]
_DEFAULT_CONTEXT = [
    "комиссия", "тариф", "логистика", "штраф",
    "оферта", "ввоз товар", "импорт товар", "поставщик",
    "карточка товара", "ранжирование", "торговля", "кросс-бордер",
]


async def _load_keywords() -> tuple[list[str], list[str]]:
    core_str = await queries.get_setting("filter_core_keywords", "")
    context_str = await queries.get_setting("filter_context_keywords", "")
    core = [k.strip() for k in core_str.split(",") if k.strip()] if core_str else _DEFAULT_CORE
    context = [k.strip() for k in context_str.split(",") if k.strip()] if context_str else _DEFAULT_CONTEXT
    return core, context


def _keyword_passes(text: str, core: list[str], context: list[str], strict: bool = False) -> bool:
    """
    strict=True (regulatory/government sources): requires at least 1 CORE keyword.
    strict=False (telegram/media): 1 CORE OR 2+ CONTEXT keywords.
    """
    low = text.lower()
    if any(kw in low for kw in core):
        return True
    if strict:
        return False
    return sum(1 for kw in context if kw in low) >= 2


async def process_unprocessed_events(bot: Bot, processing_tier: str = "all") -> int:
    max_events = int(await queries.get_setting("max_events_per_run", "5"))
    min_confidence = float(await queries.get_setting("min_confidence", "0.45"))
    relevant_labels_str = await queries.get_setting("relevant_labels", "")
    _default_labels = {
        "Регуляторика_RU", "Регуляторика_AM", "Таможня_ЕАЭС",
        "Маркетплейс_политика_WB", "Изменение_оферты", "Коммуникации_WB",
        "Антимонопольное_ФАС", "Комиссии_логистика",
    }
    relevant_labels = set(relevant_labels_str.split(",")) if relevant_labels_str else _default_labels

    core_kw, context_kw = await _load_keywords()
    strategies = await queries.get_strategies_for_context(limit=5)

    strict_filter = processing_tier == "weekly"

    if processing_tier == "all":
        events = await queries.get_unprocessed_events(limit=max_events)
    else:
        events = await queries.get_unprocessed_events_tiered(processing_tier, limit=max_events)

    # Pre-filter by keywords
    candidate_events = []
    for event in events:
        text = f"{event.get('title', '')}\n{event.get('body', '')}"
        if len(text.strip()) < 30:
            await queries.mark_event_processed(event["id"])
            continue
        if not _keyword_passes(text, core_kw, context_kw, strict=strict_filter):
            print(f"[processor] Keyword skip {event['id']} (tier={processing_tier})")
            await queries.mark_event_processed(event["id"])
            continue
        candidate_events.append(event)

    if not candidate_events:
        return 0

    # Cluster similar events so duplicates produce only one post
    clusters = await llm.cluster_events(candidate_events)

    processed = 0
    for cluster_indices in clusters:
        # Use the first event in cluster as representative; mark all as processed
        representative = candidate_events[cluster_indices[0]]
        cluster_events_list = [candidate_events[i] for i in cluster_indices]

        try:
            text = f"{representative.get('title', '')}\n{representative.get('body', '')}"
            # For clustered events, combine titles/bodies for better context
            if len(cluster_indices) > 1:
                combined_titles = " | ".join(
                    candidate_events[i].get("title", "") for i in cluster_indices if candidate_events[i].get("title")
                )
                text = f"{combined_titles}\n{representative.get('body', '')}"

            classification = await llm.classify_and_summarize(text)
            confidence = classification.get("confidence", 0)
            label = classification.get("label", "")
            alert_tier = classification.get("alert_tier", 2)
            market = classification.get("market", "unclear")

            is_relevant = (
                confidence >= min_confidence
                and (not relevant_labels or label in relevant_labels or alert_tier == 1)
            )

            if not is_relevant:
                print(f"[processor] Low relevance cluster leader {representative['id']}: label={label}, conf={confidence:.2f}")
                for ev in cluster_events_list:
                    await queries.mark_event_processed(ev["id"])
                continue

            # Save RAW to Obsidian
            try:
                from bot.services.obsidian import save_raw_to_obsidian
                await save_raw_to_obsidian(
                    source_type=representative.get("source_type", ""),
                    source_url=representative.get("url", ""),
                    title=representative.get("title", ""),
                    body=representative.get("body", ""),
                    classification=classification,
                    event_id=representative["id"],
                )
            except Exception as obs_err:
                print(f"[obsidian] RAW save failed: {obs_err}")

            confidence_band = _get_confidence_band(classification)
            summary_ru = classification.get("summary_ru", "")
            post = await llm.generate_post(
                label=label,
                summary_ru=summary_ru,
                entities=classification.get("entities", []),
                confidence_band=confidence_band,
                strategies=strategies,
            )

            draft_id = await queries.create_draft(
                body_ru=post.get("body_ru", ""),
                body_hy=post.get("body_hy", ""),
            )

            source_url = representative.get("url", "")
            draft = {"id": draft_id, "body_ru": post.get("body_ru", ""), "body_hy": post.get("body_hy", "")}

            # Tier-1 (critical) → notify moderators AND auto-publish
            if alert_tier == 1:
                source_info = _format_source(representative)
                await send_draft_to_moderators(
                    draft_id=draft_id,
                    body_ru=post.get("body_ru", ""),
                    body_hy=post.get("body_hy", ""),
                    bot=bot,
                    tier_label="🔴 КРИТИЧНО",
                    source_info=source_info,
                    label=label,
                    confidence=confidence,
                )

            # Auto-publish to channels based on market
            await auto_publish_by_market(
                draft=draft,
                market=market,
                bot=bot,
                label=label,
                source_url=source_url,
                summary_ru=summary_ru,
            )

            # Handle strategy proposals extracted by Claude
            if classification.get("has_strategy") and classification.get("strategy_title"):
                await _handle_strategy_proposal(
                    draft_id=draft_id,
                    raw_event_id=representative["id"],
                    title=classification["strategy_title"],
                    body=classification.get("strategy_body", ""),
                    label=label,
                    bot=bot,
                )

            for ev in cluster_events_list:
                await queries.mark_event_processed(ev["id"])
            processed += 1
            # Pause between events to respect Gemini free tier (15 RPM)
            await asyncio.sleep(5)

        except Exception as e:
            err_str = str(e)
            # API errors (no credits, rate limit) — keep events in queue for retry
            if any(kw in err_str.lower() for kw in ("credit balance", "rate_limit", "529", "overloaded", "gemini", "quota", "api error")):
                print(f"[processor] API error on {representative['id']}, keeping in queue: {e}")
            else:
                # Other errors (bad JSON, missing field) — mark processed to avoid infinite loop
                print(f"[processor] Processing error on {representative['id']}, marking done: {e}")
                for ev in cluster_events_list:
                    try:
                        await queries.mark_event_processed(ev["id"])
                    except Exception:
                        pass

    return processed


async def _handle_strategy_proposal(
    draft_id: int, raw_event_id: int,
    title: str, body: str, label: str,
    bot: Bot,
) -> None:
    """Save strategy proposal and notify admin DMs."""
    try:
        proposal_id = await queries.save_strategy_proposal(
            raw_event_id=raw_event_id,
            draft_id=draft_id,
            title=title,
            body=body,
            category=label,
        )
        admin_ids = await queries.get_admin_ids()
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Добавить в стратегии", callback_data=f"strat_ok:{proposal_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"strat_no:{proposal_id}"),
        ]])
        preview = (
            f"💡 <b>Новая стратегия (предложение #{proposal_id})</b>\n"
            f"🏷 {label}\n\n"
            f"<b>{title}</b>\n\n"
            f"{body[:500]}"
        )
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, preview, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                print(f"[processor] Cannot reach admin {admin_id}: {e}")
    except Exception as e:
        print(f"[processor] Strategy proposal failed: {e}")


async def batch_keyword_filter() -> dict:
    """Free pre-pass: mark irrelevant events as processed without calling Claude."""
    events = await queries.get_unprocessed_events(limit=1000)
    core_kw, context_kw = await _load_keywords()
    skipped = 0
    kept = 0
    for event in events:
        text = f"{event.get('title', '')}\n{event.get('body', '')}"
        if len(text.strip()) < 30 or not _keyword_passes(text, core_kw, context_kw, strict=False):
            await queries.mark_event_processed(event["id"])
            skipped += 1
        else:
            kept += 1
    return {"skipped": skipped, "kept": kept}


async def process_by_source_type(bot: Bot, source_type: str) -> int:
    """Process unprocessed events filtered by source_type."""
    max_events = int(await queries.get_setting("max_events_per_run", "30"))
    min_confidence = float(await queries.get_setting("min_confidence", "0.45"))
    relevant_labels_str = await queries.get_setting("relevant_labels", "")
    _default_labels = {
        "Регуляторика_RU", "Регуляторика_AM", "Таможня_ЕАЭС",
        "Маркетплейс_политика_WB", "Изменение_оферты", "Коммуникации_WB",
        "Антимонопольное_ФАС", "Комиссии_логистика",
    }
    relevant_labels = set(relevant_labels_str.split(",")) if relevant_labels_str else _default_labels
    core_kw, context_kw = await _load_keywords()
    strategies = await queries.get_strategies_for_context(limit=5)

    events = await queries.get_unprocessed_events_by_source_type(source_type, limit=max_events)

    # Pre-filter by keywords
    candidate_events = []
    for event in events:
        text = f"{event.get('title', '')}\n{event.get('body', '')}"
        if len(text.strip()) < 30 or not _keyword_passes(text, core_kw, context_kw):
            await queries.mark_event_processed(event["id"])
            continue
        candidate_events.append(event)

    if not candidate_events:
        return 0

    clusters = await llm.cluster_events(candidate_events)
    processed = 0

    for cluster_indices in clusters:
        representative = candidate_events[cluster_indices[0]]
        cluster_events_list = [candidate_events[i] for i in cluster_indices]
        try:
            text = f"{representative.get('title', '')}\n{representative.get('body', '')}"
            if len(cluster_indices) > 1:
                combined_titles = " | ".join(
                    candidate_events[i].get("title", "") for i in cluster_indices if candidate_events[i].get("title")
                )
                text = f"{combined_titles}\n{representative.get('body', '')}"

            classification = await llm.classify_and_summarize(text)
            confidence = classification.get("confidence", 0)
            label = classification.get("label", "")
            alert_tier = classification.get("alert_tier", 2)
            market = classification.get("market", "unclear")

            is_relevant = (
                confidence >= min_confidence
                and (not relevant_labels or label in relevant_labels or alert_tier == 1)
            )
            if not is_relevant:
                for ev in cluster_events_list:
                    await queries.mark_event_processed(ev["id"])
                continue

            try:
                from bot.services.obsidian import save_raw_to_obsidian
                await save_raw_to_obsidian(
                    source_type=representative.get("source_type", ""),
                    source_url=representative.get("url", ""),
                    title=representative.get("title", ""),
                    body=representative.get("body", ""),
                    classification=classification,
                    event_id=representative["id"],
                )
            except Exception:
                pass

            summary_ru = classification.get("summary_ru", "")
            post = await llm.generate_post(
                label=label,
                summary_ru=summary_ru,
                entities=classification.get("entities", []),
                confidence_band=_get_confidence_band(classification),
                strategies=strategies,
            )
            draft_id = await queries.create_draft(post.get("body_ru", ""), post.get("body_hy", ""))
            draft = {"id": draft_id, "body_ru": post.get("body_ru", ""), "body_hy": post.get("body_hy", "")}

            if alert_tier == 1:
                await send_draft_to_moderators(
                    draft_id=draft_id, body_ru=post.get("body_ru", ""),
                    body_hy=post.get("body_hy", ""), bot=bot,
                    tier_label="🔴 КРИТИЧНО",
                    source_info=_format_source(representative), label=label, confidence=confidence,
                )

            await auto_publish_by_market(
                draft=draft, market=market, bot=bot,
                label=label, source_url=representative.get("url", ""), summary_ru=summary_ru,
            )

            if classification.get("has_strategy") and classification.get("strategy_title"):
                await _handle_strategy_proposal(
                    draft_id=draft_id, raw_event_id=representative["id"],
                    title=classification["strategy_title"],
                    body=classification.get("strategy_body", ""),
                    label=label, bot=bot,
                )

            for ev in cluster_events_list:
                await queries.mark_event_processed(ev["id"])
            processed += 1
            await asyncio.sleep(5)
        except Exception as e:
            err_str = str(e)
            if any(kw in err_str.lower() for kw in ("credit balance", "rate_limit", "529", "overloaded", "gemini", "quota", "api error")):
                print(f"[processor] API error on {representative['id']}, keeping in queue: {e}")
            else:
                print(f"[processor] Processing error on {representative['id']}, marking done: {e}")
                for ev in cluster_events_list:
                    try:
                        await queries.mark_event_processed(ev["id"])
                    except Exception:
                        pass
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

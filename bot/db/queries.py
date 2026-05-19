from .pool import get_pool


async def get_active_sources(source_type: str | None = None) -> list[dict]:
    pool = await get_pool()
    if source_type:
        rows = await pool.fetch(
            "SELECT * FROM source_registry WHERE active = TRUE AND source_type = $1",
            source_type,
        )
    else:
        rows = await pool.fetch("SELECT * FROM source_registry WHERE active = TRUE")
    return [dict(r) for r in rows]


async def insert_raw_event(
    source_id: int,
    source_type: str,
    external_id: str | None,
    url: str | None,
    title: str | None,
    body: str,
    lang_detected: str | None,
    content_hash: str,
) -> int | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO raw_events
            (source_id, source_type, external_id, url, title, body, lang_detected, content_hash)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (content_hash) DO NOTHING
        RETURNING id
        """,
        source_id, source_type, external_id, url, title, body, lang_detected, content_hash,
    )
    return row["id"] if row else None


async def get_unprocessed_events(limit: int = 50) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM raw_events WHERE processed_at IS NULL ORDER BY fetched_at LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


async def get_unprocessed_events_tiered(processing_tier: str, limit: int = 20) -> list[dict]:
    pool = await get_pool()
    if processing_tier == "frequent":
        # Telegram, YouTube, blog-tier, or any WB-related identifier
        rows = await pool.fetch(
            """
            SELECT re.* FROM raw_events re
            LEFT JOIN source_registry sr ON re.source_id = sr.id
            WHERE re.processed_at IS NULL
              AND (
                sr.source_type IN ('telegram', 'youtube')
                OR sr.source_tier = 'blog'
                OR sr.identifier ILIKE '%wildberries%'
              )
            ORDER BY re.fetched_at LIMIT $1
            """,
            limit,
        )
    elif processing_tier == "daily":
        # Media-tier RSS (not telegram/youtube — those are in 'frequent')
        rows = await pool.fetch(
            """
            SELECT re.* FROM raw_events re
            LEFT JOIN source_registry sr ON re.source_id = sr.id
            WHERE re.processed_at IS NULL
              AND sr.source_tier = 'media'
              AND sr.source_type NOT IN ('telegram', 'youtube')
            ORDER BY re.fetched_at LIMIT $1
            """,
            limit,
        )
    elif processing_tier == "weekly":
        # Official government sources (kremlin, fas, gov.am, etc.) — not WB-related
        rows = await pool.fetch(
            """
            SELECT re.* FROM raw_events re
            LEFT JOIN source_registry sr ON re.source_id = sr.id
            WHERE re.processed_at IS NULL
              AND sr.source_tier = 'official'
              AND sr.identifier NOT ILIKE '%wildberries%'
            ORDER BY re.fetched_at LIMIT $1
            """,
            limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM raw_events WHERE processed_at IS NULL ORDER BY fetched_at LIMIT $1",
            limit,
        )
    return [dict(r) for r in rows]


async def mark_event_processed(event_id: int) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE raw_events SET processed_at = NOW() WHERE id = $1", event_id
    )


async def create_draft(body_ru: str, body_hy: str, cluster_id: int | None = None) -> int:
    pool = await get_pool()
    row = await pool.fetchrow(
        "INSERT INTO drafts (body_ru, body_hy, cluster_id) VALUES ($1,$2,$3) RETURNING id",
        body_ru, body_hy, cluster_id,
    )
    return row["id"]


async def get_draft(draft_id: int) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM drafts WHERE id = $1", draft_id)
    return dict(row) if row else None


async def get_pending_drafts(limit: int = 10) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM drafts WHERE status='pending' ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


async def archive_old_events(days: int = 7) -> int:
    """Mark events older than N days as processed without Claude (send to Obsidian archive)."""
    pool = await get_pool()
    result = await pool.execute(
        """UPDATE raw_events SET processed_at = NOW()
           WHERE processed_at IS NULL
           AND fetched_at < NOW() - ($1 || ' days')::INTERVAL""",
        str(days),
    )
    # result is like "UPDATE 42"
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


async def get_unprocessed_by_type() -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT source_type, COUNT(*) as cnt
           FROM raw_events
           WHERE processed_at IS NULL
           GROUP BY source_type
           ORDER BY cnt DESC"""
    )
    return [dict(r) for r in rows]


async def preview_unprocessed_titles(limit: int = 30) -> list[dict]:
    """Return titles/sources of unprocessed events for free preview (no Claude)."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT re.id, re.source_type, re.title, re.url, re.fetched_at,
                  COALESCE(sr.identifier, '') as source_id
           FROM raw_events re
           LEFT JOIN source_registry sr ON re.source_id = sr.id
           WHERE re.processed_at IS NULL
           ORDER BY re.fetched_at DESC
           LIMIT $1""",
        limit,
    )
    return [dict(r) for r in rows]


async def get_unprocessed_events_by_source_type(source_type: str, limit: int = 30) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM raw_events WHERE processed_at IS NULL AND source_type=$1 ORDER BY fetched_at LIMIT $2",
        source_type, limit,
    )
    return [dict(r) for r in rows]


async def approve_draft(draft_id: int, approved_by: int) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE drafts SET status='approved', approved_at=NOW(), approved_by=$1 WHERE id=$2",
        approved_by, draft_id,
    )


async def reject_draft(draft_id: int) -> None:
    pool = await get_pool()
    await pool.execute("UPDATE drafts SET status='rejected' WHERE id=$1", draft_id)


async def get_publish_channels(locale: str) -> list[int]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT channel_id FROM channel_routing WHERE locale=$1 AND active=TRUE", locale
    )
    return [r["channel_id"] for r in rows]


async def log_publish(draft_id: int, channel_id: int, locale: str, tg_message_id: int) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO publishes (draft_id, channel_id, locale, tg_message_id) VALUES ($1,$2,$3,$4)",
        draft_id, channel_id, locale, tg_message_id,
    )


async def get_moderator_ids() -> list[int]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT tg_user_id FROM rbac_users WHERE active=TRUE AND role IN ('admin','moderator')"
    )
    return [r["tg_user_id"] for r in rows]


async def get_admin_ids() -> list[int]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT tg_user_id FROM rbac_users WHERE active=TRUE AND role='admin'"
    )
    return [r["tg_user_id"] for r in rows]


async def upsert_user(tg_user_id: int, username: str | None, role: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO rbac_users (tg_user_id, username, role)
        VALUES ($1, $2, $3)
        ON CONFLICT (tg_user_id) DO UPDATE SET role=$3, username=$2, active=TRUE
        """,
        tg_user_id, username, role,
    )


async def log_llm_cost(operation: str, input_tokens: int, output_tokens: int, model: str, cost_usd: float) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO llm_cost_log (operation, input_tokens, output_tokens, model, cost_usd) VALUES ($1,$2,$3,$4,$5)",
        operation, input_tokens, output_tokens, model, cost_usd,
    )


async def get_stored_offer(locale: str) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM stored_offers WHERE locale=$1", locale)
    return dict(row) if row else None


async def save_stored_offer(locale: str, text_content: str, filename: str, uploaded_by: int, version: str = "") -> None:
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO stored_offers (locale, text_content, filename, uploaded_by, version, uploaded_at)
           VALUES ($1,$2,$3,$4,$5,NOW())
           ON CONFLICT (locale) DO UPDATE
           SET text_content=$2, filename=$3, uploaded_by=$4, version=$5, uploaded_at=NOW()""",
        locale, text_content, filename, uploaded_by, version,
    )


async def list_stored_offers() -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch("SELECT locale, version, filename, uploaded_at FROM stored_offers ORDER BY locale")
    return [dict(r) for r in rows]


async def search_events(query: str, limit: int = 5) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, source_type, url, title, body, fetched_at
           FROM raw_events
           WHERE (title ILIKE $1 OR body ILIKE $1)
           ORDER BY fetched_at DESC LIMIT $2""",
        f"%{query}%", limit,
    )
    return [dict(r) for r in rows]


async def get_llm_cost_total() -> float:
    pool = await get_pool()
    val = await pool.fetchval("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_cost_log")
    return float(val)


async def get_sources_by_type() -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT source_type, locale, COUNT(*) as cnt FROM source_registry WHERE active=TRUE GROUP BY source_type, locale ORDER BY source_type, locale"
    )
    return [dict(r) for r in rows]


async def get_setting(key: str, default: str = "") -> str:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT value FROM bot_settings WHERE key=$1", key)
    return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO bot_settings (key, value) VALUES ($1,$2) ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=NOW()",
        key, value,
    )


async def add_source(source_type: str, source_tier: str, locale: str, identifier: str) -> bool:
    pool = await get_pool()
    try:
        await pool.execute(
            "INSERT INTO source_registry (source_type, source_tier, locale, identifier) VALUES ($1,$2,$3,$4)",
            source_type, source_tier, locale, identifier,
        )
        return True
    except Exception:
        return False


async def deactivate_source(source_id: int) -> None:
    pool = await get_pool()
    await pool.execute("UPDATE source_registry SET active=FALSE WHERE id=$1", source_id)


async def get_sources_paginated(source_type: str | None = None, limit: int = 10, offset: int = 0) -> list[dict]:
    pool = await get_pool()
    if source_type:
        rows = await pool.fetch(
            "SELECT * FROM source_registry WHERE active=TRUE AND source_type=$1 ORDER BY id LIMIT $2 OFFSET $3",
            source_type, limit, offset,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM source_registry WHERE active=TRUE ORDER BY source_type, id LIMIT $1 OFFSET $2",
            limit, offset,
        )
    return [dict(r) for r in rows]


async def add_publish_channel(locale: str, channel_id: int, label: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO channel_routing (locale, channel_id, label) VALUES ($1,$2,$3) ON CONFLICT (channel_id) DO NOTHING",
        locale, channel_id, label,
    )


async def remove_publish_channel(channel_id: int) -> None:
    pool = await get_pool()
    await pool.execute("UPDATE channel_routing SET active=FALSE WHERE channel_id=$1", channel_id)


async def get_publish_channels_full() -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch("SELECT * FROM channel_routing WHERE active=TRUE ORDER BY locale")
    return [dict(r) for r in rows]


async def get_bot_stats() -> dict:
    pool = await get_pool()
    raw_count = await pool.fetchval("SELECT COUNT(*) FROM raw_events")
    unprocessed = await pool.fetchval("SELECT COUNT(*) FROM raw_events WHERE processed_at IS NULL")
    drafts_pending = await pool.fetchval("SELECT COUNT(*) FROM drafts WHERE status='pending'")
    published = await pool.fetchval("SELECT COUNT(*) FROM publishes")
    sources = await pool.fetchval("SELECT COUNT(*) FROM source_registry WHERE active=TRUE")
    return {
        "raw_total": raw_count,
        "unprocessed": unprocessed,
        "drafts_pending": drafts_pending,
        "published": published,
        "sources": sources,
    }


# ── Strategies ─────────────────────────────────────────────────────────────

async def save_strategy(title: str, body: str, category: str, created_by: int) -> int:
    pool = await get_pool()
    row = await pool.fetchrow(
        "INSERT INTO strategies (title, body, category, created_by) VALUES ($1,$2,$3,$4) RETURNING id",
        title, body, category, created_by,
    )
    return row["id"]


async def get_strategies(category: str | None = None, limit: int = 20) -> list[dict]:
    pool = await get_pool()
    if category and category != "all":
        rows = await pool.fetch(
            "SELECT * FROM strategies WHERE category=$1 ORDER BY created_at DESC LIMIT $2",
            category, limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM strategies ORDER BY created_at DESC LIMIT $1", limit
        )
    return [dict(r) for r in rows]


async def get_strategy(strategy_id: int) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM strategies WHERE id=$1", strategy_id)
    return dict(row) if row else None


async def delete_strategy(strategy_id: int) -> None:
    pool = await get_pool()
    await pool.execute("DELETE FROM strategies WHERE id=$1", strategy_id)


async def get_strategies_for_context(limit: int = 5) -> list[dict]:
    """Fetch most recent strategies to use as RAG context for Claude."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT title, body, category FROM strategies ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]

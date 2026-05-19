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

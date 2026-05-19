import os
import base64
import httpx
from datetime import datetime

GITHUB_TOKEN = os.environ.get("OBSIDIAN_GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("OBSIDIAN_GITHUB_REPO", "")  # e.g. "gototop686/wb-notes"


async def _github_put(filename: str, content: str, commit_msg: str) -> bool:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False
    encoded = base64.b64encode(content.encode("utf-8")).decode()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={"message": commit_msg, "content": encoded},
        )
    if resp.status_code in (200, 201):
        print(f"[obsidian] Saved: {filename}")
        return True
    print(f"[obsidian] Error {resp.status_code}: {resp.text[:200]}")
    return False


async def save_raw_to_obsidian(
    source_type: str,
    source_url: str,
    title: str,
    body: str,
    classification: dict,
    event_id: int = 0,
) -> bool:
    """Save every classified (relevant) event to Obsidian RAW folder."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    label = classification.get("label", "Unknown")
    confidence = classification.get("confidence", 0)
    alert_tier = classification.get("alert_tier", 2)
    summary_ru = classification.get("summary_ru", "")
    summary_hy = classification.get("summary_hy", "")
    entities = ", ".join(classification.get("entities", []))

    safe_title = (title or f"event_{event_id}")[:60].replace("/", "-").replace(":", "-").replace('"', "")
    filename = f"RAW/{date_str}/{label}_{event_id}_{safe_title}.md"

    tier_str = "🔴 КРИТИЧНО" if alert_tier == 1 else "🟡 Дайджест"
    content = f"""---
date: {date_str}
event_id: {event_id}
source: {source_type}
url: {source_url or "—"}
label: {label}
confidence: {confidence:.2f}
alert_tier: {alert_tier}
entities: [{entities}]
status: raw
---

# {title or safe_title}

> {tier_str} · {label} · уверенность {confidence:.0%}

## Резюме RU
{summary_ru}

## Резюме HY
{summary_hy}

## Оригинальный текст
{body[:6000]}
"""
    return await _github_put(
        filename,
        content,
        f"📥 RAW [{label}] {safe_title}",
    )


async def save_strategy_to_obsidian(
    title: str,
    body: str,
    category: str,
    strategy_id: int = 0,
) -> bool:
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_title = title[:60].replace("/", "-").replace(":", "-").replace('"', "")
    filename = f"Стратегии/{category}/{safe_title}.md"
    content = f"""---
date: {date_str}
strategy_id: {strategy_id}
category: {category}
---

# {title}

{body}
"""
    return await _github_put(filename, content, f"🧠 [Стратегия] {safe_title}")


async def save_to_obsidian(
    title: str,
    body_ru: str,
    label: str,
    summary_ru: str = "",
    source_url: str = "",
    draft_id: int = 0,
) -> bool:
    """Save approved published post to Obsidian."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M")
    safe_title = title[:60].replace("/", "-").replace(":", "-").replace('"', "")
    filename = f"Аналитика/{date_str}/{safe_title}.md"

    content = f"""---
date: {date_str}
time: {time_str}
label: {label}
draft_id: {draft_id}
source: {source_url or "—"}
status: published
---

# {title}

## Резюме
{summary_ru}

## Пост (RU)
{body_ru}

---
*Опубликовано ботом GoToTop Analytics*
"""
    return await _github_put(
        filename,
        content,
        f"📊 [{label}] {safe_title}",
    )

import os
import base64
import httpx
from datetime import datetime

GITHUB_TOKEN = os.environ.get("OBSIDIAN_GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("OBSIDIAN_GITHUB_REPO", "")  # e.g. "gototop686-pixel/wb-notes"


async def save_to_obsidian(
    title: str,
    body_ru: str,
    label: str,
    summary_ru: str = "",
    source_url: str = "",
    draft_id: int = 0,
) -> bool:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False

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

    encoded = base64.b64encode(content.encode("utf-8")).decode()

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={
                "message": f"📊 [{label}] {safe_title}",
                "content": encoded,
            },
        )

    if resp.status_code in (200, 201):
        print(f"[obsidian] Saved: {filename}")
        return True
    else:
        print(f"[obsidian] Error {resp.status_code}: {resp.text[:200]}")
        return False

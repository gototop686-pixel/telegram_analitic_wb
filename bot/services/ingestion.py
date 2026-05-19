import hashlib
import httpx
import feedparser
import os

from bot.db import queries


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def ingest_rss(source: dict) -> int:
    url = source["identifier"]
    feed = feedparser.parse(url)
    saved = 0
    for entry in feed.entries[:20]:
        body = entry.get("summary", "") or entry.get("description", "")
        title = entry.get("title", "")
        link = entry.get("link", "")
        text = f"{title}\n{body}"
        content_hash = _hash(text)
        event_id = await queries.insert_raw_event(
            source_id=source["id"],
            source_type="rss",
            external_id=link,
            url=link,
            title=title,
            body=body,
            lang_detected=source["locale"],
            content_hash=content_hash,
        )
        if event_id:
            saved += 1
    return saved


async def ingest_youtube(source: dict) -> int:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return 0
    keyword = source["identifier"]
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": keyword,
        "type": "video",
        "order": "date",
        "maxResults": 10,
        "key": api_key,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            print(f"[youtube] Error {resp.status_code}: {resp.text[:200]}")
            return 0
        data = resp.json()

    saved = 0
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        video_id = item["id"].get("videoId", "")
        title = snippet.get("title", "")
        description = snippet.get("description", "")
        text = f"{title}\n{description}"
        content_hash = _hash(text)
        event_id = await queries.insert_raw_event(
            source_id=source["id"],
            source_type="youtube",
            external_id=video_id,
            url=f"https://youtube.com/watch?v={video_id}",
            title=title,
            body=description,
            lang_detected=source["locale"],
            content_hash=content_hash,
        )
        if event_id:
            saved += 1
    return saved


GOOGLE_NEWS_QUERIES = [
    "Wildberries",
    "Wildberries штраф блокировка",
    "Wildberries оферта изменения",
    "Wildberries Армения",
    "ФАС Wildberries",
    "Wildberries комиссия логистика",
]

GOOGLE_NEWS_SOURCE = {
    "id": -1,
    "source_type": "rss",
    "source_tier": "media",
    "locale": "ru",
}


async def ingest_google_news() -> int:
    saved = 0
    base = "https://news.google.com/rss/search?hl=ru&gl=RU&ceid=RU:ru&q="
    for query in GOOGLE_NEWS_QUERIES:
        url = base + query.replace(" ", "+")
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                body = entry.get("summary", "")
                text = f"{title}\n{body}"
                content_hash = _hash(text)
                event_id = await queries.insert_raw_event(
                    source_id=None,
                    source_type="rss",
                    external_id=link,
                    url=link,
                    title=title,
                    body=body,
                    lang_detected="ru",
                    content_hash=content_hash,
                )
                if event_id:
                    saved += 1
        except Exception as e:
            print(f"[google_news] Error for '{query}': {e}")
    return saved


async def run_all_ingestion() -> dict:
    sources = await queries.get_active_sources()
    results = {"rss": 0, "youtube": 0, "google_news": 0}
    for source in sources:
        if source["source_type"] == "rss":
            results["rss"] += await ingest_rss(source)
        elif source["source_type"] == "youtube":
            results["youtube"] += await ingest_youtube(source)
    results["google_news"] = await ingest_google_news()
    print(f"[ingestion] Done: {results}")
    return results

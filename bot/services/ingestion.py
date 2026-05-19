import asyncio
import hashlib
import httpx
import feedparser
import os

from bot.db import queries

# Government/official sources produce huge volumes of off-topic content.
# Pre-filter at ingestion time so the DB stays clean.
_INGEST_CORE = [
    "wildberries", "маркетплейс", "ozon", "озон", "kaspi",
    "selfer", "селлер", "продавец", "еаэс", "таможн",
    "комиссия", "тариф", "оферта", "фас ", "антимонопол",
    "ввоз товар", "импорт товар", "торговля",
]

# Identifiers of government/regulatory RSS feeds that need pre-filtering
_GOVERNMENT_IDENTIFIERS = {
    "kremlin.ru", "fas.gov.ru", "nalog.gov.ru",
    "gov.am", "parliament.am",
}


def _is_government_source(identifier: str) -> bool:
    return any(gov in identifier for gov in _GOVERNMENT_IDENTIFIERS)


def _ingest_keyword_passes(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in _INGEST_CORE)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _parse_feed_sync(url: str):
    return feedparser.parse(url)


async def _parse_feed(url: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _parse_feed_sync, url)


async def ingest_rss(source: dict) -> int:
    url = source["identifier"]
    is_gov = _is_government_source(url)
    feed = await _parse_feed(url)
    saved = 0
    for entry in feed.entries[:20]:
        body = entry.get("summary", "") or entry.get("description", "")
        title = entry.get("title", "")
        link = entry.get("link", "")
        text = f"{title}\n{body}"
        # Government sources: skip articles with no marketplace keywords at all
        if is_gov and not _ingest_keyword_passes(text):
            continue
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
    # WB новости
    "Wildberries",
    "Wildberries штраф блокировка",
    "Wildberries оферта изменения",
    "Wildberries Армения",
    "ФАС Wildberries",
    "Wildberries комиссия логистика",
    "Wildberries новые правила продавцы",
    "Wildberries тарифы 2025",
    # Законы РФ для маркетплейсов
    "маркетплейс закон регуляторика",
    "закон маркетплейс продавец 2025",
    "Минпромторг маркетплейс",
    "ФАС маркетплейс антимонопольное",
    # Таможня ЕАЭС
    "таможня ЕАЭС 2025",
    "ЕАЭС маркетплейс таможенные пошлины",
    "ВЭД ЕАЭС маркетплейс",
    # Армения законы
    "Армения таможня закон 2025",
    "Армения ЕАЭС маркетплейс",
    "Армения Wildberries продавцы",
    # Конкуренты
    "Ozon комиссия изменения продавцы",
    "Ozon штраф блокировка селлер",
    "Kaspi маркетплейс Казахстан продавцы",
    "Kaspi изменения тарифы",
    "Wildberries vs Ozon маркетплейс",
    # Кросс-бордер Китай
    "Китай маркетплейс ЕАЭС импорт",
    "кросс-бордер маркетплейс таможня",
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
            feed = await _parse_feed(url)
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


FORUM_RSS_SOURCES = [
    # VC.ru — уже может быть в source_registry, но добавим прямо в код
    {"url": "https://vc.ru/rss/tag/wildberries", "locale": "ru", "label": "vc.ru/wildberries"},
    {"url": "https://vc.ru/rss/tag/маркетплейсы", "locale": "ru", "label": "vc.ru/маркетплейсы"},
    # Ozon seller blog RSS
    {"url": "https://seller-edu.ozon.ru/rss.xml", "locale": "ru", "label": "ozon seller"},
]


async def ingest_forum_rss() -> int:
    saved = 0
    for source in FORUM_RSS_SOURCES:
        try:
            feed = await _parse_feed(source["url"])
            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                body = entry.get("summary", "") or entry.get("description", "")
                text = f"{title}\n{body}"
                content_hash = _hash(text)
                event_id = await queries.insert_raw_event(
                    source_id=None,
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
        except Exception as e:
            print(f"[forum_rss] Error {source['label']}: {e}")
    return saved


async def ingest_wb_release_notes() -> int:
    """Scrape WB developer release notes page."""
    url = "https://dev.wildberries.ru/en/release-notes"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return 0
            html = resp.text
        # Extract text blocks between <h2> tags (simple HTML parse without BS4)
        import re
        # Find all article-like blocks
        items = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL)
        saved = 0
        for item in items[:10]:
            clean = re.sub(r'<[^>]+>', '', item).strip()
            if len(clean) < 10:
                continue
            content_hash = _hash(clean)
            event_id = await queries.insert_raw_event(
                source_id=None,
                source_type="rss",
                external_id=content_hash[:16],
                url=url,
                title=f"WB Release Notes: {clean[:100]}",
                body=clean,
                lang_detected="ru",
                content_hash=content_hash,
            )
            if event_id:
                saved += 1
        return saved
    except Exception as e:
        print(f"[wb_release_notes] Error: {e}")
        return 0


async def run_all_ingestion() -> dict:
    sources = await queries.get_active_sources()
    results = {"rss": 0, "youtube": 0, "google_news": 0, "forums": 0, "wb_release": 0}
    for source in sources:
        if source["source_type"] == "rss":
            results["rss"] += await ingest_rss(source)
        elif source["source_type"] == "youtube":
            results["youtube"] += await ingest_youtube(source)
    results["google_news"] = await ingest_google_news()
    results["forums"] = await ingest_forum_rss()
    results["wb_release"] = await ingest_wb_release_notes()
    print(f"[ingestion] Done: {results}")
    return results

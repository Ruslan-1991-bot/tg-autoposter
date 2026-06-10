import feedparser
from urllib.parse import urlparse
import re
import html


# Источники под инвесторов/бизнес:
# крипто (проверенные) + макро/финансы + регуляторика/полит
RSS_FEEDS = [
    # ---- Crypto / Global ----
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),

    # ---- Macro / Markets ----
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),

    # ---- Russia (финансы/инвестиции) ----
    ("РБК", "https://www.rbc.ru/v10/ajax/get-news-feed/project/rbcnews.rss"),
    ("Интерфакс", "https://www.interfax.ru/rss.asp"),
    ("Коммерсант", "https://www.kommersant.ru/RSS/main.xml"),
    ("ТАСС", "https://tass.ru/rss/v2.xml"),

    # ---- Community signal (не истина, но бывает быстрый алерт) ----
    ("Smart-Lab", "https://smart-lab.ru/rss/"),
]



def _pick(entry, *fields, default=""):
    for f in fields:
        v = getattr(entry, f, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""

def clean_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    # убрать img полностью
    text = re.sub(r"<img[^>]*>", " ", text, flags=re.IGNORECASE)
    # убрать остальные теги
    text = re.sub(r"<[^>]+>", " ", text)
    # нормализовать пробелы
    text = " ".join(text.split())
    return text.strip()


def fetch_rss_items(limit: int = 10):
    """
    limit — сколько записей брать из КАЖДОГО источника.
    На выходе: список dict: id, title, link, summary, source
    """
    items = []
    seen_links = set()

    for source_name, url in RSS_FEEDS:
        feed = feedparser.parse(url)

        # если у фида есть проблемы — просто пропускаем, не валим весь бот
        if not getattr(feed, "entries", None):
            continue

        for e in feed.entries[:limit]:
            link = _pick(e, "link", default="")
            if not link:
                continue

            # антидубликат по ссылке
            if link in seen_links:
                continue
            seen_links.add(link)

            title = _pick(e, "title", default="").strip()
            if not title:
                continue

            summary = _pick(e, "summary", "description", default="").strip()

            item_id = _pick(e, "id", default="") or link
            src = source_name or _domain(link)

            items.append({
    "id": getattr(e, "id", None) or getattr(e, "link", None),
    "title": getattr(e, "title", "").strip(),
    "link": getattr(e, "link", "").strip(),
    "summary": clean_html(getattr(e, "summary", "")),
    "source": source_name,
})


    return items


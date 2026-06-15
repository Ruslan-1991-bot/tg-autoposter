import os
import re
import json
import difflib
from urllib.parse import urlparse, urlunparse
from typing import List, Dict, Tuple

from openai import AsyncOpenAI


ANALYSIS_SYSTEM_PROMPT = """
Ты — редактор-аналитик Telegram-канала для русскоязычной аудитории (инвесторы/бизнес).
Твоя задача: из набора новостей (США/глобал + Россия) сделать 1 короткий аналитический пост на русском.

Правила:
1) Отделяй ФАКТЫ от ОЦЕНОК. Факты: цифры, события, решения, даты. Оценки/эмоции/политика — вторично.
2) Сопоставляй версии разных источников: ищи совпадение фактов у независимых источников. Если факты расходятся — не утверждай, а укажи, что оценки/данные расходятся или недостаточно подтверждений.
3) Жёлтая пресса/кликбейт: эмоции, отсутствие цифр, “по словам источников” без подтверждений — снижать доверие. Не делать на этом основу поста.
4) Пиши просто, по-русски, без жаргона, но без воды.
5) Никаких ссылок. Источники указывай только названиями (1–3).
6) Не давай инвестиционных рекомендаций (не призывай покупать/продавать).
7) Избегай политической риторики. Имена без титулов, если титул не влияет на рынок.
8) Длина: 900–1200 знаков (стремись).
9) Структура обязательна:

🧠 <короткий заголовок>
🔥 Важность: X/10
✅ Достоверность: X/10
🏷 Категория: <Крипта / Регуляторика / Банки / Макроэкономика / Российский рынок / Фондовый рынок / Валюта / Бизнес>

Что произошло:
<1–2 предложения фактов>

Почему это важно:
<2–3 предложения простым языком>

Что это значит для инвестора:
<1–2 предложения без призыва покупать/продавать>

📌 Риски / последствия:
– <1–2 конкретных пункта>

💡 Вывод:
<1–2 предложения>

👤 Кому это важно:
– <1–2 группы>

Источники: <1–3 названия>

10) Как ставить оценки:
Важность 1–3: локальная/малозначимая новость.
Важность 4–6: средняя важность для рынка/инвесторов.
Важность 7–10: высокая важность: влияет на рынок, регулирование, банки, крипту, валюты или бизнес.
Достоверность 1–3: один слабый источник или непроверенная информация.
Достоверность 4–6: один нормальный источник или несколько косвенных.
Достоверность 7–10: несколько источников или авторитетный источник.
"""


HTML_TAG_RE = re.compile(r"<[^>]+>")


def clean_html(text: str) -> str:
    text = HTML_TAG_RE.sub("", text or "")
    return " ".join(text.split()).strip()


def clean_url(url: str) -> str:
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    except Exception:
        return url


def title_similarity(a: str, b: str) -> float:
    a = (a or "").lower().strip()
    b = (b or "").lower().strip()
    return difflib.SequenceMatcher(a=a, b=b).ratio()


def group_into_topics(items: List[Dict], min_sim: float = 0.55, max_sources_per_topic: int = 3) -> List[List[Dict]]:
    """
    Простая кластеризация по похожести заголовков.
    На выходе: список тем, каждая тема — список новостей (2–3 источника).
    """
    # нормализация
    norm = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        it = dict(it)
        it["title"] = title
        it["summary"] = clean_html(it.get("summary") or "")
        it["link"] = clean_url(it.get("link") or "")
        norm.append(it)

    used = set()
    topics: List[List[Dict]] = []

    for i, base in enumerate(norm):
        if i in used:
            continue
        topic = [base]
        used.add(i)

        for j in range(i + 1, len(norm)):
            if j in used:
                continue
            cand = norm[j]
            sim = title_similarity(base["title"], cand["title"])
            if sim >= min_sim:
                topic.append(cand)
                used.add(j)
                if len(topic) >= max_sources_per_topic:
                    break

        # нам нужны темы хотя бы из 2 источников (иначе слабая база)
        if len(topic) >= 2:
            topics.append(topic)

    return topics


def make_topic_id(topic: List[Dict]) -> str:
    """
    Стабильный id темы по заголовкам+доменам, чтобы не постить повторно.
    """
    parts = []
    for it in topic:
        dom = ""
        try:
            dom = urlparse(it.get("link", "")).netloc
        except Exception:
            dom = ""
        parts.append((it.get("title", "")[:80] + "|" + dom).lower())
    raw = "||".join(sorted(parts))
    # короткий хэш
    import hashlib
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


async def generate_ru_post(topic: List[Dict], model: str) -> str:
    """
    topic: список новостей (2–3+ источника) по одной теме
    """
    payload = []
    for it in topic:
        src = it.get("source")
        if not src:
            try:
                src = urlparse(it.get("link", "")).netloc
            except Exception:
                src = ""

        payload.append({
            "source": src,
            "title": (it.get("title") or "").strip(),
            "summary": (it.get("summary") or "")[:900],
        })

    user_content = {"news": payload}

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = await client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT.strip()},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
        ],
        temperature=0.4,
    )
    return (resp.output_text or "").strip()


def _text_of_topic(topic: List[Dict]) -> str:
    parts = []
    for it in topic:
        parts.append(it.get("title", ""))
        parts.append(it.get("summary", ""))
        parts.append(it.get("source", ""))
    return (" ".join(parts)).lower()


def topic_score(topic: List[Dict]) -> int:
    """
    Редакторский скоринг: чем выше — тем важнее для инвесторов/бизнеса.
    """
    text = _text_of_topic(topic)

    # Жёсткие стоп-слова: отсекаем мусор сразу
    drop_list = os.getenv("DROP_IF_CONTAINS", "")
    if drop_list:
        for w in [x.strip().lower() for x in drop_list.split(",") if x.strip()]:
            if w and w in text:
                return -999

    score = 0

    # 1) Регуляторика / законы / контроль
    reg = [
        "sec", "cftc", "regulator", "regulation", "bill", "law", "ban", "lawsuit",
        "fine", "investigation", "compliance", "kyc", "aml",
        "санкц", "регуля", "закон", "законопроект", "запрет", "штраф", "расследован",
        "цб", "центробанк", "минфин", "налог", "суд", "прокурат"
    ]
    if any(k in text for k in reg):
        score += 4

    # 2) Макро: ставки, инфляция, доллар, облигации, ликвидность
    macro = [
        "fed", "fomc", "interest rate", "rates", "inflation", "yield", "treasury",
        "dxy", "liquidity", "recession", "gdp",
        "фрс", "ставк", "инфляц", "доходност", "облигац", "ликвидн", "рецесс",
        "ввп", "доллар", "dxy"
    ]
    if any(k in text for k in macro):
        score += 4

    # 3) Институционалы: банки, фонды, ETF, крупный капитал
    inst = [
        "etf", "blackrock", "fidelity", "ark", "institutional", "fund", "bank",
        "custody", "asset manager", "sovereign", "pension",
        "банк", "фонд", "институцион", "etf", "управляющ", "активами", "кастоди"
    ]
    if any(k in text for k in inst):
        score += 3

    # 4) Рыночные риски: взломы, банкротства, стейблы, де-пег, биржи
    risk = [
        "hack", "exploit", "breach", "bankruptcy", "insolvency", "default",
        "stablecoin", "depeg", "peg", "exchange", "outage",
        "взлом", "эксплойт", "утечк", "банкрот", "дефолт",
        "стейбл", "депег", "бирж", "останов", "сбой"
    ]
    if any(k in text for k in risk):
        score += 3

    # 5) Если тема подтверждена несколькими источниками — плюс к весу
    # (у тебя 2–3 источника на тему — это уже редакционный сигнал)
    score += max(0, len(topic) - 1)

    return score


def filter_topics(topics: List[List[Dict]], min_score: int) -> List[List[Dict]]:
    scored: List[Tuple[int, List[Dict]]] = []
    for t in topics:
        s = topic_score(t)
        if s >= min_score:
            scored.append((s, t))

    # сортируем по важности (самое жирное — наверх)
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored]

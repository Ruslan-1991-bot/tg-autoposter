import json
from pathlib import Path

STORAGE = Path("/root/tg_autoposter/storage.json")


def _load():
    if not STORAGE.exists():
        return {"posted_ids": []}
    try:
        return json.loads(STORAGE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"posted_ids": []}


def _save(data):
    STORAGE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_new(item_id: str) -> bool:
    data = _load()
    return item_id not in set(data.get("posted_ids", []))


def mark_posted(item_id: str):
    data = _load()
    posted = data.get("posted_ids", [])
    posted.append(item_id)
    data["posted_ids"] = posted[-2000:]
    _save(data)


def format_post(item: dict) -> str:
    title = (item.get("title") or "").strip()
    link = (item.get("link") or "").strip()
    summary = (item.get("summary") or "").strip()

    if summary:
        summary = " ".join(summary.split())
        if len(summary) > 450:
            summary = summary[:450] + "…"

    text = f"🧠 {title}\n\n"
    if summary:
        text += f"{summary}\n\n"
    text += f"Источник: {link}\n#crypto #финансы"
    return text

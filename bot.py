import sys
sys.path.insert(0, "/root/tg_autoposter")
import os
import asyncio
from dotenv import load_dotenv
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sources import fetch_rss_items
from post_utils import is_new, mark_posted, format_post
from analyzer import group_into_topics, filter_topics, make_topic_id, generate_ru_post
from market import check_market_triggers
from market import update_market_ticks, check_market_alerts



load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")


async def job_post_news(bot: Bot):
    print("=== MARKET CHECK START ===")
    try:
        events = check_market_triggers()
        print(f"MARKET EVENTS: {len(events)}")
    except Exception as e:
        print(f"MARKET ERROR: {e}")
        events = []

    # 0) Market triggers (без спама — максимум 1 событие за запуск)
    posted_market = 0
    for ev in events:
        direction = "вырос" if ev["pct"] > 0 else "упал"
        pct = abs(ev["pct"])
        text = (
            f"📊 Сигнал рынка: {ev['asset']} {direction} на {pct:.2f}%\n\n"
            f"Текущая цена/уровень: {ev['price']:.2f}\n"
            f"Порог срабатывания: {ev['threshold']:.2f}%\n\n"
            f"Что это может значить:\n"
            f"— реакция рынка на новости/ликвидность/риск-аппетит\n"
            f"— если движение продолжится, стоит оценить риск-менеджмент\n\n"
            f"#рынки #инвестиции"
        )
        await bot.send_message(CHANNEL_ID, text)
        posted_market += 1
        if posted_market >= 1:
            break

    # Далее — твоя логика новостей (RSS → topics → GPT → пост)
    items = fetch_rss_items(limit=25)

    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    max_posts = int(os.getenv("MAX_POSTS_PER_RUN", "2"))
    max_sources = int(os.getenv("MAX_SOURCES_PER_TOPIC", "3"))
    min_sim = float(os.getenv("MIN_TITLE_SIMILARITY", "0.55"))

    topics = group_into_topics(items, min_sim=min_sim, max_sources_per_topic=max_sources)

    min_score = int(os.getenv("MIN_TOPIC_SCORE", "6"))
    topics = filter_topics(topics, min_score=min_score)

    posted_now = 0
    for topic in topics:
        topic_id = make_topic_id(topic)

        if not is_new(topic_id):
            continue

        try:
            text = await generate_ru_post(topic, model=model)
            if not text:
                continue

            await bot.send_message(CHANNEL_ID, text)
            mark_posted(topic_id)
            posted_now += 1
            print(f"✅ Posted topic: {topic_id}")

            if posted_now >= max_posts:
                break

        except Exception as e:
            print(f"❌ Failed to post topic {topic_id}: {e}")

async def post_market_alerts(bot):
    alerts = check_market_alerts()
    if not alerts:
        return

    for _, text in alerts.items():
        try:
            await bot.send_message(CHANNEL_ID, text)
        except Exception as e:
            print(f"⚠️ Market alert send error: {e}")


async def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        raise RuntimeError("Нет BOT_TOKEN или CHANNEL_ID в .env")

    bot = Bot(token=BOT_TOKEN)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        job_post_news,
        "interval",
        hours=4,
        args=[bot],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    # --- Market monitor (ticks + alerts) ---
    scheduler.add_job(update_market_ticks, "interval", minutes=5)

    scheduler.add_job(
        post_market_alerts,
        "interval",
        minutes=5,
        args=[bot],
        max_instances=1,
        coalesce=True
    )

    scheduler.start()

    print("✅ Autoposter started and waiting (scheduler mode)...")

    # ВАЖНО: держим процесс живым всегда
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())

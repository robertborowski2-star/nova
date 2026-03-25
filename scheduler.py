"""
scheduler.py — Nova's scheduled report engine
===============================================
Runs the weekly/monthly/quarterly reports automatically.
Uses APScheduler — a lightweight Python scheduler, no cron needed.

Lesson: APScheduler runs inside your Python process, so the scheduler
and the Telegram bot run concurrently via asyncio. No separate cron job
or systemd timer needed — one process does everything.
"""

import logging
import os
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from agent import Agent
from memory import Memory
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("nova.scheduler")

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# Shared instances (same memory as the bot uses)
memory = Memory()
agent  = Agent(memory)


async def send_scheduled_report(mode: str, label: str):
    """
    Run a scheduled portfolio report and send it to Telegram.
    Called automatically by APScheduler on the defined schedule.
    """
    log.info(f"Running scheduled {label}")
    try:
        response = await agent.chat(f"{mode} report")

        bot = Bot(token=TELEGRAM_TOKEN)
        header = f"⏰ *Scheduled: {label}*\n\n"

        full_message = header + response
        if len(full_message) <= 4096:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=full_message,
                parse_mode="Markdown"
            )
        else:
            # Split long reports
            chunks = [full_message[i:i+4000]
                      for i in range(0, len(full_message), 4000)]
            for i, chunk in enumerate(chunks):
                prefix = f"*({i+1}/{len(chunks)})*\n\n" if len(chunks) > 1 else ""
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=prefix + chunk,
                    parse_mode="Markdown"
                )
        log.info(f"Scheduled {label} sent successfully")

    except Exception as e:
        log.error(f"Scheduled {label} failed: {e}")
        try:
            bot = Bot(token=TELEGRAM_TOKEN)
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"⚠️ *Nova Scheduler*\n{label} failed: `{str(e)}`",
                parse_mode="Markdown"
            )
        except Exception:
            pass


async def start_scheduler():
    """
    Configure and start APScheduler with all report schedules.

    Schedule summary:
        Weekly pulse     — Every Monday at 8:00am
        Monthly review   — 1st of every month at 8:00am
        Quarterly review — 1st of Jan, Apr, Jul, Oct at 8:00am

    Lesson: CronTrigger uses the same syntax as Unix cron.
    "0 8 * * mon" means: minute=0, hour=8, any day, any month, Monday.
    """
    scheduler = AsyncIOScheduler()

    # Weekly pulse — every Monday 8am
    scheduler.add_job(
        send_scheduled_report,
        CronTrigger(day_of_week="mon", hour=8, minute=0),
        args=["weekly", "Weekly Portfolio Pulse"],
        id="weekly_pulse",
        name="Weekly Portfolio Pulse",
        misfire_grace_time=3600,  # run even if Pi was offline, up to 1hr late
    )

    # Monthly review — 1st of month 8am
    scheduler.add_job(
        send_scheduled_report,
        CronTrigger(day=1, hour=8, minute=0),
        args=["monthly", "Monthly Portfolio Review"],
        id="monthly_review",
        name="Monthly Portfolio Review",
        misfire_grace_time=3600,
    )

    # Quarterly review — Jan/Apr/Jul/Oct 1st 8am
    scheduler.add_job(
        send_scheduled_report,
        CronTrigger(month="1,4,7,10", day=1, hour=8, minute=0),
        args=["quarterly", "Quarterly Portfolio Review"],
        id="quarterly_review",
        name="Quarterly Portfolio Review",
        misfire_grace_time=3600,
    )

    scheduler.start()
    log.info("Scheduler started — weekly/monthly/quarterly reports active")

    # Log next run times so you can verify
    for job in scheduler.get_jobs():
        log.info(f"  {job.name}: next run {job.next_run_time}")

    # Keep the scheduler alive — asyncio will run it concurrently with the bot
    import asyncio
    while True:
        await asyncio.sleep(60)

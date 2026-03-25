"""
Nova — Personal AI Agent
========================
A self-hosted AI agent with persistent memory, CFA-grade portfolio research,
scheduled reports, and Telegram interface. Runs on Raspberry Pi.

Author: Robert Borowski
License: MIT

Usage:
    python main.py          # Start the agent (Telegram bot + scheduler)
    python main.py --test   # Test a single query without Telegram
"""

import asyncio
import argparse
import logging
from scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("nova.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("nova")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nova AI Agent")
    parser.add_argument("--test", type=str, help="Test a query directly")
    args = parser.parse_args()

    if args.test:
        from agent import Agent
        from memory import Memory

        async def test():
            memory = Memory()
            agent = Agent(memory)
            response = await agent.chat(args.test)
            print("\n" + "="*60)
            print(response)
            print("="*60)

        asyncio.run(test())

    else:
        # Run scheduler in background thread
        import threading
        scheduler_thread = threading.Thread(
            target=lambda: asyncio.run(start_scheduler()),
            daemon=True
        )
        scheduler_thread.start()

        # Let python-telegram-bot manage its own event loop
        log.info("🌟 Nova is starting...")
        from telegram_bot import start_bot_sync
        start_bot_sync()
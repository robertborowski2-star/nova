"""
telegram_bot.py — Nova's Telegram interface
============================================
Listens for messages on your Telegram bot and routes them to the agent.
Uses python-telegram-bot (async version).

Setup:
    1. Message @BotFather on Telegram → /newbot → get your token
    2. Message @userinfobot to get your chat_id
    3. Add both to .env file

Security: ALLOWED_CHAT_ID whitelist means only YOU can talk to Nova.
"""

import logging
import os
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from agent import Agent
from memory import Memory
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("nova.telegram")

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
ALLOWED_CHAT_ID  = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# Shared instances — one memory and agent for all interactions
memory = Memory()
agent  = Agent(memory)


def is_authorised(update: Update) -> bool:
    """Only respond to messages from your own Telegram account."""
    return update.effective_chat.id == ALLOWED_CHAT_ID


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route all text messages to Nova's agent loop."""
    if not is_authorised(update):
        log.warning(f"Unauthorised access attempt from {update.effective_chat.id}")
        return

    user_message = update.message.text
    log.info(f"Received: {user_message[:80]}")

    # Show typing indicator while Nova thinks
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    response = await agent.chat(user_message)

    # Telegram has a 4096 char limit per message — split if needed
    if len(response) <= 4096:
        await update.message.reply_text(response, parse_mode="Markdown")
    else:
        # Split on double newline to avoid cutting mid-sentence
        chunks = split_message(response, limit=4000)
        for i, chunk in enumerate(chunks):
            prefix = f"*({i+1}/{len(chunks)})*\n\n" if len(chunks) > 1 else ""
            await update.message.reply_text(
                prefix + chunk, parse_mode="Markdown"
            )


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not is_authorised(update):
        return
    history_count = memory.get_history_count()
    facts_count   = len(memory.get_facts())
    await update.message.reply_text(
        f"*Nova is online* 🌟\n\n"
        f"Memory: {history_count} messages, {facts_count} facts stored\n\n"
        f"Try:\n"
        f"• `weekly brief`\n"
        f"• `research EQB`\n"
        f"• `quarterly review`\n"
        f"• Any question — I'll search the web",
        parse_mode="Markdown"
    )


async def handle_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /memory command — show what Nova knows about you."""
    if not is_authorised(update):
        return
    facts = memory.format_facts_for_prompt()
    await update.message.reply_text(
        f"*What Nova remembers about you:*\n\n`{facts}`",
        parse_mode="Markdown"
    )


async def handle_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /forget command — clear a specific fact."""
    if not is_authorised(update):
        return
    # Usage: /forget portfolio risk_tolerance
    args = context.args
    if len(args) >= 2:
        category, key = args[0], args[1]
        memory.conn.execute(
            "DELETE FROM facts WHERE category = ? AND key = ?",
            (category, key)
        )
        memory.conn.commit()
        await update.message.reply_text(f"Forgotten: [{category}] {key}")
    else:
        await update.message.reply_text(
            "Usage: `/forget category key`\nExample: `/forget portfolio risk_tolerance`",
            parse_mode="Markdown"
        )


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Split a long message into chunks without cutting mid-paragraph."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Find last double-newline before limit
        split_at = text.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return chunks


async def start_bot():
    """Build and start the Telegram bot."""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN not set in .env")
    if not ALLOWED_CHAT_ID:
        raise ValueError("TELEGRAM_CHAT_ID not set in .env")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start",  handle_start))
    app.add_handler(CommandHandler("memory", handle_memory))
    app.add_handler(CommandHandler("forget", handle_forget))

    # All other text messages → agent
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Telegram bot started — waiting for messages")
    await app.run_polling(allowed_updates=Update.ALL_TYPES)



def start_bot_sync():
    """Entry point that lets PTB manage its own event loop."""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN not set in .env")
    if not ALLOWED_CHAT_ID:
        raise ValueError("TELEGRAM_CHAT_ID not set in .env")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("memory", handle_memory))
    app.add_handler(CommandHandler("forget", handle_forget))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Telegram bot started — waiting for messages")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

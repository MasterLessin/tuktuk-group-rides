# tuktuk_bot.py
import os
import sys
import logging
import asyncio

from telegram.ext import Application

from db import AsyncDB
import handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("tuktuk_main")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID_ENV = os.environ.get("ADMIN_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN missing. Set it as an env var.")
    sys.exit(1)
if not ADMIN_ID_ENV:
    logger.error("ADMIN_ID missing. Set it as an env var.")
    sys.exit(1)
if not DATABASE_URL:
    logger.error("DATABASE_URL missing. Set it as an env var.")
    sys.exit(1)

try:
    ADMIN_ID = int(ADMIN_ID_ENV)
except Exception as e:
    logger.error("ADMIN_ID must be numeric. Error: %s", e)
    sys.exit(1)


async def async_main():
    # init DB
    db = AsyncDB(DATABASE_URL)
    try:
        await db.init()
    except Exception as e:
        logger.exception("DB init failed: %s", e)
        raise

    # build bot app
    app = Application.builder().token(TOKEN).build()

    # register handlers & give them db + admin via bot_data
    handlers.register_handlers(app, db, ADMIN_ID)

    logger.info("Bot starting (polling)...")
    try:
        await app.run_polling()
    finally:
        logger.info("Shutting down application...")
        await db.close()
        await app.shutdown()


if __name__ == "__main__":
    # Use get_event_loop.run_until_complete to be safe on hosts that already manage loops
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(async_main())
    except Exception as e:
        logger.exception("Fatal startup error: %s", e)
        raise

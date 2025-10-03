# tuktuk_main.py
"""
App startup: checks env, initializes DB, registers handlers, runs polling.
"""
import os
import sys
import logging
from telegram.ext import Application
from tuktuk_db import AsyncDB
import handlers

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("tuktuk_main")


async def async_main():
    # read environment variables
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    ADMIN_ID_ENV = os.environ.get("ADMIN_ID")
    DATABASE_URL = os.environ.get("DATABASE_URL")

    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        sys.exit(1)
    if not ADMIN_ID_ENV:
        logger.error("ADMIN_ID is not set. Exiting.")
        sys.exit(1)
    if not DATABASE_URL:
        logger.error("DATABASE_URL is not set. Exiting.")
        sys.exit(1)

    try:
        ADMIN_ID = int(ADMIN_ID_ENV)
    except Exception as e:
        logger.error("ADMIN_ID must be a numeric Telegram user id. Error: %s", e)
        sys.exit(1)

    # init DB
    db = AsyncDB(DATABASE_URL)
    try:
        await db.init()
    except Exception as e:
        logger.exception("Failed to initialize DB: %s", e)
        sys.exit(1)

    # build application
    app = Application.builder().token(TOKEN).build()

    # register handlers and attach db + admin to app.bot_data
    handlers.register_handlers(app, db, ADMIN_ID)

    logger.info("Bot starting (polling)...")
    try:
        # run polling until killed
        await app.run_polling()
    finally:
        logger.info("Shutting down...")
        try:
            await db.close()
        except Exception as e:
            logger.exception("Error closing DB: %s", e)
        try:
            # ensure Application shutdown (clean)
            await app.shutdown()
        except Exception:
            pass



#!/usr/bin/env python3
import os, sys, logging, asyncio
import nest_asyncio
nest_asyncio.apply()

from telegram.ext import Application

from bot.db import AsyncDB
from bot.handlers import register_handlers

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger('tuktuk_main')

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_ID_ENV = os.environ.get('ADMIN_ID')
DATABASE_URL = os.environ.get('DATABASE_URL')

if not TOKEN:
    logger.error('TELEGRAM_BOT_TOKEN is not set in environment. Exiting.')
    sys.exit(1)
if not ADMIN_ID_ENV:
    logger.error('ADMIN_ID is not set in environment. Exiting.')
    sys.exit(1)
if not DATABASE_URL:
    logger.error('DATABASE_URL is not set in environment. Exiting.')
    sys.exit(1)

try:
    ADMIN_ID = int(ADMIN_ID_ENV)
except Exception as e:
    logger.error('ADMIN_ID must be a numeric Telegram user id. Error: %s', e)
    sys.exit(1)

async def main():
    logger.info('Starting DB...')
    db = AsyncDB(DATABASE_URL)
    await db.init()

    logger.info('Building Telegram application...')
    app = Application.builder().token(TOKEN).build()

    register_handlers(app, db, ADMIN_ID)

    logger.info('Bot started (polling).')
    try:
        await app.run_polling()
    finally:
        logger.info('Shutting down...')
        await db.close()
        await app.shutdown()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info('Exit requested, shutting down.')
    except Exception:
        logger.exception('Fatal error while starting bot.')
        raise

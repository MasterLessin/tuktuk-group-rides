import os
import nest_asyncio
from telegram.ext import Application
from bot import handlers

# Fix event loop for Railway
nest_asyncio.apply()

async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set!")

    application = Application.builder().token(token).build()

    # Register all handlers
    handlers.register_handlers(application)

    print("Tuktuk Bot is running...")
    await application.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

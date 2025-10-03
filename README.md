# TukTuk Group Rides Bot (Modular)

This is a complete modular Telegram bot for TukTuk group rides.
It includes:
- driver & rider registration
- ride request flow (pickup via Telegram location, optional drop text/location)
- posting requests to a dispatch group (admin-set)
- drivers accepting rides (atomic assign)
- ride history with pagination (My Rides)
- Postgres (asyncpg) backend with auto-table creation
- modular structure for maintainability

## Required environment variables (Railway)
- TELEGRAM_BOT_TOKEN  (your bot token)
- ADMIN_ID            (numeric Telegram ID of admin)
- DATABASE_URL        (Postgres connection string)

## Deploy (Railway)
1. Push this repo to GitHub.
2. Create a Railway project, connect the repo, or upload directly.
3. Set the env vars above in Railway.
4. Deploy. Railway runs `Procfile` -> `python tuktuk_main.py`.

## Local testing
- Install dependencies: `pip install -r requirements.txt`
- Set env vars locally (or use a .env file)
- Run: `python tuktuk_main.py`

## Notes
- Admin must run `/set_dispatch_group` in the driver group (bot must be added to that group).
- If you see `event loop` errors, nest_asyncio is included and applied.

# db.py
import asyncpg
import logging

logger = logging.getLogger("db")

class AsyncDB:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool = None

    async def init(self):
        logger.info("Creating asyncpg pool...")
        self.pool = await asyncpg.create_pool(self.dsn)
        await self._create_tables()
        logger.info("Database ready.")

    async def _create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS rides (
                    id SERIAL PRIMARY KEY,
                    rider_id BIGINT NOT NULL,
                    driver_id BIGINT,
                    start_location TEXT NOT NULL,
                    end_location TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'requested',
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    role TEXT NOT NULL, -- rider or driver
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

    async def add_user(self, user_id: int, role: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (user_id, role)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO NOTHING;
            """, user_id, role)

    async def create_ride(self, rider_id: int, start: str, end: str):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                INSERT INTO rides (rider_id, start_location, end_location, status)
                VALUES ($1, $2, $3, 'requested')
                RETURNING *;
            """, rider_id, start, end)

    async def assign_driver(self, ride_id: int, driver_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                UPDATE rides
                SET driver_id = $2, status = 'accepted'
                WHERE id = $1
                RETURNING *;
            """, ride_id, driver_id)

    async def update_ride_status(self, ride_id: int, status: str):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                UPDATE rides
                SET status = $2
                WHERE id = $1
                RETURNING *;
            """, ride_id, status)

    async def get_ride_history(self, user_id: int, role: str, limit: int, offset: int):
        async with self.pool.acquire() as conn:
            if role == "rider":
                return await conn.fetch("""
                    SELECT * FROM rides WHERE rider_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3;
                """, user_id, limit, offset)
            else:
                return await conn.fetch("""
                    SELECT * FROM rides WHERE driver_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3;
                """, user_id, limit, offset)

    async def count_ride_history(self, user_id: int, role: str):
        async with self.pool.acquire() as conn:
            if role == "rider":
                row = await conn.fetchrow("""
                    SELECT COUNT(*) as total FROM rides WHERE rider_id = $1;
                """, user_id)
            else:
                row = await conn.fetchrow("""
                    SELECT COUNT(*) as total FROM rides WHERE driver_id = $1;
                """, user_id)
            return row["total"]

    async def close(self):
        if self.pool:
            await self.pool.close()

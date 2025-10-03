# tuktuk_db.py
"""
Async Postgres wrapper — AsyncDB using asyncpg.
Auto-creates tables on init.
"""

import asyncpg
import logging
import time
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger("tuktuk_db")


class AsyncDB:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[asyncpg.pool.Pool] = None

    async def init(self):
        logger.info("Creating asyncpg pool...")
        # create pool (adjust min/max depending on hosting)
        self.pool = await asyncpg.create_pool(dsn=self.database_url, min_size=1, max_size=5)
        logger.info("Ensuring tables exist...")
        await self._create_tables()
        logger.info("DB initialized.")

    async def close(self):
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("DB pool closed.")

    async def _create_tables(self):
        create_drivers = """
        CREATE TABLE IF NOT EXISTS drivers (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE,
            name TEXT,
            phone TEXT,
            reg_no TEXT,
            status TEXT,
            lat DOUBLE PRECISION,
            lng DOUBLE PRECISION,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
        create_rides = """
        CREATE TABLE IF NOT EXISTS rides (
            id SERIAL PRIMARY KEY,
            rider_tg_id BIGINT,
            pickup_lat DOUBLE PRECISION,
            pickup_lng DOUBLE PRECISION,
            drop_lat DOUBLE PRECISION,
            drop_lng DOUBLE PRECISION,
            drop_text TEXT,
            group_size INTEGER,
            status TEXT,
            assigned_driver_id INTEGER REFERENCES drivers(id),
            created_at BIGINT
        );
        """
        create_settings = """
        CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT
        );
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(create_drivers)
                await conn.execute(create_rides)
                await conn.execute(create_settings)

    # settings helpers
    async def set_setting(self, k: str, v: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO settings (k, v) VALUES ($1, $2)
                ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v;
                """,
                k, v
            )

    async def get_setting(self, k: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT v FROM settings WHERE k=$1;", k)
            return row["v"] if row else None

    # driver helpers
    async def add_or_update_driver(self, tg_id: int, name: Optional[str] = None, phone: Optional[str] = None, reg_no: Optional[str] = None) -> int:
        """
        Insert or update driver. Returns driver.id
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO drivers (telegram_id, name, phone, reg_no, status)
                    VALUES ($1,$2,$3,$4,'offline')
                    ON CONFLICT (telegram_id) DO UPDATE
                      SET name = COALESCE(EXCLUDED.name, drivers.name),
                          phone = COALESCE(EXCLUDED.phone, drivers.phone),
                          reg_no = COALESCE(EXCLUDED.reg_no, drivers.reg_no);
                    """,
                    tg_id, name, phone, reg_no
                )
                row = await conn.fetchrow("SELECT * FROM drivers WHERE telegram_id=$1;", tg_id)
                return int(row["id"])

    async def set_driver_status(self, tg_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE drivers SET status=$1 WHERE telegram_id=$2;", status, tg_id)

    async def update_driver_location(self, tg_id: int, lat: float, lng: float):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE drivers SET lat=$1, lng=$2 WHERE telegram_id=$3;", lat, lng, tg_id)

    async def get_driver_by_tg(self, tg_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM drivers WHERE telegram_id=$1;", tg_id)
            return dict(row) if row else None

    async def get_driver_by_id(self, driver_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM drivers WHERE id=$1;", driver_id)
            return dict(row) if row else None

    async def get_online_drivers(self) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM drivers WHERE status='online';")
            return [dict(r) for r in rows]

    # rides helpers
    async def create_ride(self, rider_tg_id: int, pickup_lat: float, pickup_lng: float,
                          drop_lat: Optional[float], drop_lng: Optional[float],
                          drop_text: Optional[str], group_size: int) -> int:
        ts = int(time.time())
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO rides (rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,'searching',$8) RETURNING id;
                """,
                rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, ts
            )
            return int(row["id"])

    async def get_ride(self, ride_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM rides WHERE id=$1;", ride_id)
            return dict(row) if row else None

    async def assign_ride_if_unassigned(self, ride_id: int, driver_id: int) -> bool:
        """
        Atomically assign ride only if unassigned. Returns True if assigned.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE rides SET assigned_driver_id=$1, status='assigned'
                WHERE id=$2 AND assigned_driver_id IS NULL
                RETURNING id;
                """,
                driver_id, ride_id
            )
            return bool(row)

    async def set_ride_status(self, ride_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE rides SET status=$1 WHERE id=$2;", status, ride_id)

    async def get_rides_by_rider(self, rider_tg_id: int, offset: int = 0, limit: int = 5) -> Tuple[List[Dict[str, Any]], int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, assigned_driver_id, created_at
                FROM rides
                WHERE rider_tg_id=$1
                ORDER BY created_at DESC
                OFFSET $2 LIMIT $3;
                """,
                rider_tg_id, offset, limit
            )
            total = await conn.fetchval("SELECT COUNT(1) FROM rides WHERE rider_tg_id=$1;", rider_tg_id)
            return [dict(r) for r in rows], int(total or 0)

    async def get_rides_by_driver(self, driver_id: int, offset: int = 0, limit: int = 5) -> Tuple[List[Dict[str, Any]], int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, created_at
                FROM rides
                WHERE assigned_driver_id=$1
                ORDER BY created_at DESC
                OFFSET $2 LIMIT $3;
                """,
                driver_id, offset, limit
            )
            total = await conn.fetchval("SELECT COUNT(1) FROM rides WHERE assigned_driver_id=$1;", driver_id)
            return [dict(r) for r in rows], int(total or 0)

    async def get_recent_searching_rides(self, limit: int = 10) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM rides WHERE status='searching' ORDER BY created_at ASC LIMIT $1;", limit)
            return [dict(r) for r in rows]

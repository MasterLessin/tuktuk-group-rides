import asyncpg, logging, time
from typing import Optional, List, Dict, Any

logger = logging.getLogger('tuktuk_db')

class AsyncDB:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def init(self):
        logger.info('Creating asyncpg pool...')
        self.pool = await asyncpg.create_pool(dsn=self.dsn, min_size=1, max_size=5)
        logger.info('Pool created. Ensuring tables exist...')
        await self._create_tables()
        logger.info('DB initialized.')

    async def close(self):
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info('DB pool closed.')

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
            created_at BIGINT DEFAULT (extract(epoch from now())::bigint)
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

    # settings
    async def set_setting(self, k: str, v: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO settings (k, v) VALUES ($1, $2)
                ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v;
            """, k, v)

    async def get_setting(self, k: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT v FROM settings WHERE k=$1;', k)
            return row['v'] if row else None

    # drivers
    async def add_or_update_driver(self, tg_id: int, name: Optional[str]=None, phone: Optional[str]=None, reg_no: Optional[str]=None):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO drivers (telegram_id, name, phone, reg_no, status)
                VALUES ($1, $2, $3, $4, 'offline')
                ON CONFLICT (telegram_id) DO UPDATE
                  SET name = COALESCE(EXCLUDED.name, drivers.name),
                      phone = COALESCE(EXCLUDED.phone, drivers.phone),
                      reg_no = COALESCE(EXCLUDED.reg_no, drivers.reg_no);
            """, tg_id, name, phone, reg_no)

    async def set_driver_status(self, tg_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE drivers SET status=$1 WHERE telegram_id=$2;', status, tg_id)

    async def update_driver_location(self, tg_id: int, lat: float, lng: float):
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE drivers SET lat=$1, lng=$2 WHERE telegram_id=$3;', lat, lng, tg_id)

    async def get_driver_by_tg(self, tg_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT id, telegram_id, name, phone, reg_no, status, lat, lng FROM drivers WHERE telegram_id=$1;', tg_id)
            return dict(row) if row else None

    async def get_driver_by_id(self, driver_id: int) -> Optional[Dict[str, Any]]:
        if not driver_id:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT id, telegram_id, name, phone, reg_no, status FROM drivers WHERE id=$1;', driver_id)
            return dict(row) if row else None

    async def get_online_drivers(self) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, telegram_id, name, phone, reg_no, status, lat, lng FROM drivers WHERE status='online';")
            return [dict(r) for r in rows]

    # rides
    async def create_ride(self, rider_tg_id:int, pickup_lat:float, pickup_lng:float,
                          drop_lat:Optional[float], drop_lng:Optional[float],
                          drop_text:Optional[str], group_size:int) -> int:
        ts = int(time.time())
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO rides (rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,'searching',$8) RETURNING id;
            """, rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, ts)
            return int(row['id'])

    async def get_ride(self, ride_id:int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM rides WHERE id=$1;', ride_id)
            return dict(row) if row else None

    async def get_rides_by_rider(self, rider_tg_id:int, limit:int=20, offset:int=0) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, assigned_driver_id, created_at
                FROM rides WHERE rider_tg_id=$1 ORDER BY created_at DESC LIMIT $2 OFFSET $3;
            """, rider_tg_id, limit, offset)
            return [dict(r) for r in rows]

    async def count_rides_by_rider(self, rider_tg_id:int) -> int:
        async with self.pool.acquire() as conn:
            val = await conn.fetchval('SELECT COUNT(*) FROM rides WHERE rider_tg_id=$1;', rider_tg_id)
            return int(val or 0)

    async def assign_ride_if_unassigned(self, ride_id:int, driver_id:int) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute('''
                UPDATE rides SET assigned_driver_id=$1, status='assigned'
                WHERE id=$2 AND assigned_driver_id IS NULL;
            ''', driver_id, ride_id)
            try:
                n = int(res.split()[-1])
                return n > 0
            except Exception:
                return False

    async def set_ride_status(self, ride_id:int, status:str):
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE rides SET status=$1 WHERE id=$2;', status, ride_id)

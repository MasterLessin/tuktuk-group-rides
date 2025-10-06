import asyncpg, logging, time
from typing import Optional, List, Dict, Any
import math

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
            rating DECIMAL DEFAULT 5.0,
            total_ratings INTEGER DEFAULT 0,
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
            fare_estimate DECIMAL,
            final_fare DECIMAL,
            estimated_pickup_time INTEGER,
            estimated_trip_time INTEGER,
            assigned_driver_id INTEGER REFERENCES drivers(id),
            created_at BIGINT,
            cancelled_at BIGINT,
            cancelled_by TEXT
        );
        """
        create_ratings = """
        CREATE TABLE IF NOT EXISTS ratings (
            id SERIAL PRIMARY KEY,
            ride_id INTEGER REFERENCES rides(id),
            driver_id INTEGER REFERENCES drivers(id),
            rider_tg_id BIGINT,
            rating INTEGER,
            comment TEXT,
            created_at BIGINT
        );
        """
        create_emergency_contacts = """
        CREATE TABLE IF NOT EXISTS emergency_contacts (
            id SERIAL PRIMARY KEY,
            user_tg_id BIGINT,
            contact_name TEXT,
            contact_phone TEXT,
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
                await conn.execute(create_ratings)
                await conn.execute(create_emergency_contacts)
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
            row = await conn.fetchrow('SELECT id, telegram_id, name, phone, reg_no, status, lat, lng, rating, total_ratings FROM drivers WHERE telegram_id=$1;', tg_id)
            return dict(row) if row else None

    async def get_driver_by_id(self, driver_id: int) -> Optional[Dict[str, Any]]:
        if not driver_id:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT id, telegram_id, name, phone, reg_no, status, rating, total_ratings FROM drivers WHERE id=$1;', driver_id)
            return dict(row) if row else None

    async def get_online_drivers(self) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, telegram_id, name, phone, reg_no, status, lat, lng, rating FROM drivers WHERE status='online';")
            return [dict(r) for r in rows]

    async def update_driver_rating(self, driver_id: int, new_rating: int):
        async with self.pool.acquire() as conn:
            driver = await self.get_driver_by_id(driver_id)
            if driver:
                current_rating = driver.get('rating', 5.0)
                total_ratings = driver.get('total_ratings', 0)
                
                # Calculate new average
                new_avg = ((current_rating * total_ratings) + new_rating) / (total_ratings + 1)
                
                await conn.execute(
                    'UPDATE drivers SET rating=$1, total_ratings=total_ratings+1 WHERE id=$2;',
                    new_avg, driver_id
                )

    # rides
    async def create_ride(self, rider_tg_id:int, pickup_lat:float, pickup_lng:float,
                          drop_lat:Optional[float], drop_lng:Optional[float],
                          drop_text:Optional[str], group_size:int, 
                          fare_estimate:float=0, estimated_pickup_time:int=0, 
                          estimated_trip_time:int=0) -> int:
        ts = int(time.time())
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO rides (rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, fare_estimate, estimated_pickup_time, estimated_trip_time, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,'searching',$8,$9,$10,$11) RETURNING id;
            """, rider_tg_id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, fare_estimate, estimated_pickup_time, estimated_trip_time, ts)
            return int(row['id'])

    async def get_ride(self, ride_id:int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM rides WHERE id=$1;', ride_id)
            return dict(row) if row else None

    async def get_rides_by_rider(self, rider_tg_id:int, limit:int=20, offset:int=0) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, pickup_lat, pickup_lng, drop_lat, drop_lng, drop_text, group_size, status, assigned_driver_id, fare_estimate, final_fare, created_at
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

    async def cancel_ride(self, ride_id:int, cancelled_by:str):
        ts = int(time.time())
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE rides SET status='cancelled', cancelled_at=$1, cancelled_by=$2 
                WHERE id=$3;
            ''', ts, cancelled_by, ride_id)

    async def update_ride_fare(self, ride_id:int, final_fare:float):
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE rides SET final_fare=$1 WHERE id=$2;', final_fare, ride_id)

    # ratings
    async def add_rating(self, ride_id:int, driver_id:int, rider_tg_id:int, rating:int, comment:str=""):
        ts = int(time.time())
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO ratings (ride_id, driver_id, rider_tg_id, rating, comment, created_at)
                VALUES ($1, $2, $3, $4, $5, $6);
            ''', ride_id, driver_id, rider_tg_id, rating, comment, ts)

    # emergency contacts
    async def add_emergency_contact(self, user_tg_id:int, contact_name:str, contact_phone:str):
        ts = int(time.time())
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO emergency_contacts (user_tg_id, contact_name, contact_phone, created_at)
                VALUES ($1, $2, $3, $4);
            ''', user_tg_id, contact_name, contact_phone, ts)

    async def get_emergency_contacts(self, user_tg_id:int) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT id, contact_name, contact_phone FROM emergency_contacts WHERE user_tg_id=$1;', user_tg_id)
            return [dict(r) for r in rows]

    # Helper function for distance calculation
    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        # Haversine formula to calculate distance in km
        R = 6371  # Earth radius in km
        
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        
        a = (math.sin(dlat/2) * math.sin(dlat/2) + 
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
             math.sin(dlon/2) * math.sin(dlon/2))
        
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R * c
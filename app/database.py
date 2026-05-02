import aiosqlite
import os
from pathlib import Path

_default_db = "/data/dropsite.db" if Path("/data").exists() else str(Path(__file__).parent.parent / "dropsite.db")
DB_PATH = os.getenv("DROPSITE_DB", _default_db)


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                plan TEXT DEFAULT 'free',
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                notification_email INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                original_filename TEXT NOT NULL,
                site_dir TEXT NOT NULL,
                password_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                is_active INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                visited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_address TEXT,
                user_agent TEXT,
                referer TEXT,
                FOREIGN KEY (site_id) REFERENCES sites(id)
            );

            CREATE INDEX IF NOT EXISTS idx_sites_slug ON sites(slug);
            CREATE INDEX IF NOT EXISTS idx_analytics_site_id ON analytics(site_id);
            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        """)
        await db.commit()
    finally:
        await db.close()


async def migrate_db() -> None:
    """Add columns that may not exist in older databases."""
    db = await get_db()
    try:
        cursor = await db.execute("PRAGMA table_info(sites)")
        columns = {row["name"] for row in await cursor.fetchall()}
        if "user_id" not in columns:
            await db.execute("ALTER TABLE sites ADD COLUMN user_id INTEGER REFERENCES users(id)")
            await db.commit()

        # Create index after column exists
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sites_user_id ON sites(user_id)")
        await db.commit()
    finally:
        await db.close()

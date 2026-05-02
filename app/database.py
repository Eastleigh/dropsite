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
        """)
        await db.commit()
    finally:
        await db.close()

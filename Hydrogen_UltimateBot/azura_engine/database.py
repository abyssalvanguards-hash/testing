"""Isolated persistence layer for the Azura slot engine.

This database is deliberately separate from WardenX's core database. It preserves
Azura's original table names and data model without colliding with WardenX tables.
"""
from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

logger = logging.getLogger("WardenX.AzuraDatabase")
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("AZURA_SLOT_DB_PATH", str(DATA_DIR / "azura_slots.db")))
BACKUP_DIR = DATA_DIR / "azura_backups"


@asynccontextmanager
async def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        yield conn


async def init_db() -> None:
    """Create Azura's tables and apply safe additive migrations."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    async with get_connection() as conn:
        await conn.executescript("""
        CREATE TABLE IF NOT EXISTS slots (
            channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL, custom_name TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'paid', created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL, ping_limit INTEGER NOT NULL DEFAULT 2,
            pings_used INTEGER NOT NULL DEFAULT 0, last_ping_date TEXT,
            on_hold INTEGER NOT NULL DEFAULT 0, is_frozen INTEGER NOT NULL DEFAULT 0,
            dashboard_message_id INTEGER, banner_message_id INTEGER,
            reminded_3d INTEGER NOT NULL DEFAULT 0,
            reminded_24h INTEGER NOT NULL DEFAULT 0,
            reminded_2h INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS slot_staff (
            channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            added_at INTEGER NOT NULL, PRIMARY KEY(channel_id,user_id),
            FOREIGN KEY(channel_id) REFERENCES slots(channel_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS recovery_keys (
            key TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            custom_name TEXT NOT NULL, tier TEXT NOT NULL, duration_days INTEGER NOT NULL,
            ping_limit INTEGER NOT NULL, remaining_seconds INTEGER NOT NULL,
            created_at INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS free_slot_claims (
            user_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, claimed_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets (
            channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, tier TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', created_at INTEGER NOT NULL,
            ticket_type TEXT DEFAULT 'slot', seller_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            action TEXT NOT NULL, user_id INTEGER NOT NULL, channel_id INTEGER,
            details TEXT, timestamp INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS role_codes (
            code TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, role_id INTEGER NOT NULL,
            creator_id INTEGER NOT NULL, role_name TEXT, created_at INTEGER NOT NULL,
            claimed_by INTEGER, claimed_at INTEGER, is_active INTEGER NOT NULL DEFAULT 1
        );
        """)
        # Add columns used by newer Azura builds without discarding existing data.
        columns = {r[1] for r in await (await conn.execute("PRAGMA table_info(slots)")).fetchall()}
        for definition in (
            "server_name TEXT", "link TEXT", "rules_url TEXT", "image_text TEXT",
            "color INTEGER", "is_hidden INTEGER DEFAULT 0",
            "vanity_warned_at INTEGER DEFAULT 0"
        ):
            name = definition.split()[0]
            if name not in columns:
                await conn.execute(f"ALTER TABLE slots ADD COLUMN {definition}")
        ticket_columns = {r[1] for r in await (await conn.execute("PRAGMA table_info(tickets)")).fetchall()}
        for definition in ("ticket_type TEXT DEFAULT 'slot'", "seller_id INTEGER"):
            name = definition.split()[0]
            if name not in ticket_columns:
                await conn.execute(f"ALTER TABLE tickets ADD COLUMN {definition}")
        await conn.commit()
    logger.info("Azura slot database initialized at %s", DB_PATH)


async def create_slot(channel_id: int, guild_id: int, owner_id: int, custom_name: str,
                      tier: str, expires_at: int, ping_limit: int,
                      dashboard_msg_id: int | None = None,
                      banner_msg_id: int | None = None) -> None:
    import time
    from datetime import datetime, timezone
    async with get_connection() as conn:
        await conn.execute(
            """INSERT INTO slots(channel_id,guild_id,owner_id,custom_name,tier,created_at,
            expires_at,ping_limit,pings_used,last_ping_date,dashboard_message_id,banner_message_id)
            VALUES(?,?,?,?,?,?,?,?,0,?,?,?)""",
            (channel_id,guild_id,owner_id,custom_name,tier.lower(),int(time.time()),
             expires_at,ping_limit,datetime.now(timezone.utc).strftime("%Y-%m-%d"),
             dashboard_msg_id,banner_msg_id)
        )
        await conn.commit()


async def get_slot(channel_id: int):
    async with get_connection() as conn:
        cur = await conn.execute("SELECT * FROM slots WHERE channel_id=?", (channel_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_all_slots(guild_id: int | None = None):
    async with get_connection() as conn:
        if guild_id is None:
            cur = await conn.execute("SELECT * FROM slots")
        else:
            cur = await conn.execute("SELECT * FROM slots WHERE guild_id=?", (guild_id,))
        return [dict(row) for row in await cur.fetchall()]

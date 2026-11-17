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


# Compatibility API used by the original Azura cogs.
import time
import shutil
from datetime import datetime, timezone

async def _fetchone(sql, params=()):
    async with get_connection() as conn:
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

async def _fetchall(sql, params=()):
    async with get_connection() as conn:
        cur = await conn.execute(sql, params)
        return [dict(row) for row in await cur.fetchall()]

async def _execute(sql, params=()):
    async with get_connection() as conn:
        cur = await conn.execute(sql, params)
        await conn.commit()
        return cur.rowcount

async def get_slots_by_owner(owner_id: int, guild_id: int | None = None):
    sql = "SELECT * FROM slots WHERE owner_id=?"
    args = [owner_id]
    if guild_id is not None:
        sql += " AND guild_id=?"; args.append(guild_id)
    return await _fetchall(sql, args)

async def search_slots(query: str, guild_id: int | None = None):
    like = f"%{query}%"
    sql = "SELECT * FROM slots WHERE (custom_name LIKE ? OR CAST(channel_id AS TEXT) LIKE ? OR CAST(owner_id AS TEXT) LIKE ?)"
    args = [like, like, like]
    if guild_id is not None:
        sql += " AND guild_id=?"; args.append(guild_id)
    return await _fetchall(sql, args)

async def update_slot(channel_id: int, **kwargs) -> None:
    allowed = {"guild_id","owner_id","custom_name","tier","expires_at","ping_limit","pings_used",
      "last_ping_date","on_hold","is_frozen","dashboard_message_id","banner_message_id",
      "reminded_3d","reminded_24h","reminded_2h","server_name","link","rules_url",
      "image_text","color","is_hidden","vanity_warned_at"}
    values = {k:v for k,v in kwargs.items() if k in allowed}
    if not values: return
    assignments = ", ".join(f"{k}=?" for k in values)
    await _execute(f"UPDATE slots SET {assignments} WHERE channel_id=?", (*values.values(), channel_id))

async def delete_slot(channel_id: int) -> None:
    await _execute("DELETE FROM slots WHERE channel_id=?", (channel_id,))

async def increment_pings(channel_id: int, today_str: str) -> int:
    async with get_connection() as conn:
        await conn.execute("UPDATE slots SET pings_used=CASE WHEN last_ping_date IS NULL OR last_ping_date<>? THEN 1 ELSE pings_used+1 END, last_ping_date=? WHERE channel_id=?", (today_str,today_str,channel_id))
        cur = await conn.execute("SELECT pings_used FROM slots WHERE channel_id=?", (channel_id,))
        row = await cur.fetchone()
        await conn.commit()
        return int(row["pings_used"]) if row else 0

async def reset_daily_pings() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return await _execute("UPDATE slots SET pings_used=0,last_ping_date=? WHERE last_ping_date IS NULL OR last_ping_date<>?", (today,today))

async def has_claimed_free_slot(user_id: int) -> bool:
    return bool(await _fetchone("SELECT 1 AS found FROM free_slot_claims WHERE user_id=?", (user_id,)))

async def record_free_slot_claim(user_id: int, channel_id: int) -> None:
    await _execute("INSERT OR REPLACE INTO free_slot_claims(user_id,channel_id,claimed_at) VALUES(?,?,?)", (user_id,channel_id,int(time.time())))

async def create_ticket(channel_id: int, guild_id: int, user_id: int, tier: str, ticket_type: str = "slot", seller_id: int = None) -> None:
    await _execute("INSERT OR REPLACE INTO tickets(channel_id,guild_id,user_id,tier,status,created_at,ticket_type,seller_id) VALUES(?,?,?,?,?,?,?,?)", (channel_id,guild_id,user_id,tier,"open",int(time.time()),ticket_type,seller_id))

async def get_ticket(channel_id: int):
    return await _fetchone("SELECT * FROM tickets WHERE channel_id=?", (channel_id,))

async def close_ticket(channel_id: int) -> None:
    await _execute("UPDATE tickets SET status='closed' WHERE channel_id=?", (channel_id,))

async def add_staff(channel_id: int, user_id: int) -> bool:
    try:
        await _execute("INSERT OR IGNORE INTO slot_staff(channel_id,user_id,added_at) VALUES(?,?,?)", (channel_id,user_id,int(time.time())))
        return True
    except Exception:
        logger.exception("Could not add slot staff")
        return False

async def remove_staff(channel_id: int, user_id: int) -> bool:
    return (await _execute("DELETE FROM slot_staff WHERE channel_id=? AND user_id=?", (channel_id,user_id))) > 0

async def get_staff(channel_id: int) -> list:
    rows = await _fetchall("SELECT user_id FROM slot_staff WHERE channel_id=? ORDER BY added_at", (channel_id,))
    return [r["user_id"] for r in rows]

async def create_recovery_key(key: str, guild_id: int, user_id: int, custom_name: str, tier: str, duration_days: int, ping_limit: int, remaining_seconds: int) -> None:
    await _execute("INSERT OR REPLACE INTO recovery_keys(key,guild_id,user_id,custom_name,tier,duration_days,ping_limit,remaining_seconds,created_at,used) VALUES(?,?,?,?,?,?,?,?,?,0)", (key,guild_id,user_id,custom_name,tier,duration_days,ping_limit,remaining_seconds,int(time.time())))

async def get_recovery_key(key: str):
    return await _fetchone("SELECT * FROM recovery_keys WHERE key=?", (key,))

async def mark_recovery_key_used(key: str) -> None:
    await _execute("UPDATE recovery_keys SET used=1 WHERE key=?", (key,))

async def log_audit(guild_id: int, action: str, user_id: int, channel_id: int = None, details: str = None) -> None:
    await _execute("INSERT INTO audit_logs(guild_id,action,user_id,channel_id,details,timestamp) VALUES(?,?,?,?,?,?)", (guild_id,action,user_id,channel_id,details,int(time.time())))

async def backup_database() -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"azura-{int(time.time())}.sqlite3"
    async with get_connection() as conn:
        await conn.execute("PRAGMA wal_checkpoint(FULL)")
    shutil.copy2(DB_PATH, target)
    return str(target)

async def set_vanity_warning(channel_id: int, timestamp: int) -> None:
    await update_slot(channel_id, vanity_warned_at=timestamp)

async def clear_vanity_warning(channel_id: int) -> None:
    await update_slot(channel_id, vanity_warned_at=0)

async def get_free_slots(guild_id: int | None = None) -> list:
    sql = "SELECT * FROM slots WHERE LOWER(tier)='free'"
    args = []
    if guild_id is not None: sql += " AND guild_id=?"; args.append(guild_id)
    return await _fetchall(sql, args)

async def create_role_code(code: str, guild_id: int, role_id: int, creator_id: int, role_name: str = None) -> None:
    await _execute("INSERT OR REPLACE INTO role_codes(code,guild_id,role_id,creator_id,role_name,created_at,is_active) VALUES(?,?,?,?,?,?,1)", (code,guild_id,role_id,creator_id,role_name,int(time.time())))

async def get_role_code(code: str) -> dict | None:
    return await _fetchone("SELECT * FROM role_codes WHERE code=?", (code,))

async def claim_role_code(code: str, claimant_id: int) -> bool:
    async with get_connection() as conn:
        cur = await conn.execute("UPDATE role_codes SET claimed_by=?,claimed_at=?,is_active=0 WHERE code=? AND is_active=1 AND claimed_by IS NULL", (claimant_id,int(time.time()),code))
        await conn.commit()
        return cur.rowcount == 1

async def get_active_role_code_by_creator(creator_id: int, guild_id: int = None) -> list:
    sql = "SELECT * FROM role_codes WHERE creator_id=? AND is_active=1"; args = [creator_id]
    if guild_id is not None: sql += " AND guild_id=?"; args.append(guild_id)
    return await _fetchall(sql, args)

async def get_role_codes_by_guild(guild_id: int) -> list:
    return await _fetchall("SELECT * FROM role_codes WHERE guild_id=? ORDER BY created_at DESC", (guild_id,))

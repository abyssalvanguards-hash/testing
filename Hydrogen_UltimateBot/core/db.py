import aiosqlite
from config import DB_PATH, DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
 guild_id INTEGER PRIMARY KEY, prefix TEXT DEFAULT '$',
 log_channel_id INTEGER, review_channel_id INTEGER,
 staff_role_id INTEGER, admin_role_id INTEGER, mod_role_id INTEGER,
 welcome_channel_id INTEGER, goodbye_channel_id INTEGER,
 ticket_transcript_channel_id INTEGER,
 excluded_category_id INTEGER,
 upi_id TEXT, upi_payee TEXT,
 ltc_address TEXT, btc_address TEXT,
 channel_template TEXT DEFAULT '{name}-hub'
);
CREATE TABLE IF NOT EXISTS bot_owners (user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS bot_admins (user_id INTEGER PRIMARY KEY, added_by INTEGER);
CREATE TABLE IF NOT EXISTS tickets (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 channel_id INTEGER UNIQUE NOT NULL, owner_id INTEGER NOT NULL,
 staff_role_id INTEGER, panel_key TEXT NOT NULL, status TEXT DEFAULT 'open',
 claimed_by INTEGER, created_at TEXT NOT NULL, closed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_ticket_per_user
 ON tickets(guild_id, owner_id) WHERE status='open';
CREATE TABLE IF NOT EXISTS ticket_panels (
 guild_id INTEGER NOT NULL, panel_key TEXT NOT NULL, title TEXT NOT NULL,
 description TEXT NOT NULL, category_id INTEGER, staff_role_id INTEGER,
 image_url TEXT, thumbnail_url TEXT,
 PRIMARY KEY(guild_id,panel_key)
);
CREATE TABLE IF NOT EXISTS applications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, username TEXT NOT NULL, answers TEXT NOT NULL,
 ai_summary TEXT, ai_recommendation TEXT, status TEXT DEFAULT 'pending',
 reviewer_id INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slots (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 channel_id INTEGER UNIQUE NOT NULL, owner_id INTEGER NOT NULL,
 ping_limit INTEGER DEFAULT 5, ping_count INTEGER DEFAULT 0,
 reset_at TEXT NOT NULL, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS xp (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 xp INTEGER DEFAULT 0, level INTEGER DEFAULT 0,
 PRIMARY KEY(guild_id,user_id)
);
CREATE TABLE IF NOT EXISTS invite_counts (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 joins INTEGER DEFAULT 0, PRIMARY KEY(guild_id,user_id)
);
CREATE TABLE IF NOT EXISTS invoices (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, method TEXT NOT NULL, amount REAL NOT NULL,
 reference TEXT, status TEXT DEFAULT 'pending', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
 actor_id INTEGER, event TEXT NOT NULL, details TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS server_change_plans (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, created_by INTEGER NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT DEFAULT 'pending', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS used_payment_txids (
 txid TEXT PRIMARY KEY, invoice_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, used_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_usage (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
 user_id INTEGER, purpose TEXT, created_at TEXT NOT NULL
);
"""

async def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # Upgrade the old built-in prefix for existing guilds.
        await db.execute("UPDATE guild_settings SET prefix=? WHERE prefix=?", ("$", "!"))
        # Safe additive migrations for databases created by earlier Hydrogen builds.
        # Move the previous built-in default prefix to the new default.
        await db.execute("UPDATE guild_settings SET prefix='
        if "txid" not in cols: await db.execute("ALTER TABLE invoices ADD COLUMN txid TEXT")
        if "confirmations" not in cols: await db.execute("ALTER TABLE invoices ADD COLUMN confirmations INTEGER DEFAULT 0")
        await db.commit()

async def execute(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        await db.commit()
        return cur

async def fetchone(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        row = await cur.fetchone()
        return dict(row) if row else None

async def fetchall(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        return [dict(r) for r in await cur.fetchall()]

async def ensure_guild(guild_id):
    await execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (guild_id,))

async def get_setting(guild_id, key, default=None):
    await ensure_guild(guild_id)
    row = await fetchone(f"SELECT {key} AS value FROM guild_settings WHERE guild_id=?", (guild_id,))
    return row["value"] if row and row["value"] is not None else default

async def set_setting(guild_id, key, value):
    allowed = {
        "prefix","log_channel_id","review_channel_id","staff_role_id",
        "admin_role_id","mod_role_id","welcome_channel_id","goodbye_channel_id","verified_role_id",
        "ticket_transcript_channel_id","excluded_category_id","upi_id","upi_payee",
        "ltc_address","btc_address","channel_template"
    }
    if key not in allowed:
        raise ValueError("Unsupported setting")
    await ensure_guild(guild_id)
    await execute(f"UPDATE guild_settings SET {key}=? WHERE guild_id=?", (value, guild_id))
,
 log_channel_id INTEGER, review_channel_id INTEGER,
 staff_role_id INTEGER, admin_role_id INTEGER, mod_role_id INTEGER,
 welcome_channel_id INTEGER, goodbye_channel_id INTEGER,
 ticket_transcript_channel_id INTEGER,
 excluded_category_id INTEGER,
 upi_id TEXT, upi_payee TEXT,
 ltc_address TEXT, btc_address TEXT,
 channel_template TEXT DEFAULT '{name}-hub'
);
CREATE TABLE IF NOT EXISTS bot_owners (user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS bot_admins (user_id INTEGER PRIMARY KEY, added_by INTEGER);
CREATE TABLE IF NOT EXISTS tickets (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 channel_id INTEGER UNIQUE NOT NULL, owner_id INTEGER NOT NULL,
 staff_role_id INTEGER, panel_key TEXT NOT NULL, status TEXT DEFAULT 'open',
 claimed_by INTEGER, created_at TEXT NOT NULL, closed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_ticket_per_user
 ON tickets(guild_id, owner_id) WHERE status='open';
CREATE TABLE IF NOT EXISTS ticket_panels (
 guild_id INTEGER NOT NULL, panel_key TEXT NOT NULL, title TEXT NOT NULL,
 description TEXT NOT NULL, category_id INTEGER, staff_role_id INTEGER,
 image_url TEXT, thumbnail_url TEXT,
 PRIMARY KEY(guild_id,panel_key)
);
CREATE TABLE IF NOT EXISTS applications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, username TEXT NOT NULL, answers TEXT NOT NULL,
 ai_summary TEXT, ai_recommendation TEXT, status TEXT DEFAULT 'pending',
 reviewer_id INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slots (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 channel_id INTEGER UNIQUE NOT NULL, owner_id INTEGER NOT NULL,
 ping_limit INTEGER DEFAULT 5, ping_count INTEGER DEFAULT 0,
 reset_at TEXT NOT NULL, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS xp (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 xp INTEGER DEFAULT 0, level INTEGER DEFAULT 0,
 PRIMARY KEY(guild_id,user_id)
);
CREATE TABLE IF NOT EXISTS invite_counts (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 joins INTEGER DEFAULT 0, PRIMARY KEY(guild_id,user_id)
);
CREATE TABLE IF NOT EXISTS invoices (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, method TEXT NOT NULL, amount REAL NOT NULL,
 reference TEXT, status TEXT DEFAULT 'pending', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
 actor_id INTEGER, event TEXT NOT NULL, details TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS server_change_plans (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, created_by INTEGER NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT DEFAULT 'pending', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS used_payment_txids (
 txid TEXT PRIMARY KEY, invoice_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, used_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_usage (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
 user_id INTEGER, purpose TEXT, created_at TEXT NOT NULL
);
"""

async def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # Safe additive migrations for databases created by earlier Hydrogen builds.
        cols = {row[1] for row in await (await db.execute("PRAGMA table_info(invoices)")).fetchall()}
        if "txid" not in cols: await db.execute("ALTER TABLE invoices ADD COLUMN txid TEXT")
        if "confirmations" not in cols: await db.execute("ALTER TABLE invoices ADD COLUMN confirmations INTEGER DEFAULT 0")
        await db.commit()

async def execute(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        await db.commit()
        return cur

async def fetchone(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        row = await cur.fetchone()
        return dict(row) if row else None

async def fetchall(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        return [dict(r) for r in await cur.fetchall()]

async def ensure_guild(guild_id):
    await execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (guild_id,))

async def get_setting(guild_id, key, default=None):
    await ensure_guild(guild_id)
    row = await fetchone(f"SELECT {key} AS value FROM guild_settings WHERE guild_id=?", (guild_id,))
    return row["value"] if row and row["value"] is not None else default

async def set_setting(guild_id, key, value):
    allowed = {
        "prefix","log_channel_id","review_channel_id","staff_role_id",
        "admin_role_id","mod_role_id","welcome_channel_id","goodbye_channel_id","verified_role_id",
        "ticket_transcript_channel_id","excluded_category_id","upi_id","upi_payee",
        "ltc_address","btc_address","channel_template"
    }
    if key not in allowed:
        raise ValueError("Unsupported setting")
    await ensure_guild(guild_id)
    await execute(f"UPDATE guild_settings SET {key}=? WHERE guild_id=?", (value, guild_id))
 WHERE prefix='!'")
        cols = {row[1] for row in await (await db.execute("PRAGMA table_info(invoices)")).fetchall()}
        if "txid" not in cols: await db.execute("ALTER TABLE invoices ADD COLUMN txid TEXT")
        if "confirmations" not in cols: await db.execute("ALTER TABLE invoices ADD COLUMN confirmations INTEGER DEFAULT 0")
        await db.commit()

async def execute(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        await db.commit()
        return cur

async def fetchone(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        row = await cur.fetchone()
        return dict(row) if row else None

async def fetchall(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        return [dict(r) for r in await cur.fetchall()]

async def ensure_guild(guild_id):
    await execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (guild_id,))

async def get_setting(guild_id, key, default=None):
    await ensure_guild(guild_id)
    row = await fetchone(f"SELECT {key} AS value FROM guild_settings WHERE guild_id=?", (guild_id,))
    return row["value"] if row and row["value"] is not None else default

async def set_setting(guild_id, key, value):
    allowed = {
        "prefix","log_channel_id","review_channel_id","staff_role_id",
        "admin_role_id","mod_role_id","welcome_channel_id","goodbye_channel_id","verified_role_id",
        "ticket_transcript_channel_id","excluded_category_id","upi_id","upi_payee",
        "ltc_address","btc_address","channel_template"
    }
    if key not in allowed:
        raise ValueError("Unsupported setting")
    await ensure_guild(guild_id)
    await execute(f"UPDATE guild_settings SET {key}=? WHERE guild_id=?", (value, guild_id))
,
 log_channel_id INTEGER, review_channel_id INTEGER,
 staff_role_id INTEGER, admin_role_id INTEGER, mod_role_id INTEGER,
 welcome_channel_id INTEGER, goodbye_channel_id INTEGER,
 ticket_transcript_channel_id INTEGER,
 excluded_category_id INTEGER,
 upi_id TEXT, upi_payee TEXT,
 ltc_address TEXT, btc_address TEXT,
 channel_template TEXT DEFAULT '{name}-hub'
);
CREATE TABLE IF NOT EXISTS bot_owners (user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS bot_admins (user_id INTEGER PRIMARY KEY, added_by INTEGER);
CREATE TABLE IF NOT EXISTS tickets (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 channel_id INTEGER UNIQUE NOT NULL, owner_id INTEGER NOT NULL,
 staff_role_id INTEGER, panel_key TEXT NOT NULL, status TEXT DEFAULT 'open',
 claimed_by INTEGER, created_at TEXT NOT NULL, closed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_ticket_per_user
 ON tickets(guild_id, owner_id) WHERE status='open';
CREATE TABLE IF NOT EXISTS ticket_panels (
 guild_id INTEGER NOT NULL, panel_key TEXT NOT NULL, title TEXT NOT NULL,
 description TEXT NOT NULL, category_id INTEGER, staff_role_id INTEGER,
 image_url TEXT, thumbnail_url TEXT,
 PRIMARY KEY(guild_id,panel_key)
);
CREATE TABLE IF NOT EXISTS applications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, username TEXT NOT NULL, answers TEXT NOT NULL,
 ai_summary TEXT, ai_recommendation TEXT, status TEXT DEFAULT 'pending',
 reviewer_id INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slots (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 channel_id INTEGER UNIQUE NOT NULL, owner_id INTEGER NOT NULL,
 ping_limit INTEGER DEFAULT 5, ping_count INTEGER DEFAULT 0,
 reset_at TEXT NOT NULL, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS xp (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 xp INTEGER DEFAULT 0, level INTEGER DEFAULT 0,
 PRIMARY KEY(guild_id,user_id)
);
CREATE TABLE IF NOT EXISTS invite_counts (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 joins INTEGER DEFAULT 0, PRIMARY KEY(guild_id,user_id)
);
CREATE TABLE IF NOT EXISTS invoices (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, method TEXT NOT NULL, amount REAL NOT NULL,
 reference TEXT, status TEXT DEFAULT 'pending', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
 actor_id INTEGER, event TEXT NOT NULL, details TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS server_change_plans (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, created_by INTEGER NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT DEFAULT 'pending', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS used_payment_txids (
 txid TEXT PRIMARY KEY, invoice_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, used_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_usage (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
 user_id INTEGER, purpose TEXT, created_at TEXT NOT NULL
);
"""

async def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # Safe additive migrations for databases created by earlier Hydrogen builds.
        cols = {row[1] for row in await (await db.execute("PRAGMA table_info(invoices)")).fetchall()}
        if "txid" not in cols: await db.execute("ALTER TABLE invoices ADD COLUMN txid TEXT")
        if "confirmations" not in cols: await db.execute("ALTER TABLE invoices ADD COLUMN confirmations INTEGER DEFAULT 0")
        await db.commit()

async def execute(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        await db.commit()
        return cur

async def fetchone(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        row = await cur.fetchone()
        return dict(row) if row else None

async def fetchall(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        return [dict(r) for r in await cur.fetchall()]

async def ensure_guild(guild_id):
    await execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (guild_id,))

async def get_setting(guild_id, key, default=None):
    await ensure_guild(guild_id)
    row = await fetchone(f"SELECT {key} AS value FROM guild_settings WHERE guild_id=?", (guild_id,))
    return row["value"] if row and row["value"] is not None else default

async def set_setting(guild_id, key, value):
    allowed = {
        "prefix","log_channel_id","review_channel_id","staff_role_id",
        "admin_role_id","mod_role_id","welcome_channel_id","goodbye_channel_id","verified_role_id",
        "ticket_transcript_channel_id","excluded_category_id","upi_id","upi_payee",
        "ltc_address","btc_address","channel_template"
    }
    if key not in allowed:
        raise ValueError("Unsupported setting")
    await ensure_guild(guild_id)
    await execute(f"UPDATE guild_settings SET {key}=? WHERE guild_id=?", (value, guild_id))

import secrets
from config import DEFAULT_PREFIX
from core import db

def is_global_owner(user_id, owner_ids):
    return int(user_id) in {int(x) for x in owner_ids}

async def is_bot_admin(member, owner_ids):
    if is_global_owner(member.id, owner_ids):
        return True
    row = await db.fetchone("SELECT user_id FROM bot_admins WHERE user_id=?", (member.id,))
    if row:
        return True
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    admin_role = await db.get_setting(member.guild.id, "admin_role_id")
    mod_role = await db.get_setting(member.guild.id, "mod_role_id")
    allowed = {int(x) for x in (admin_role, mod_role) if x}
    return any(r.id in allowed for r in member.roles)

async def is_server_owner_or_global(member, owner_ids):
    return member.guild and (member.id == member.guild.owner_id or is_global_owner(member.id, owner_ids))

def safe_name(value):
    import re
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", value.lower())).strip("-")[:80] or "member"

def new_reference():
    return secrets.token_hex(5).upper()

from datetime import datetime, timezone
import discord
from core import db

LOG_CHANNELS = {
    "audit": "logs-audit",
    "moderation": "logs-moderation",
    "members": "logs-members",
    "messages": "logs-messages",
    "security": "logs-security",
}

def _event_type(event):
    value = str(event).lower()
    if any(word in value for word in ("ban", "kick", "timeout", "mute", "warn", "purge", "moderation", "role granted", "role revoked")):
        return "moderation"
    if any(word in value for word in ("member", "join", "leave", "welcome", "goodbye", "nickname")):
        return "members"
    if any(word in value for word in ("message", "edit", "delete", "bulk")):
        return "messages"
    if any(word in value for word in ("security", "attack", "raid", "spam", "abuse", "verification", "permission")):
        return "security"
    return "audit"

async def ensure_log_channels(guild, actor=None):
    """Create/reuse private, screenshot-style log categories and persist each route."""
    me = guild.me
    staff_names = {
        "Founder", "Owner", "Manager", "Admin", "Senior Manager",
        "Senior Moderator", "Moderator", "Senior Staff", "Staff", "Trial Staff",
    }
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            embed_links=True, attach_files=True
        ),
    }
    for role in guild.roles:
        if role.name in {("W X・" + name) for name in staff_names}:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True
            )

    categories = {
        "audit": ".Logs",
        "members": ".Logs",
        "messages": ".Logs",
        "security": ".Logs",
        "moderation": ".Mod Logs",
    }
    result = {}
    category_cache = {}
    for event_type, name in LOG_CHANNELS.items():
        category_name = categories[event_type]
        category = category_cache.get(category_name)
        if category is None:
            category = discord.utils.get(guild.categories, name=category_name)
            if category is None:
                category = await guild.create_category(
                    category_name, overwrites=overwrites,
                    reason=f"WardenX log setup requested by {actor or 'server builder'}"
                )
            else:
                await category.edit(
                    overwrites=overwrites,
                    reason="Apply WardenX private log-category permissions"
                )
            category_cache[category_name] = category

        channel = discord.utils.get(category.text_channels, name=name)
        if channel is None:
            channel = await guild.create_text_channel(
                name, category=category, overwrites=overwrites,
                reason=f"WardenX {event_type} log channel"
            )
        else:
            await channel.edit(
                category=category, sync_permissions=True,
                reason="Place WardenX log channel in its dedicated category"
            )
        await db.execute(
            "INSERT INTO log_channel_routes(guild_id,event_type,channel_id) VALUES(?,?,?) "
            "ON CONFLICT(guild_id,event_type) DO UPDATE SET channel_id=excluded.channel_id",
            (guild.id, event_type, channel.id)
        )
        result[event_type] = channel
    await db.set_setting(guild.id, "log_channel_id", result["audit"].id)
    return result

async def audit(bot, guild, event, details="", actor=None):
    gid = guild.id if guild else None
    actor_id = actor.id if actor else None
    now = datetime.now(timezone.utc).isoformat()
    await db.execute("INSERT INTO audit_events(guild_id,actor_id,event,details,created_at) VALUES(?,?,?,?,?)", (gid, actor_id, event, str(details)[:3500], now))
    if not guild:
        return
    event_type = _event_type(event)
    route = await db.fetchone("SELECT channel_id FROM log_channel_routes WHERE guild_id=? AND event_type=?", (guild.id, event_type))
    channel_id = route["channel_id"] if route else None
    if not channel_id and event_type == "audit":
        channel_id = await db.get_setting(guild.id, "log_channel_id")
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel:
        embed = discord.Embed(title=f"{event_type.title()} • {event[:220]}", description=str(details)[:4000] or "No additional details", color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Actor", value=actor.mention if actor else "System", inline=True)
        embed.set_footer(text=f"WardenX Manager • {event_type.title()} Logs")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

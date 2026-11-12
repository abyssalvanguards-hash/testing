"""Owner-controlled busy mode and conservative server-structure backups."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import discord
from discord.ext import commands, tasks
from core import db
from core.security import is_server_owner_or_global

BASE = Path(__file__).resolve().parents[1]
BACKUP_DIR = BASE / "data" / "backups"

def validate_backup_payload(payload, guild_id):
    """Validate the backup structure before any Discord API operation."""
    if not isinstance(payload, dict) or payload.get("format") != 1:
        raise ValueError("Unsupported backup format.")
    if str(payload.get("guild_id")) != str(guild_id):
        raise ValueError("Backup belongs to a different server.")
    roles = payload.get("roles")
    channels = payload.get("channels")
    if not isinstance(roles, list) or not isinstance(channels, list):
        raise ValueError("Backup roles or channels are malformed.")
    for role in roles:
        if not isinstance(role, dict) or not isinstance(role.get("name"), str) or not role["name"].strip():
            raise ValueError("Backup contains an invalid role entry.")
        for key in ("permissions", "color"):
            if key in role and (not isinstance(role[key], int) or role[key] < 0):
                raise ValueError("Backup contains invalid role settings.")
    for channel in channels:
        if not isinstance(channel, dict) or channel.get("type") not in {"category", "text", "voice"}:
            raise ValueError("Backup contains an unsupported channel entry.")
        if not isinstance(channel.get("name"), str) or not channel["name"].strip():
            raise ValueError("Backup contains an invalid channel name.")
        overwrites = channel.get("overwrites", [])
        if not isinstance(overwrites, list):
            raise ValueError("Backup channel overwrites are malformed.")
        for overwrite in overwrites:
            if (not isinstance(overwrite, dict)
                    or overwrite.get("target_type") != "role"
                    or not isinstance(overwrite.get("target_name"), str)
                    or not isinstance(overwrite.get("allow", 0), int)
                    or not isinstance(overwrite.get("deny", 0), int)
                    or overwrite.get("allow", 0) < 0
                    or overwrite.get("deny", 0) < 0):
                raise ValueError("Backup contains an invalid permission overwrite.")
    return roles, channels


def parse_duration(value):
    match = re.fullmatch(r"([1-9][0-9]?)(h|d)", str(value).strip().lower())
    if not match:
        raise ValueError("Use a duration such as 6h, 12h, or 2d (maximum 30d).")
    amount, unit = int(match.group(1)), match.group(2)
    hours = amount if unit == "h" else amount * 24
    if hours > 30 * 24:
        raise ValueError("Busy mode can be set for at most 30 days at a time.")
    return timedelta(hours=hours)

class ServerCare(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.expiry_check.start()

    async def cog_unload(self):
        self.expiry_check.cancel()

    async def _owner(self, ctx):
        if not ctx.guild or not await is_server_owner_or_global(ctx.author, self.bot.owner_ids):
            await ctx.reply("Only the server owner or configured Hydrogen owner can use this command.")
            return False
        return True

    @commands.hybrid_command(name="busy")
    async def busy(self, ctx, duration: str):
        """Enable server-care mode for a limited period (examples: 12h, 2d)."""
        if not await self._owner(ctx): return
        try: delta = parse_duration(duration)
        except ValueError as exc: return await ctx.reply(str(exc))
        now = datetime.now(timezone.utc); expires = now + delta
        await db.execute("INSERT INTO busy_states(guild_id,owner_id,expires_at,created_at) VALUES(?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET owner_id=excluded.owner_id, expires_at=excluded.expires_at, created_at=excluded.created_at", (ctx.guild.id, ctx.author.id, expires.isoformat(), now.isoformat()))
        await ctx.reply(f"Server-care mode is enabled until <t:{int(expires.timestamp())}:F>. Hydrogen continues its already-configured moderation, welcome, and logging features. It will not invent new rules or perform unapproved administrative changes.")

    @commands.hybrid_command(name="busyoff")
    async def busy_off(self, ctx):
        """Turn off server-care mode early."""
        if not await self._owner(ctx): return
        await db.execute("DELETE FROM busy_states WHERE guild_id=?", (ctx.guild.id,))
        await ctx.reply("Server-care mode is now off.")

    @commands.hybrid_command(name="busystatus")
    async def busy_status(self, ctx):
        if not ctx.guild: return await ctx.reply("Use this command in a server.")
        row = await db.fetchone("SELECT expires_at FROM busy_states WHERE guild_id=?", (ctx.guild.id,))
        if not row: return await ctx.reply("Server-care mode is currently off.")
        try: expiry = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            await db.execute("DELETE FROM busy_states WHERE guild_id=?", (ctx.guild.id,))
            return await ctx.reply("The saved busy-mode expiry was invalid and has been cleared.")
        if expiry <= datetime.now(timezone.utc):
            await db.execute("DELETE FROM busy_states WHERE guild_id=?", (ctx.guild.id,))
            return await ctx.reply("Server-care mode has expired.")
        await ctx.reply(f"Server-care mode is on until <t:{int(expiry.timestamp())}:F>.")

    @tasks.loop(minutes=5)
    async def expiry_check(self):
        await db.execute("DELETE FROM busy_states WHERE expires_at<=?", (datetime.now(timezone.utc).isoformat(),))

    @expiry_check.before_loop
    async def before_expiry_check(self): await self.bot.wait_until_ready()

    def _snapshot(self, guild):
        roles = []
        for role in guild.roles:
            if role.is_default() or role.managed: continue
            roles.append({"name": role.name, "permissions": role.permissions.value, "color": role.color.value, "hoist": role.hoist, "mentionable": role.mentionable, "position": role.position})
        channels = []
        for channel in guild.channels:
            # Stage and other channel types are excluded because restore only recreates categories/text/voice.
            if not isinstance(channel, (discord.CategoryChannel, discord.TextChannel, discord.VoiceChannel)):
                continue
            kind = "category" if isinstance(channel, discord.CategoryChannel) else "text" if isinstance(channel, discord.TextChannel) else "voice"
            overwrites = []
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Role):
                    allow, deny = overwrite.pair()
                    overwrites.append({"target_type": "role", "target_name": target.name, "allow": allow.value, "deny": deny.value})
            item = {"name": channel.name, "type": kind, "position": channel.position, "category": channel.category.name if getattr(channel, "category", None) else None, "overwrites": overwrites}
            if isinstance(channel, discord.TextChannel): item.update({"topic": channel.topic, "nsfw": channel.nsfw, "slowmode_delay": channel.slowmode_delay})
            if isinstance(channel, discord.VoiceChannel): item.update({"bitrate": channel.bitrate, "user_limit": channel.user_limit})
            channels.append(item)
        return {"format": 1, "guild_id": str(guild.id), "guild_name": guild.name, "created_at": datetime.now(timezone.utc).isoformat(), "limitations": ["Message history is not included.", "Member lists and personal data are not included.", "Managed/integration roles are not included.", "Stage and other unsupported channel types are excluded."], "roles": roles, "channels": channels}

    @commands.hybrid_group(name="backup", invoke_without_command=True)
    async def backup(self, ctx): await ctx.reply("Use /backup create or /backup list.")

    @backup.command(name="create")
    async def backup_create(self, ctx):
        """Create a JSON backup of server roles, channels, and role overwrites."""
        if not await self._owner(ctx): return
        guild_dir = BACKUP_DIR / str(ctx.guild.id); guild_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"); path = guild_dir / f"{stamp}.json"
        payload = self._snapshot(ctx.guild)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        cur = await db.execute("INSERT INTO server_backups(guild_id,created_by,file_path,created_at,description) VALUES(?,?,?,?,?)", (ctx.guild.id, ctx.author.id, str(path.relative_to(BASE)), payload["created_at"], f"{len(payload['roles'])} roles; {len(payload['channels'])} channels"))
        await ctx.reply(f"Backup #{cur.lastrowid} created. Includes {len(payload['roles'])} roles and {len(payload['channels'])} channels. Message history and member data are not included.")

    @backup.command(name="list")
    async def backup_list(self, ctx):
        if not await self._owner(ctx): return
        rows = await db.fetchall("SELECT id,created_at,description FROM server_backups WHERE guild_id=? ORDER BY id DESC LIMIT 10", (ctx.guild.id,))
        if not rows: return await ctx.reply("No backups found for this server.")
        lines = [f"#{r['id']} • {r['created_at']} • {r['description'] or 'Server structure'}" for r in rows]
        await ctx.reply("**Recent server backups**\n" + "\n".join(lines))

    @backup.command(name="export")
    async def backup_export(self, ctx, backup_id: int):
        """Send a selected JSON backup file to the server owner."""
        if not await self._owner(ctx):
            return
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.reply("Only the actual Discord server owner can export a backup.")
        row = await db.fetchone(
            "SELECT file_path FROM server_backups WHERE id=? AND guild_id=?",
            (backup_id, ctx.guild.id)
        )
        if not row:
            return await ctx.reply("Backup not found for this server.")
        path = (BASE / row["file_path"]).resolve()
        if BACKUP_DIR.resolve() not in path.parents or not path.is_file():
            return await ctx.reply("Backup file is missing or its path is invalid.")
        if path.stat().st_size > ctx.guild.filesize_limit:
            return await ctx.reply("This backup exceeds the server's upload size limit.")
        await ctx.reply(
            f"Here is backup #{backup_id}. Keep it somewhere safe.",
            file=discord.File(path, filename=f"hydrogen-backup-{backup_id}.json")
        )

    @backup.command(name="restore")
    async def backup_restore(self, ctx, backup_id: int, confirmation: str = ""):
        """Restore missing structure from a backup; never deletes existing items."""
        if not await self._owner(ctx): return
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.reply("Only the actual Discord server owner can restore a server backup.")
        row = await db.fetchone("SELECT file_path FROM server_backups WHERE id=? AND guild_id=?", (backup_id, ctx.guild.id))
        if not row: return await ctx.reply("Backup not found for this server.")
        path = (BASE / row["file_path"]).resolve()
        if BACKUP_DIR.resolve() not in path.parents or not path.is_file():
            return await ctx.reply("Backup file is missing or its path is invalid.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            roles, channels = validate_backup_payload(payload, ctx.guild.id)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return await ctx.reply(f"Could not read this backup: {str(exc)[:180]}")
        missing_roles = [x for x in roles if not discord.utils.get(ctx.guild.roles, name=x["name"])]
        missing_categories = [x for x in channels if x["type"] == "category" and not discord.utils.get(ctx.guild.categories, name=x["name"])]
        missing_channels = [x for x in channels if x["type"] in {"text", "voice"} and not discord.utils.get(ctx.guild.channels, name=x["name"])]
        preview = (f"Backup #{backup_id}: create {len(missing_roles)} roles, {len(missing_categories)} categories, and {len(missing_channels)} channels. Existing items will be left unchanged; nothing will be deleted. Message history and member assignments cannot be restored. To proceed, run /backup restore {backup_id} CONFIRM.")
        if confirmation != "CONFIRM": return await ctx.reply(preview)
        bot_member = ctx.guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_roles or not bot_member.guild_permissions.manage_channels:
            return await ctx.reply("Hydrogen needs Manage Roles and Manage Channels permissions to restore this structure.")
        try:
            role_map = {r.name: r for r in ctx.guild.roles}
            for item in missing_roles:
                role = await ctx.guild.create_role(name=str(item["name"])[:100], permissions=discord.Permissions(int(item.get("permissions", 0))), colour=discord.Colour(int(item.get("color", 0))), hoist=bool(item.get("hoist", False)), mentionable=bool(item.get("mentionable", False)), reason=f"Owner-confirmed Hydrogen backup restore #{backup_id}")
                role_map[role.name] = role
            category_map = {x.name: x for x in ctx.guild.categories}
            for item in missing_categories:
                overwrites = {}
                for ow in item.get("overwrites", []):
                    role = role_map.get(ow.get("target_name"))
                    if role: overwrites[role] = discord.PermissionOverwrite.from_pair(discord.Permissions(int(ow.get("allow", 0))), discord.Permissions(int(ow.get("deny", 0))))
                category = await ctx.guild.create_category(str(item["name"])[:100], overwrites=overwrites, reason=f"Owner-confirmed Hydrogen backup restore #{backup_id}")
                category_map[category.name] = category
            for item in missing_channels:
                category = category_map.get(item.get("category")); overwrites = {}
                for ow in item.get("overwrites", []):
                    role = role_map.get(ow.get("target_name"))
                    if role: overwrites[role] = discord.PermissionOverwrite.from_pair(discord.Permissions(int(ow.get("allow", 0))), discord.Permissions(int(ow.get("deny", 0))))
                name = str(item["name"])[:100]; reason = f"Owner-confirmed Hydrogen backup restore #{backup_id}"
                if item["type"] == "text":
                    await ctx.guild.create_text_channel(name, category=category, overwrites=overwrites, topic=item.get("topic"), nsfw=bool(item.get("nsfw", False)), slowmode_delay=int(item.get("slowmode_delay", 0)), reason=reason)
                elif item["type"] == "voice":
                    await ctx.guild.create_voice_channel(name, category=category, overwrites=overwrites, bitrate=min(int(item.get("bitrate", 64000)), ctx.guild.bitrate_limit), user_limit=int(item.get("user_limit", 0)), reason=reason)
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError, KeyError) as exc:
            return await ctx.reply(f"Restore stopped safely after a partial restore: {type(exc).__name__}: {str(exc)[:180]}")
        await ctx.reply(f"Restore completed: created {len(missing_roles)} roles, {len(missing_categories)} categories, and {len(missing_channels)} channels. No existing items were edited or deleted.")

async def setup(bot): await bot.add_cog(ServerCare(bot))
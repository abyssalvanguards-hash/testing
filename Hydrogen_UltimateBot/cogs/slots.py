import hashlib
import secrets
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands

from core import db
from core.security import is_bot_admin, safe_name
from core.logging import audit
from emojis import Emoji


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class Slots(commands.Cog):
    """Slot channels with ping enforcement, timed locks and owner recovery."""

    def __init__(self, bot):
        self.bot = bot

    async def _admin(self, ctx):
        if not await is_bot_admin(ctx.author, self.bot.owner_ids):
            await ctx.reply(f"{Emoji.error} Permission denied.")
            return False
        return True

    @commands.hybrid_command(name="createslot", description="Create a private slot for a member")
    async def create_slot(self, ctx, member: discord.Member, ping_limit: int = 5, *, name=None):
        if not await self._admin(ctx):
            return
        if not 1 <= ping_limit <= 100:
            return await ctx.reply(f"{Emoji.warning} Ping limit must be between 1 and 100.")
        existing = await db.fetchone(
            "SELECT id FROM slots WHERE guild_id=? AND owner_id=? AND active=1",
            (ctx.guild.id, member.id)
        )
        if existing:
            return await ctx.reply(f"{Emoji.warning} That member already has an active slot.")
        channel_name = safe_name(name or f"{member.display_name}-slot")
        channel = await ctx.guild.create_text_channel(channel_name, reason="Hydrogen slot creation")
        await channel.set_permissions(ctx.guild.default_role, view_channel=False, send_messages=False)
        await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await channel.set_permissions(ctx.guild.me, view_channel=True, send_messages=True, manage_channels=True)
        await db.execute(
            """INSERT INTO slots(guild_id,channel_id,owner_id,ping_limit,ping_count,reset_at,active,locked)
               VALUES(?,?,?,?,?,?,1,0)""",
            (ctx.guild.id, channel.id, member.id, ping_limit, 0, datetime.now(timezone.utc).isoformat())
        )
        await audit(self.bot, ctx.guild, "Slot created", f"{channel.mention} for {member.mention}", ctx.author)
        await ctx.reply(f"{Emoji.success} Created {channel.mention} for {member.mention}. Ping limit: **{ping_limit}**.")

    @commands.hybrid_command(name="slotinfo", description="Show this slot's limits and status")
    async def info(self, ctx):
        row = await db.fetchone("SELECT * FROM slots WHERE channel_id=? AND active=1", (ctx.channel.id,))
        if not row:
            return await ctx.reply(f"{Emoji.warning} This channel is not a registered active slot.")
        state = "🔒 Locked" if row.get("locked") else "🟢 Active"
        await ctx.reply(
            f"{Emoji.slot} **Slot status:** {state}\n"
            f"Owner: <@{row['owner_id']}>\n"
            f"Ping usage: **{row['ping_count']}/{row['ping_limit']}**"
        )

    async def _lock_slot(self, guild, row, reason):
        channel = guild.get_channel(row["channel_id"])
        owner = guild.get_member(row["owner_id"])
        if channel and owner:
            await channel.set_permissions(owner, view_channel=True, send_messages=False, read_message_history=True)
        await db.execute("UPDATE slots SET locked=1 WHERE id=?", (row["id"],))
        if channel:
            await channel.send(f"{Emoji.lock} Slot automatically locked: {reason}. Contact a server admin for review.")
        await audit(self.bot, guild, "Slot automatically locked", f"Slot {row['channel_id']}: {reason}", owner)

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.author.bot:
            return
        row = await db.fetchone(
            "SELECT * FROM slots WHERE channel_id=? AND active=1",
            (message.channel.id,)
        )
        if not row or message.author.id != row["owner_id"] or row.get("locked"):
            return
        # Count actual mass-ping classes, not every ordinary user mention.
        everyone_count = 1 if message.mention_everyone else 0
        here_count = 1 if "@here" in message.content else 0
        user_count = len(message.mentions)
        used = everyone_count + here_count + user_count
        if not used:
            return
        new_count = row["ping_count"] + used
        if new_count > row["ping_limit"]:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            await self._lock_slot(message.guild, row, f"ping limit ({row['ping_limit']}) exceeded")
            return
        await db.execute("UPDATE slots SET ping_count=? WHERE id=?", (new_count, row["id"]))
        if new_count >= row["ping_limit"]:
            await self._lock_slot(message.guild, row, "configured ping allowance used")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        row = await db.fetchone(
            "SELECT * FROM slots WHERE guild_id=? AND owner_id=? AND active=1",
            (member.guild.id, member.id)
        )
        if not row:
            return
        # Keep the channel and database record. Hide it from the departed member;
        # server administrators retain access through Discord's Administrator permission.
        channel = member.guild.get_channel(row["channel_id"])
        if channel:
            await channel.set_permissions(member, view_channel=False, send_messages=False)
        code = secrets.token_urlsafe(18)
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        await db.execute(
            "UPDATE slots SET recovery_hash=?,recovery_expires=?,locked=1 WHERE id=?",
            (_code_hash(code), expires, row["id"])
        )
        try:
            await member.send(
                f"{Emoji.slot} Your Hydrogen slot in **{member.guild.name}** has been preserved for 30 days.\n"
                f"Recovery code: ||{code}||\n"
                "Keep this code private. If you rejoin, use /slotrecover in the server. "
                "An admin may also help verify ownership."
            )
        except discord.Forbidden:
            await audit(self.bot, member.guild, "Slot recovery DM failed", f"Could not DM former owner {member.id}.")
        await audit(self.bot, member.guild, "Slot preserved for recovery", f"Channel {row['channel_id']} retained; owner left.")

    @commands.hybrid_command(name="slotrecover", description="Recover your preserved slot using its one-time code")
    async def recover(self, ctx, code: str):
        row = await db.fetchone(
            """SELECT * FROM slots WHERE guild_id=? AND owner_id=? AND active=1
               AND recovery_hash IS NOT NULL ORDER BY id DESC LIMIT 1""",
            (ctx.guild.id, ctx.author.id)
        )
        if not row or not secrets.compare_digest(row["recovery_hash"], _code_hash(code.strip())):
            return await ctx.reply(f"{Emoji.error} Recovery code is invalid or no recoverable slot was found.", ephemeral=True)
        expiry = datetime.fromisoformat(row["recovery_expires"])
        if expiry < datetime.now(timezone.utc):
            await db.execute("UPDATE slots SET recovery_hash=NULL,recovery_expires=NULL WHERE id=?", (row["id"],))
            return await ctx.reply(f"{Emoji.warning} This recovery code has expired. Ask a server admin for help.", ephemeral=True)
        channel = ctx.guild.get_channel(row["channel_id"])
        if not channel:
            return await ctx.reply(f"{Emoji.error} The preserved slot channel no longer exists. Contact an admin.", ephemeral=True)
        await channel.set_permissions(ctx.author, view_channel=True, send_messages=True, read_message_history=True)
        await db.execute(
            "UPDATE slots SET recovery_hash=NULL,recovery_expires=NULL,locked=0,ping_count=0,reset_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), row["id"])
        )
        await audit(self.bot, ctx.guild, "Slot recovered", f"{ctx.author.mention} recovered {channel.mention}", ctx.author)
        await ctx.reply(f"{Emoji.success} Your slot has been restored: {channel.mention}", ephemeral=True)

    @commands.hybrid_command(name="slotunlock", description="Admin: unlock a member's slot after review")
    async def unlock(self, ctx, member: discord.Member):
        if not await self._admin(ctx):
            return
        row = await db.fetchone(
            "SELECT * FROM slots WHERE guild_id=? AND owner_id=? AND active=1",
            (ctx.guild.id, member.id)
        )
        if not row:
            return await ctx.reply(f"{Emoji.warning} No active slot found for that member.")
        channel = ctx.guild.get_channel(row["channel_id"])
        if not channel:
            return await ctx.reply(f"{Emoji.error} Slot channel is missing.")
        await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await db.execute("UPDATE slots SET locked=0,ping_count=0,reset_at=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(), row["id"]))
        await audit(self.bot, ctx.guild, "Slot unlocked by admin", f"{channel.mention} for {member.mention}", ctx.author)
        await ctx.reply(f"{Emoji.success} Unlocked {channel.mention} and reset its ping counter.")

    @commands.hybrid_command(name="slotreset", description="Admin: reset a slot's ping counter")
    async def reset(self, ctx, channel: discord.TextChannel = None):
        if not await self._admin(ctx):
            return
        channel = channel or ctx.channel
        await db.execute(
            "UPDATE slots SET ping_count=0,reset_at=? WHERE channel_id=?",
            (datetime.now(timezone.utc).isoformat(), channel.id)
        )
        await ctx.reply(f"{Emoji.success} Ping count reset for {channel.mention}.")


async def setup(bot):
    await bot.add_cog(Slots(bot))

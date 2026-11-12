"""Per-guild invite attribution and configurable invite rewards."""
import discord
from discord.ext import commands
from core import db
from core.logging import audit
from emojis import Emoji


class InviteRewards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.invite_cache = {}

    async def _snapshot(self, guild):
        try:
            invites = await guild.invites()
            self.invite_cache[guild.id] = {
                item.code: (item.uses or 0, item.inviter.id if item.inviter else None)
                for item in invites
            }
        except (discord.Forbidden, discord.HTTPException):
            self.invite_cache[guild.id] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._snapshot(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        self.invite_cache.setdefault(invite.guild.id, {})[invite.code] = (
            invite.uses or 0, invite.inviter.id if invite.inviter else None
        )

    @commands.Cog.listener()
    async def on_member_join(self, member):
        before = self.invite_cache.get(member.guild.id, {})
        inviter_id = None
        try:
            current = await member.guild.invites()
            for invite in current:
                old_uses, old_inviter = before.get(
                    invite.code, (0, invite.inviter.id if invite.inviter else None)
                )
                if (invite.uses or 0) > old_uses:
                    inviter_id = invite.inviter.id if invite.inviter else old_inviter
                    break
            self.invite_cache[member.guild.id] = {
                item.code: (item.uses or 0, item.inviter.id if item.inviter else None)
                for item in current
            }
        except (discord.Forbidden, discord.HTTPException):
            return

        if not inviter_id or inviter_id == member.id:
            return
        await db.execute(
            """INSERT INTO invite_counts(guild_id,user_id,joins) VALUES(?,?,1)
               ON CONFLICT(guild_id,user_id) DO UPDATE SET joins=joins+1""",
            (member.guild.id, inviter_id)
        )
        await self._award_rewards(member.guild, inviter_id)

    async def _award_rewards(self, guild, user_id):
        rewards = await db.fetchall(
            "SELECT invite_threshold, role_id, dm_message FROM invite_rewards WHERE guild_id=? ORDER BY invite_threshold",
            (guild.id,)
        )
        count = await db.fetchone(
            "SELECT joins FROM invite_counts WHERE guild_id=? AND user_id=?",
            (guild.id, user_id)
        )
        total = count["joins"] if count else 0
        member = guild.get_member(user_id)
        if not member:
            return
        for reward in rewards:
            if total < reward["invite_threshold"]:
                continue
            role = guild.get_role(reward["role_id"])
            if not role or role in member.roles or role >= guild.me.top_role:
                continue
            try:
                await member.add_roles(role, reason=f"Hydrogen invite reward: {reward['invite_threshold']} invites")
            except discord.HTTPException:
                continue
            message = reward["dm_message"] or (
                f"{Emoji.success} You reached {reward['invite_threshold']} invites in {guild.name} "
                f"and received the {role.name} role!"
            )
            try:
                await member.send(
                    message.replace("{server}", guild.name)
                    .replace("{role}", role.name)
                    .replace("{invites}", str(total))
                )
            except discord.Forbidden:
                pass
            await audit(
                self.bot, guild, "Invite reward granted",
                f"{member} received {role.name} at {reward['invite_threshold']} invites."
            )

    @commands.hybrid_command(name="invites", aliases=["i"], description="Show tracked invites for a member")
    async def invites(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        row = await db.fetchone(
            "SELECT joins FROM invite_counts WHERE guild_id=? AND user_id=?",
            (ctx.guild.id, member.id)
        )
        total = row["joins"] if row else 0
        await ctx.reply(f"{Emoji.invite} {member.mention} has {total} tracked invites in this server.")

    @commands.hybrid_group(name="invite", invoke_without_command=True, description="Configure invite rewards")
    async def invite(self, ctx):
        await ctx.reply("Use the invite reward subcommand to configure a milestone, role, and optional DM message.")

    @invite.command(name="reward", description="Set a role and DM reward for an invite milestone")
    @commands.has_guild_permissions(manage_guild=True)
    async def invite_reward(self, ctx, invite_count: int, role: discord.Role, *, dm_message: str = None):
        if invite_count < 1 or invite_count > 10000:
            return await ctx.reply(f"{Emoji.warning} Invite count must be between 1 and 10000.")
        if role.is_default() or role.managed or role >= ctx.guild.me.top_role:
            return await ctx.reply(f"{Emoji.error} Choose a normal role below the bot's highest role.")
        if dm_message and len(dm_message) > 1500:
            return await ctx.reply(f"{Emoji.warning} DM message must be 1500 characters or fewer.")
        await db.execute(
            """INSERT INTO invite_rewards(guild_id,invite_threshold,role_id,dm_message)
               VALUES(?,?,?,?)
               ON CONFLICT(guild_id,invite_threshold) DO UPDATE SET
                 role_id=excluded.role_id, dm_message=excluded.dm_message""",
            (ctx.guild.id, invite_count, role.id, dm_message)
        )
        await ctx.reply(
            f"{Emoji.success} At {invite_count} invites, members receive {role.mention}."
            + (f" DM template saved: {dm_message}" if dm_message else " A default reward DM will be sent.")
        )

    @invite.command(name="rewards", description="List configured invite rewards")
    async def invite_rewards(self, ctx):
        rows = await db.fetchall(
            "SELECT invite_threshold,role_id FROM invite_rewards WHERE guild_id=? ORDER BY invite_threshold",
            (ctx.guild.id,)
        )
        if not rows:
            return await ctx.reply("No invite rewards configured for this server yet.")
        lines = []
        for row in rows:
            role = ctx.guild.get_role(row["role_id"])
            lines.append(f"{row['invite_threshold']} invites -> {role.mention if role else 'deleted role'}")
        await ctx.reply(f"{Emoji.invite} Invite rewards for {ctx.guild.name}\n" + "\n".join(lines))

    @invite.command(name="remove", description="Remove an invite reward milestone")
    @commands.has_guild_permissions(manage_guild=True)
    async def invite_reward_remove(self, ctx, invite_count: int):
        await db.execute(
            "DELETE FROM invite_rewards WHERE guild_id=? AND invite_threshold=?",
            (ctx.guild.id, invite_count)
        )
        await ctx.reply(f"{Emoji.success} Removed the {invite_count}-invite reward if it existed.")


async def setup(bot):
    await bot.add_cog(InviteRewards(bot))

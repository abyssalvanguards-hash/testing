import discord, random
from discord.ext import commands
from core import db
from core.logging import audit

class Community(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or len(message.content.strip()) < 3:
            return
        if random.random() > 0.18:
            return
        row = await db.fetchone(
            "SELECT xp,level FROM xp WHERE guild_id=? AND user_id=?",
            (message.guild.id, message.author.id)
        )
        xp = (row["xp"] if row else 0) + random.randint(8, 15)
        level = int((xp / 100) ** 0.5)
        old = row["level"] if row else 0
        await db.execute(
            """INSERT INTO xp(guild_id,user_id,xp,level) VALUES(?,?,?,?)
               ON CONFLICT(guild_id,user_id) DO UPDATE SET xp=excluded.xp,level=excluded.level""",
            (message.guild.id, message.author.id, xp, level)
        )
        if level > old:
            await message.channel.send(f"🎉 {message.author.mention} reached level **{level}**!")
            await audit(self.bot, message.guild, "Level up", f"{message.author} reached level {level}")

    @commands.hybrid_command(name="rank")
    async def rank(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        row = await db.fetchone(
            "SELECT xp,level FROM xp WHERE guild_id=? AND user_id=?",
            (ctx.guild.id, member.id)
        )
        await ctx.reply(
            f"**{member.display_name}** • Level {row['level'] if row else 0} • XP {row['xp'] if row else 0}"
        )

    @commands.hybrid_command(name="leaderboard")
    async def leaderboard(self, ctx):
        rows = await db.fetchall(
            "SELECT user_id,xp,level FROM xp WHERE guild_id=? ORDER BY xp DESC LIMIT 10",
            (ctx.guild.id,)
        )
        lines = []
        for i, row in enumerate(rows, 1):
            member = ctx.guild.get_member(row["user_id"])
            lines.append(
                f"{i}. {member.mention if member else row['user_id']} — L{row['level']} ({row['xp']} XP)"
            )
        await ctx.reply("\n".join(lines) or "No XP data yet.")

    @commands.hybrid_command(name="setwelcome")
    async def setwelcome(self, ctx, channel: discord.TextChannel):
        await db.set_setting(ctx.guild.id, "welcome_channel_id", channel.id)
        await ctx.reply(f"Welcome channel: {channel.mention}")

    @commands.hybrid_command(name="setgoodbye")
    async def setgoodbye(self, ctx, channel: discord.TextChannel):
        await db.set_setting(ctx.guild.id, "goodbye_channel_id", channel.id)
        await ctx.reply(f"Goodbye channel: {channel.mention}")


async def setup(bot):
    await bot.add_cog(Community(bot))

import discord, re, time
from collections import defaultdict, deque
from discord.ext import commands
from datetime import timedelta
from core import db
from core.logging import audit
from core.security import is_bot_admin

DURATION=re.compile(r"^(\d+)(s|m|h|d)$",re.I)
def parse_duration(value):
    m=DURATION.match(value)
    if not m: raise ValueError("Use 30s, 10m, 2h, or 1d.")
    n=int(m.group(1)); unit=m.group(2).lower()
    return timedelta(seconds=n*{"s":1,"m":60,"h":3600,"d":86400}[unit])

class Moderation(commands.Cog):
    def __init__(self,bot):
        self.bot=bot; self.recent=defaultdict(deque); self.last_text=defaultdict(lambda:"")
    async def _moderator(self,ctx):
        return await is_bot_admin(ctx.author,self.bot.owner_ids)
    @commands.hybrid_command()
    async def timeout(self,ctx,member:discord.Member,duration,*,reason="No reason provided"):
        if not await self._moderator(ctx): return await ctx.reply("Permission denied.")
        try: delta=parse_duration(duration)
        except ValueError as e: return await ctx.reply(str(e))
        if delta.total_seconds()>28*86400: return await ctx.reply("Discord timeout maximum is 28 days.")
        if member.top_role>=ctx.author.top_role and ctx.author.id!=ctx.guild.owner_id: return await ctx.reply("Role hierarchy prevents this action.")
        await member.timeout(delta,reason=reason)
        await audit(self.bot,ctx.guild,"Member timed out",f"{member.mention} • {duration} • {reason}",ctx.author)
        await ctx.reply(f"Timed out {member.mention} for {duration}.")
    @commands.hybrid_command()
    async def warn(self,ctx,member:discord.Member,*,reason="No reason provided"):
        if not await self._moderator(ctx): return await ctx.reply("Permission denied.")
        await audit(self.bot,ctx.guild,"Member warned",f"{member.mention} • {reason}",ctx.author)
        try: await member.send(f"You were warned in {ctx.guild.name}: {reason}")
        except discord.HTTPException: pass
        await ctx.reply(f"Warned {member.mention}.")
    @commands.Cog.listener()
    async def on_message(self,message):
        if message.author.bot or not message.guild: return
        key=(message.guild.id,message.author.id); now=time.monotonic(); q=self.recent[key]
        q.append(now)
        while q and now-q[0]>8: q.popleft()
        content=message.content.lower().strip()
        repeated=bool(content) and content==self.last_text[key]
        self.last_text[key]=content
        if len(q)>=7 or (repeated and len(q)>=4):
            if message.author.guild_permissions.manage_messages: return
            try:
                await message.author.timeout(timedelta(seconds=60),reason="WardenX Manager AutoMod: spam threshold")
                await message.channel.send(f"{message.author.mention} was timed out for spam. If this is a mistake, contact staff.",delete_after=8)
                await audit(self.bot,message.guild,"AutoMod timeout",f"Spam threshold triggered in {message.channel.mention}",message.author)
                q.clear()
            except (discord.Forbidden,discord.HTTPException):
                await audit(self.bot,message.guild,"AutoMod action failed",f"Could not timeout {message.author} in {message.channel.mention}")
    @commands.Cog.listener()
    async def on_message_delete(self,message):
        if message.guild and not message.author.bot:
            await audit(self.bot,message.guild,"Message deleted",f"Author: {message.author} • Channel: {message.channel.mention}\nContent: {(message.content or '[no cached text]')[:1200]}")
    @commands.Cog.listener()
    async def on_message_edit(self,before,after):
        if before.guild and not before.author.bot and before.content!=after.content:
            await audit(self.bot,before.guild,"Message edited",f"Author: {before.author} • Channel: {before.channel.mention}\nBefore: {before.content[:900]}\nAfter: {after.content[:900]}")
    @commands.Cog.listener()
    async def on_member_join(self,member):
        channel_id=await db.get_setting(member.guild.id,"welcome_channel_id")
        ch=member.guild.get_channel(channel_id) if channel_id else None
        if ch: await ch.send(f"Welcome {member.mention} to **{member.guild.name}**!")
        await audit(self.bot,member.guild,"Member joined",str(member))
    @commands.Cog.listener()
    async def on_member_remove(self,member):
        channel_id=await db.get_setting(member.guild.id,"goodbye_channel_id")
        ch=member.guild.get_channel(channel_id) if channel_id else None
        if ch: await ch.send(f"**{member}** left the server.")
        await audit(self.bot,member.guild,"Member left",str(member))
    @commands.Cog.listener()
    async def on_member_update(self,before,after):
        if before.roles!=after.roles:
            await audit(self.bot,after.guild,"Member roles updated",f"{after.mention}: {', '.join(r.name for r in after.roles)}")
async def setup(bot): await bot.add_cog(Moderation(bot))

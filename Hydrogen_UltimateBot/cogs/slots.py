import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from core import db
from core.security import is_bot_admin, safe_name
from core.logging import audit

class Slots(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.hybrid_command(name="createslot")
    async def create_slot(self,ctx,member:discord.Member,ping_limit:int=5,*,name=None):
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        if not 1<=ping_limit<=100: return await ctx.reply("Ping limit must be 1–100.")
        channel=await ctx.guild.create_text_channel(name or f"{safe_name(member.display_name)}-slot")
        await channel.set_permissions(ctx.guild.default_role,view_channel=True,send_messages=True)
        await channel.set_permissions(member,view_channel=True,send_messages=True,manage_messages=True)
        await channel.set_permissions(ctx.guild.me,view_channel=True,send_messages=True,manage_channels=True)
        await db.execute("INSERT INTO slots(guild_id,channel_id,owner_id,ping_limit,reset_at) VALUES(?,?,?,?,?)",
            (ctx.guild.id,channel.id,member.id,ping_limit,datetime.now(timezone.utc).isoformat()))
        await audit(self.bot,ctx.guild,"Slot created",f"{channel.mention} for {member.mention}",ctx.author)
        await ctx.reply(f"Created {channel.mention} for {member.mention}.")
    @commands.hybrid_command(name="slotinfo")
    async def info(self,ctx):
        row=await db.fetchone("SELECT * FROM slots WHERE channel_id=? AND active=1",(ctx.channel.id,))
        if not row: return await ctx.reply("This channel is not a registered slot.")
        await ctx.reply(f"Owner: <@{row['owner_id']}> • Pings: {row['ping_count']}/{row['ping_limit']}")
    @commands.Cog.listener()
    async def on_message(self,message):
        if not message.guild or message.author.bot: return
        row=await db.fetchone("SELECT * FROM slots WHERE channel_id=? AND active=1",(message.channel.id,))
        if not row or message.author.id!=row["owner_id"]: return
        if message.mentions:
            count=row["ping_count"]+len(message.mentions)
            if count>row["ping_limit"]:
                try: await message.delete()
                except discord.HTTPException: pass
                await message.channel.send(f"{message.author.mention}, slot ping limit reached ({row['ping_limit']}).",delete_after=8)
            else:
                await db.execute("UPDATE slots SET ping_count=? WHERE id=?",(count,row["id"]))
    @commands.hybrid_command(name="slotreset")
    async def reset(self,ctx,channel:discord.TextChannel=None):
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        channel=channel or ctx.channel
        await db.execute("UPDATE slots SET ping_count=0,reset_at=? WHERE channel_id=?",(datetime.now(timezone.utc).isoformat(),channel.id))
        await ctx.reply(f"Ping count reset for {channel.mention}.")
async def setup(bot): await bot.add_cog(Slots(bot))

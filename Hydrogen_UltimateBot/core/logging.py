from datetime import datetime, timezone
import discord
from core import db

async def audit(bot, guild, event, details="", actor=None):
    gid = guild.id if guild else None
    actor_id = actor.id if actor else None
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO audit_events(guild_id,actor_id,event,details,created_at) VALUES(?,?,?,?,?)",
        (gid, actor_id, event, str(details)[:3500], now)
    )
    if not guild:
        return
    channel_id = await db.get_setting(guild.id, "log_channel_id")
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel:
        embed = discord.Embed(
            title=f"Audit • {event[:240]}",
            description=str(details)[:4000] or "No additional details",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Actor", value=actor.mention if actor else "System", inline=True)
        embed.set_footer(text="WardenX Manager • Audit")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

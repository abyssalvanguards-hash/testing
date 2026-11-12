"""Non-destructive setup guide. Server layout is managed only by /create server."""
import discord
from discord.ext import commands
from emojis import Emoji


class SetupWizard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name="setup",
        description="Show Hydrogen setup instructions without creating extra channels or roles"
    )
    async def setup(self, ctx):
        if not ctx.guild:
            return await ctx.reply("Use this command inside a server.")
        if ctx.author.id != ctx.guild.owner_id and ctx.author.id not in self.bot.owner_ids:
            return await ctx.reply("Only the server owner or configured Hydrogen owner can use this command.")

        embed = discord.Embed(
            title=f"{Emoji.settings} Hydrogen Setup Guide",
            description=(
                "This guide is read-only. It does not create, rename, delete, or modify roles, "
                "channels, or permission overwrites."
            ),
            color=discord.Color.blurple()
        )
        embed.add_field(
            name="Build the requested layout",
            value="Run **/create server** or **$create server** after configuring this server's IDs.",
            inline=False
        )
        embed.add_field(
            name="Verification",
            value=(
                "Hydrogen uses your existing verification bot, Verified role, and verification channel. "
                "Set their IDs plus the protected channel IDs in data/channel_access.json. "
                "The setup guide will not create a second verification system."
            ),
            inline=False
        )
        embed.add_field(
            name="Tickets",
            value="Ticket panel creation remains manual, as requested.",
            inline=False
        )
        embed.add_field(
            name="Safety",
            value=(
                "The server builder creates only the configured template items that are missing. "
                "Review the preview and permissions in your test server before using it elsewhere."
            ),
            inline=False
        )
        await ctx.reply(embed=embed)


async def setup(bot):
    await bot.add_cog(SetupWizard(bot))

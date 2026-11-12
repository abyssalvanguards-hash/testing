import discord
from discord.ext import commands
from discord.ui import View
from core import db
from core.logging import audit
from core.security import is_bot_admin
from emojis import Emoji

ROLE_NAMES = ["Verified", "Member", "Free Slot Owner", "Premium Slot Owner"]
LOG_NAMES = ["logs-audit", "logs-moderation", "logs-members", "logs-messages", "logs-security"]


class ConfirmSetup(View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot

    @discord.ui.button(label="Apply approved setup", style=discord.ButtonStyle.success)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message("Run this in a server.", ephemeral=True)
        if interaction.user.id != interaction.guild.owner_id and not await is_bot_admin(interaction.user, self.bot.owner_ids):
            return await interaction.response.send_message("Permission denied.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        created_roles, created_channels = [], []

        # Additive-only: existing roles/channels are never renamed, deleted,
        # recreated, or have their existing permission overwrites replaced.
        roles = {}
        for name in ROLE_NAMES:
            role = discord.utils.get(guild.roles, name=name)
            if role is None:
                role = await guild.create_role(name=name, reason="Hydrogen guided setup")
                created_roles.append(name)
            roles[name] = role

        category = discord.utils.get(guild.categories, name="Hydrogen Logs")
        if category is None:
            category = await guild.create_category("Hydrogen Logs", reason="Hydrogen guided setup")
            created_channels.append("Hydrogen Logs")
        for name in LOG_NAMES:
            channel = discord.utils.get(category.text_channels, name=name)
            if channel is None:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                }
                channel = await guild.create_text_channel(name, category=category, overwrites=overwrites)
                created_channels.append(name)
            if name == "logs-audit":
                await db.set_setting(guild.id, "log_channel_id", channel.id)

        community_category = discord.utils.get(guild.categories, name="Hydrogen Community")
        if community_category is None:
            community_category = await guild.create_category("Hydrogen Community", reason="Hydrogen guided setup")
            created_channels.append("Hydrogen Community")
        verify = discord.utils.get(community_category.text_channels, name="verify-here")
        if verify is None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
                roles["Verified"]: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            }
            verify = await guild.create_text_channel("verify-here", category=community_category, overwrites=overwrites)
            created_channels.append("verify-here")
        await db.set_setting(guild.id, "verified_role_id", roles["Verified"].id)
        await audit(self.bot, guild, "Guided setup applied",
                    f"Created roles: {created_roles}; created channels: {created_channels}", interaction.user)
        await interaction.followup.send(
            f"{Emoji.success} Setup applied.\nNew roles: {', '.join(created_roles) or 'none'}\n"
            f"New channels/categories: {', '.join(created_channels) or 'none'}\n"
            "Existing roles, channels, and permission overwrites were left unchanged. "
            "The verify channel is ready, but this wizard does not bulk-hide existing server channels "
            "or automatically assign the Verified role.",
            ephemeral=True
        )
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Setup cancelled.", view=None)


class SetupWizard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="setup", description="Preview and apply additive Hydrogen server setup")
    async def setup(self, ctx):
        if not ctx.guild:
            return await ctx.reply("Use this command inside a server.")
        if not await is_bot_admin(ctx.author, self.bot.owner_ids):
            return await ctx.reply("Permission denied.")
        embed = discord.Embed(
            title=f"{Emoji.settings} Hydrogen Setup Preview",
            description="This setup only creates missing items. It does not delete, rename, or overwrite existing roles, channels, or permission settings.",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Roles to ensure", value="\n".join(f"• {name}" for name in ROLE_NAMES), inline=False)
        embed.add_field(name="Private logging", value="Hydrogen Logs with audit, moderation, members, messages, and security channels.", inline=False)
        embed.add_field(name="Verification entry", value="Hydrogen Community / #verify-here. Everyone can view; only the Verified role and bot can send messages.", inline=False)
        embed.add_field(name="Important", value="Existing channels will not be hidden automatically, and this setup does not assign the Verified role. Review your server's access rules separately.", inline=False)
        await ctx.reply(embed=embed, view=ConfirmSetup(self.bot))


async def setup(bot):
    await bot.add_cog(SetupWizard(bot))

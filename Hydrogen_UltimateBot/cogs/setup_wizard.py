import discord
from discord.ext import commands
from discord.ui import View, Button
from core import db
from core.logging import audit
from core.security import is_bot_admin

class ConfirmSetup(View):
    def __init__(self,bot,guild_id,actions):
        super().__init__(timeout=120); self.bot=bot; self.guild_id=guild_id; self.actions=actions
    @discord.ui.button(label="Apply approved setup",style=discord.ButtonStyle.success)
    async def apply(self,interaction,button):
        if interaction.user.id!=interaction.guild.owner_id and not await is_bot_admin(interaction.user,self.bot.owner_ids):
            return await interaction.response.send_message("Permission denied.",ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        created=[]
        # Non-destructive: only create missing log channels; never delete/recreate existing channels.
        category=discord.utils.get(interaction.guild.categories,name="Hydrogen Logs")
        if not category: category=await interaction.guild.create_category("Hydrogen Logs")
        for name in ["logs-audit","logs-moderation","logs-members","logs-messages","logs-security"]:
            ch=discord.utils.get(category.text_channels,name=name)
            if not ch:
                ow={interaction.guild.default_role:discord.PermissionOverwrite(view_channel=False),
                    interaction.guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)}
                ch=await interaction.guild.create_text_channel(name,category=category,overwrites=ow); created.append(name)
            if name=="logs-audit": await db.set_setting(interaction.guild.id,"log_channel_id",ch.id)
        await audit(self.bot,interaction.guild,"Guided setup applied",f"Created {created}",interaction.user)
        await interaction.followup.send("Setup applied. Existing channels were not deleted or overwritten.",ephemeral=True)
        for child in self.children: child.disabled=True
        await interaction.message.edit(view=self)
    @discord.ui.button(label="Cancel",style=discord.ButtonStyle.danger)
    async def cancel(self,interaction,button):
        await interaction.response.edit_message(content="Setup cancelled.",view=None)

class SetupWizard(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.hybrid_command(name="setup")
    async def setup(self,ctx):
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        embed=discord.Embed(title="Hydrogen Setup Preview",description="This wizard will create missing private log channels and set the audit destination. It will not delete, rename, or bulk-overwrite existing channels.",color=discord.Color.blurple())
        embed.add_field(name="Channels",value="logs-audit\nlogs-moderation\nlogs-members\nlogs-messages\nlogs-security",inline=False)
        embed.add_field(name="Safety",value="Explicit confirmation required. Excluded categories and existing channels are left untouched.",inline=False)
        await ctx.reply(embed=embed,view=ConfirmSetup(self.bot,ctx.guild.id,[]))
async def setup(bot): await bot.add_cog(SetupWizard(bot))

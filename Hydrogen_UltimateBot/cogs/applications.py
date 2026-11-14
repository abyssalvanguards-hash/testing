import discord, json
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
from datetime import datetime, timezone
from core import db
from core.logging import audit
from core.security import is_bot_admin
from core.ai import AIUnavailable

class StaffApplicationModal(Modal,title="Hydrogen Staff Application"):
    experience=TextInput(label="Previous experience",style=discord.TextStyle.paragraph,max_length=900)
    skills=TextInput(label="Skills you can contribute",style=discord.TextStyle.paragraph,max_length=700)
    availability=TextInput(label="Weekly availability / timezone",max_length=300)
    scenario=TextInput(label="How would you handle a heated argument?",style=discord.TextStyle.paragraph,max_length=900)
    motivation=TextInput(label="Why do you want to join staff?",style=discord.TextStyle.paragraph,max_length=700)
    def __init__(self,bot): super().__init__(); self.bot=bot
    async def on_submit(self,interaction):
        answers={k:getattr(self,k).value for k in ["experience","skills","availability","scenario","motivation"]}
        await db.execute("INSERT INTO applications(guild_id,user_id,username,answers,created_at) VALUES(?,?,?,?,?)",
            (interaction.guild.id,interaction.user.id,str(interaction.user),json.dumps(answers),datetime.now(timezone.utc).isoformat()))
        app=await db.fetchone("SELECT id FROM applications WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 1",(interaction.guild.id,interaction.user.id))
        review_id=await db.get_setting(interaction.guild.id,"review_channel_id")
        channel=interaction.guild.get_channel(review_id) if review_id else None
        if not channel: return await interaction.response.send_message("Application saved, but review channel is not configured. Contact staff.",ephemeral=True)
        summary="AI review not configured."
        recommendation="manual_review"
        if self.bot.ai:
            try:
                summary=await self.bot.ai.ask("Summarize this staff application neutrally. Do not infer protected traits. Return strengths, gaps, and follow-up questions. Do not make the final decision.\n"+json.dumps(answers),interaction.guild.id,interaction.user.id,"application")
                recommendation="AI advisory only; owner decision required."
            except AIUnavailable as exc:
                summary=f"AI unavailable; manual review required: {exc}"
        await db.execute("UPDATE applications SET ai_summary=?,ai_recommendation=? WHERE id=?",(summary,recommendation,app["id"]))
        embed=discord.Embed(title=f"Staff Application #{app['id']}",description=f"Applicant: {interaction.user.mention}",color=discord.Color.blurple())
        for k,v in answers.items(): embed.add_field(name=k.title(),value=v[:1024],inline=False)
        embed.add_field(name="AI advisory (not a decision)",value=summary[:1024],inline=False)
        embed.set_footer(text="Only the server owner can approve or reject.")
        await channel.send(embed=embed,view=ApplicationReview(self.bot,app["id"]))
        await interaction.response.send_message("Application submitted. The owner will review it.",ephemeral=True)
        await audit(self.bot,interaction.guild,"Staff application submitted",f"Application #{app['id']}",interaction.user)

class ApplyPanel(View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Apply for Staff",style=discord.ButtonStyle.primary,custom_id="hydro:application:open")
    async def apply(self,interaction,button):
        await interaction.response.send_modal(StaffApplicationModal(interaction.client))

class ApplicationReview(View):
    def __init__(self,bot,app_id):
        super().__init__(timeout=None); self.bot=bot; self.app_id=app_id
        for label,style,decision in [("Approve",discord.ButtonStyle.success,"approved"),("Reject",discord.ButtonStyle.danger,"rejected")]:
            b=Button(label=label,style=style,custom_id=f"hydro:application:{decision}:{app_id}")
            b.callback=self.make_callback(decision); self.add_item(b)
    def make_callback(self,decision):
        async def cb(interaction):
            if interaction.user.id!=interaction.guild.owner_id and interaction.user.id not in self.bot.owner_ids:
                return await interaction.response.send_message("Only the server owner or configured bot owner can decide.",ephemeral=True)
            app=await db.fetchone("SELECT * FROM applications WHERE id=? AND guild_id=?",(self.app_id,interaction.guild.id))
            if not app or app["status"]!="pending": return await interaction.response.send_message("Already reviewed or missing.",ephemeral=True)
            await db.execute("UPDATE applications SET status=?,reviewer_id=? WHERE id=?",(decision,interaction.user.id,self.app_id))
            result="Decision saved."
            if decision=="approved":
                role_id=await db.get_setting(interaction.guild.id,"staff_role_id")
                role=interaction.guild.get_role(int(role_id)) if role_id else None
                member=interaction.guild.get_member(app["user_id"])
                if member is None:
                    try:
                        member=await interaction.guild.fetch_member(int(app["user_id"]))
                    except discord.NotFound:
                        result="Approved, but the applicant is no longer in this server; no staff role was assigned."
                    except discord.HTTPException:
                        result="Approved, but I could not retrieve the applicant. Check the bot's Server Members Intent and try again."
                if role is None:
                    result="Approved, but no valid staff role is configured. Run /setstaffrole and select the staff role."
                elif member is not None:
                    bot_member=interaction.guild.me
                    if not interaction.guild.me.guild_permissions.manage_roles:
                        result="Approved, but I need the Manage Roles permission to assign the staff role."
                    elif role.managed or role >= bot_member.top_role:
                        result="Approved, but the staff role is above or equal to my highest role. Move my bot role above it in Server Settings → Roles."
                    else:
                        try:
                            await member.add_roles(role,reason=f"Application #{self.app_id} approved by owner")
                            result=f"Approved and assigned {role.mention}."
                        except discord.Forbidden:
                            result="Approved, but Discord denied role assignment. Check Manage Roles permission and role hierarchy."
                        except discord.HTTPException as exc:
                            result=f"Approved, but Discord could not assign the role ({exc}). Check bot permissions and try again."
            await interaction.response.send_message(result,ephemeral=True)
            for item in self.children: item.disabled=True
            await interaction.message.edit(view=self)
            await audit(self.bot,interaction.guild,f"Application {decision}",f"Application #{self.app_id}",interaction.user)
        return cb

class Applications(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.hybrid_command(name="applicationpanel")
    async def panel(self,ctx):
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        await ctx.send(embed=discord.Embed(title="Staff Applications",description="Apply using the button below. The owner makes the final decision.",color=discord.Color.blurple()),view=ApplyPanel())
    @commands.hybrid_command(name="setreview")
    async def set_review(self,ctx,channel:discord.TextChannel):
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        await db.set_setting(ctx.guild.id,"review_channel_id",channel.id)
        await ctx.reply(f"Application review channel set to {channel.mention}.")

    @commands.hybrid_command(name="setstaffrole")
    async def set_staff_role(self,ctx,role:discord.Role):
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        bot_member=ctx.guild.me
        if role.managed or role >= bot_member.top_role:
            return await ctx.reply("I cannot assign that role. Move my bot role above the selected staff role, then run this command again.")
        if not bot_member.guild_permissions.manage_roles:
            return await ctx.reply("I need the Manage Roles permission before a staff role can be configured.")
        await db.set_setting(ctx.guild.id,"staff_role_id",role.id)
        await ctx.reply(f"Staff application role set to {role.mention}.")
async def setup(bot):
    await bot.add_cog(Applications(bot)); bot.add_view(ApplyPanel())

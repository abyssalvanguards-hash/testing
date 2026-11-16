import discord
import json
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput, RoleSelect
from datetime import datetime, timezone

from core import db
from core.logging import audit
from core.security import is_bot_admin
from core.ai import AIUnavailable


def _safe_role_candidates(guild):
    """Roles the bot can safely assign; exclude privileged and managed roles."""
    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        return []
    blocked = (
        "administrator", "manage_guild", "manage_roles", "manage_channels",
        "manage_webhooks", "ban_members", "kick_members", "moderate_members",
        "manage_messages", "mention_everyone"
    )
    candidates = []
    for role in guild.roles:
        if role.is_default() or role.managed or role >= bot_member.top_role:
            continue
        if any(getattr(role.permissions, permission, False) for permission in blocked):
            continue
        candidates.append(role)
    return candidates


def _role_from_recommendation(guild, recommendation):
    if not recommendation:
        return None
    # The AI can only recommend a role that exists in the validated candidate list.
    candidates = _safe_role_candidates(guild)
    normalized = str(recommendation).strip().casefold()
    return next((role for role in candidates if role.name.casefold() == normalized), None)


class StaffApplicationModal(Modal, title="WardenX Staff Application"):
    experience = TextInput(label="Previous experience", style=discord.TextStyle.paragraph, max_length=900)
    skills = TextInput(label="Skills you can contribute", style=discord.TextStyle.paragraph, max_length=700)
    availability = TextInput(label="Weekly availability / timezone", max_length=300)
    scenario = TextInput(label="How would you handle a heated argument?", style=discord.TextStyle.paragraph, max_length=900)
    motivation = TextInput(label="Why do you want to join staff?", style=discord.TextStyle.paragraph, max_length=700)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction):
        answers = {key: getattr(self, key).value for key in (
            "experience", "skills", "availability", "scenario", "motivation"
        )}
        await db.execute(
            "INSERT INTO applications(guild_id,user_id,username,answers,created_at) VALUES(?,?,?,?,?)",
            (interaction.guild.id, interaction.user.id, str(interaction.user),
             json.dumps(answers), datetime.now(timezone.utc).isoformat())
        )
        app = await db.fetchone(
            "SELECT id FROM applications WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
            (interaction.guild.id, interaction.user.id)
        )
        review_id = await db.get_setting(interaction.guild.id, "review_channel_id")
        channel = interaction.guild.get_channel(review_id) if review_id else None
        if not channel:
            return await interaction.response.send_message(
                "Application saved, but the review channel is not configured. Contact staff.",
                ephemeral=True
            )

        summary = "AI review unavailable; please review manually."
        recommended_role = ""
        candidates = _safe_role_candidates(interaction.guild)
        if self.bot.ai:
            try:
                summary = await self.bot.ai.ask(
                    "Review this staff application neutrally. Do not infer protected traits. "
                    "Return concise strengths, gaps, and follow-up questions. Do not make a decision.\n"
                    + json.dumps(answers),
                    interaction.guild.id, interaction.user.id, "application"
                )
                if candidates:
                    role_list = "\n".join(
                        f"- {role.name}: {role.permissions.value}" for role in candidates
                    )
                    role_result = await self.bot.ai.ask(
                        "Recommend exactly one suitable role name from the allowed list for this applicant. "
                        "Choose the least-privileged suitable role. Never recommend administrator, moderation, "
                        "or management privileges. If none fits, answer NONE. Return only the exact role name.\n"
                        f"Allowed roles:\n{role_list}\nApplication:\n{json.dumps(answers)}",
                        interaction.guild.id, interaction.user.id, "application_role_advice"
                    )
                    recommended_role = role_result.strip().strip("`'\"")
                    if recommended_role.casefold() == "none" or not _role_from_recommendation(
                        interaction.guild, recommended_role
                    ):
                        recommended_role = ""
            except AIUnavailable:
                pass

        advisory = summary[:850]
        advisory += f"\n\nSuggested role: {recommended_role or 'No safe recommendation; owner can choose manually.'}"
        await db.execute(
            "UPDATE applications SET ai_summary=?,ai_recommendation=? WHERE id=?",
            (summary, recommended_role or "manual_review", app["id"])
        )

        embed = discord.Embed(
            title=f"Staff Application #{app['id']}",
            description=f"Applicant: {interaction.user.mention}",
            color=discord.Color.blurple()
        )
        for key, value in answers.items():
            embed.add_field(name=key.title(), value=value[:1024], inline=False)
        embed.add_field(name="AI advisory (not a decision)", value=advisory[:1024], inline=False)
        embed.set_footer(text="Only the server owner or configured bot owner can decide.")
        await channel.send(
            embed=embed,
            view=ApplicationReview(self.bot, app["id"], recommended_role)
        )
        await interaction.response.send_message(
            "Application submitted. The owner will review it.", ephemeral=True
        )
        await audit(
            self.bot, interaction.guild, "Staff application submitted",
            f"Application #{app['id']}", interaction.user
        )


class ApplyPanel(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Apply for Staff", style=discord.ButtonStyle.primary,
        custom_id="hydro:application:open"
    )
    async def apply(self, interaction, button):
        await interaction.response.send_modal(StaffApplicationModal(interaction.client))


class AlternativeRoleView(View):
    def __init__(self, review, applicant_id):
        super().__init__(timeout=120)
        self.review = review
        self.applicant_id = applicant_id
        selector = RoleSelect(
            placeholder="Choose a safe role to assign",
            min_values=1, max_values=1
        )
        selector.callback = self.selected
        self.add_item(selector)

    async def selected(self, interaction):
        if not self.review.is_owner(interaction):
            return await interaction.response.send_message(
                "Only the server owner or configured bot owner can decide.", ephemeral=True
            )
        role = self.children[0].values[0]
        if role not in _safe_role_candidates(interaction.guild):
            return await interaction.response.send_message(
                "That role is not eligible for assignment. Choose a lower-privilege role below the bot's role.",
                ephemeral=True
            )
        await self.review._approve(interaction, role)


class ApplicationReview(View):
    def __init__(self, bot, app_id, recommended_role_name=""):
        super().__init__(timeout=None)
        self.bot = bot
        self.app_id = app_id
        self.recommended_role_name = recommended_role_name
        self.add_item(self._button("Approve recommended", discord.ButtonStyle.success, "recommend"))
        self.add_item(self._button("Approve with another role", discord.ButtonStyle.primary, "choose"))
        self.add_item(self._button("Reject", discord.ButtonStyle.danger, "reject"))

    def _button(self, label, style, action):
        button = Button(
            label=label, style=style,
            custom_id=f"hydro:application:{action}:{self.app_id}"
        )
        button.callback = self._callback(action)
        return button

    def is_owner(self, interaction):
        return (
            interaction.guild
            and (
                interaction.user.id == interaction.guild.owner_id
                or interaction.user.id in self.bot.owner_ids
            )
        )

    async def _get_pending(self, interaction):
        app = await db.fetchone(
            "SELECT * FROM applications WHERE id=? AND guild_id=?",
            (self.app_id, interaction.guild.id)
        )
        if not app or app["status"] != "pending":
            await interaction.response.send_message(
                "This application has already been reviewed or no longer exists.",
                ephemeral=True
            )
            return None
        return app

    async def _approve(self, interaction, role):
        app = await self._get_pending(interaction)
        if not app:
            return
        if role not in _safe_role_candidates(interaction.guild):
            return await interaction.response.send_message(
                "That role is no longer eligible. Check the bot's Manage Roles permission and role hierarchy.",
                ephemeral=True
            )
        member = interaction.guild.get_member(int(app["user_id"]))
        if member is None:
            try:
                member = await interaction.guild.fetch_member(int(app["user_id"]))
            except discord.NotFound:
                return await interaction.response.send_message(
                    "The applicant is no longer in this server; no role was assigned.", ephemeral=True
                )
            except discord.HTTPException:
                return await interaction.response.send_message(
                    "Could not retrieve the applicant. Check the Server Members Intent and try again.",
                    ephemeral=True
                )
        try:
            await member.add_roles(role, reason=f"Application #{self.app_id} approved by owner")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "Discord denied role assignment. Check Manage Roles permission and role hierarchy.",
                ephemeral=True
            )
        except discord.HTTPException:
            return await interaction.response.send_message(
                "Discord could not assign that role. No approval was recorded; try again later.",
                ephemeral=True
            )

        await db.execute(
            "UPDATE applications SET status='approved',reviewer_id=? WHERE id=? AND status='pending'",
            (interaction.user.id, self.app_id)
        )
        await interaction.response.send_message(
            f"Application approved. Assigned {role.mention} to {member.mention}.", ephemeral=True
        )
        await self._disable_message(interaction)
        await audit(
            self.bot, interaction.guild, "Application approved",
            f"Application #{self.app_id}; assigned role {role.name}", interaction.user
        )

    async def _reject(self, interaction):
        app = await self._get_pending(interaction)
        if not app:
            return
        await db.execute(
            "UPDATE applications SET status='rejected',reviewer_id=? WHERE id=? AND status='pending'",
            (interaction.user.id, self.app_id)
        )
        await interaction.response.send_message("Application rejected.", ephemeral=True)
        await self._disable_message(interaction)
        await audit(
            self.bot, interaction.guild, "Application rejected",
            f"Application #{self.app_id}", interaction.user
        )

    async def _disable_message(self, interaction):
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

    def _callback(self, action):
        async def callback(interaction):
            if not self.is_owner(interaction):
                return await interaction.response.send_message(
                    "Only the server owner or configured bot owner can decide.", ephemeral=True
                )
            if action == "reject":
                return await self._reject(interaction)
            if action == "choose":
                if not _safe_role_candidates(interaction.guild):
                    return await interaction.response.send_message(
                        "No safe assignable roles are available. Check Manage Roles and role hierarchy.",
                        ephemeral=True
                    )
                return await interaction.response.send_message(
                    "Choose the role to assign:", view=AlternativeRoleView(self, 0), ephemeral=True
                )
            role = _role_from_recommendation(interaction.guild, self.recommended_role_name)
            if role is None:
                return await interaction.response.send_message(
                    "There is no valid AI-recommended role. Use 'Approve with another role' to choose manually.",
                    ephemeral=True
                )
            return await self._approve(interaction, role)
        return callback


class Applications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="applicationpanel")
    async def panel(self, ctx):
        if not await is_bot_admin(ctx.author, self.bot.owner_ids):
            return await ctx.reply("Permission denied.")
        await ctx.send(
            embed=discord.Embed(
                title="Staff Applications",
                description="Apply using the button below. The owner makes the final decision.",
                color=discord.Color.blurple()
            ),
            view=ApplyPanel()
        )

    @commands.hybrid_command(name="setreview")
    async def set_review(self, ctx, channel: discord.TextChannel):
        if not await is_bot_admin(ctx.author, self.bot.owner_ids):
            return await ctx.reply("Permission denied.")
        await db.set_setting(ctx.guild.id, "review_channel_id", channel.id)
        await ctx.reply(f"Application review channel set to {channel.mention}.")


async def setup(bot):
    await bot.add_cog(Applications(bot))
    bot.add_view(ApplyPanel())

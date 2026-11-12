"""AI-assisted server builder with explicit, per-guild verification access rules."""
import json
from pathlib import Path

import discord
from discord.ext import commands

from core.ai import AIUnavailable
from core.security import is_server_owner_or_global
from core.logging import audit
from emojis import Emoji

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = BASE_DIR / "data" / "server_template.json"
ACCESS_PATH = BASE_DIR / "data" / "channel_access.json"
SAFE_ROLE_PERMISSIONS = {
    "send_messages", "read_message_history", "embed_links",
    "attach_files", "add_reactions", "manage_messages", "moderate_members",
    "manage_threads", "view_audit_log",
}
CONFIRMATION_REQUIRED_PERMISSIONS = {"administrator", "manage_roles", "manage_channels"}
CLASSIFICATION_ROLES = ("Hydrogen", "Bots", "Members")
SMALL_CAPS = str.maketrans({
    "a":"ᴀ","b":"ʙ","c":"ᴄ","d":"ᴅ","e":"ᴇ","f":"ꜰ","g":"ɢ","h":"ʜ",
    "i":"ɪ","j":"ᴊ","k":"ᴋ","l":"ʟ","m":"ᴍ","n":"ɴ","o":"ᴏ","p":"ᴘ",
    "q":"ǫ","r":"ʀ","s":"s","t":"ᴛ","u":"ᴜ","v":"ᴠ","w":"ᴡ","x":"x",
    "y":"ʏ","z":"ᴢ"
})


def channel_name(value):
    return ("▫-" + value.lower().translate(SMALL_CAPS).replace(" ", "-"))[:100]


def role_name(value):
    return ("W X・" + value.strip())[:100]


def category_name(value):
    return ("━━━「 " + value.upper().strip() + " 」━━━")[:100]


def snowflake(value):
    return int(value) if str(value or "").isdigit() else 0


class RolePermissionConfirmation(discord.ui.View):
    """Require the invoking server owner to approve Gemini's full role-permission plan."""

    def __init__(self, requester_id):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.approved = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the person who started this server build can confirm it.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Approve permissions", style=discord.ButtonStyle.danger)
    async def approve(self, interaction, button):
        self.approved = True
        await interaction.response.send_message(
            "Permission plan approved. Hydrogen will now create the missing roles.", ephemeral=True
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.approved = False
        await interaction.response.send_message(
            "Cancelled. No roles or server layout changes were made.", ephemeral=True
        )
        self.stop()


class ServerBuilder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _template(self):
        with TEMPLATE_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data.get("categories"), list) or not isinstance(data.get("roles"), list):
            raise ValueError("Server template is malformed.")
        return data

    async def _get_or_create_role(self, guild, base_name):
        """Return a neutral classification role, creating it when absent."""
        formatted = role_name(base_name)
        existing = discord.utils.get(guild.roles, name=formatted)
        if existing is not None:
            return existing
        return await guild.create_role(
            name=formatted,
            permissions=discord.Permissions.none(),
            reason="Hydrogen member and bot role classification",
        )

    async def _assign_classification(self, member):
        """Assign Hydrogen, Bots, or Members based on the joining account."""
        guild = member.guild
        if self.bot.user and member.id == self.bot.user.id:
            base_name = "Hydrogen"
        elif member.bot:
            base_name = "Bots"
        else:
            base_name = "Members"
        role = await self._get_or_create_role(guild, base_name)
        if role not in member.roles:
            await member.add_roles(role, reason="Hydrogen automatic member classification")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        try:
            await self._assign_classification(member)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await audit(
                self.bot, member.guild, "Automatic role assignment failed",
                f"Could not assign {member} to the correct classification role: {exc}", member
            )

    def _guild_access(self, guild_id):
        if not ACCESS_PATH.exists():
            return {}
        with ACCESS_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        guilds = data.get("guilds", {})
        return guilds.get(str(guild_id), {}) if isinstance(guilds, dict) else {}

    async def _ai_permissions(self, guild, role_names):
        prompt = (
            "Return ONLY JSON in this exact shape: "
            '{"role_permissions":{"Founder":[],"Moderator":["manage_messages"]}}. '
            "Choose least-privilege Discord role permissions for the listed role names. "
            "Allowed permission names only: " + ", ".join(sorted(SAFE_ROLE_PERMISSIONS | CONFIRMATION_REQUIRED_PERMISSIONS)) + ". "
            "You may propose administrator, manage_roles, and manage_channels when genuinely appropriate, "
            "but these three permissions require a separate explicit human confirmation before assignment. "
            "Never return manage_guild, manage_webhooks, mention_everyone, kick_members, ban_members, "
            "or any permission outside the allowed list. "
            "Do not invent roles. Roles: " + json.dumps(role_names, ensure_ascii=False) +
            ". Guild name: " + guild.name
        )
        raw = await self.bot.ai.ask(prompt, guild_id=guild.id, purpose="server_builder_permissions")
        manager = self.bot.get_cog("AIServerManager")
        if manager is None:
            raise ValueError("AI server manager cog is not loaded.")
        plan = manager._parse_json(raw)
        proposed = plan.get("role_permissions", {})
        if not isinstance(proposed, dict):
            raise ValueError("AI permission response did not contain role_permissions.")
        return {
            name: sorted(set(v for v in proposed.get(name, [])
                             if isinstance(v, str) and v in (SAFE_ROLE_PERMISSIONS | CONFIRMATION_REQUIRED_PERMISSIONS)))
            if isinstance(proposed.get(name, []), list) else []
            for name in role_names
        }

    async def _apply_verification_access(self, guild, config):
        """Apply only explicitly configured channel IDs; never create/replace verification."""
        verified_id = snowflake(config.get("verified_role_id"))
        verification_id = snowflake(config.get("verification_channel_id"))
        raw_protected = config.get("protected_channel_ids", [])
        if not verified_id or not verification_id:
            return False, "Verified role ID or verification channel ID is missing."
        if not isinstance(raw_protected, list):
            return False, "protected_channel_ids must be a JSON list."
        if any(not snowflake(value) for value in raw_protected):
            return False, "Every protected channel ID must be a numeric Discord ID."
        protected_ids = list(dict.fromkeys(snowflake(value) for value in raw_protected))
        verified = guild.get_role(verified_id)
        verification = guild.get_channel(verification_id)
        protected = [guild.get_channel(channel_id) for channel_id in protected_ids]
        if verified is None or verification is None:
            return False, "Configured Verified role or verification channel was not found in this server."
        if any(channel is None for channel in protected):
            return False, "At least one configured protected channel ID was not found; no verification overwrites were changed."
        if verification_id in protected_ids:
            protected = [channel for channel in protected if channel.id != verification_id]
        everyone = guild.default_role
        # Validate every target before changing any permission overwrites.
        await verification.set_permissions(everyone, view_channel=True,
                                            reason="Hydrogen verification-channel access")
        await verification.set_permissions(verified, view_channel=True,
                                            reason="Hydrogen Verified-role access")
        applied = 0
        for channel in protected:
            # Explicitly deny ordinary roles so an allow on another role cannot
            # accidentally override the @everyone deny for non-verified members.
            for role in guild.roles:
                if role.id == verified.id or role.is_default():
                    continue
                await channel.set_permissions(role, view_channel=False,
                                              reason="Hydrogen Verified-only channel access")
            await channel.set_permissions(everyone, view_channel=False,
                                          reason="Hydrogen Verified-only channel access")
            await channel.set_permissions(verified, view_channel=True,
                                          reason="Hydrogen Verified-only channel access")
            await channel.set_permissions(guild.me, view_channel=True,
                                          reason="Hydrogen bot access to protected channel")
            applied += 1
        return True, (f"Verification channel public; {applied} protected channels restricted to Verified. "
                      "Discord Administrator permissions bypass channel overwrites.")

    @commands.hybrid_group(name="create", invoke_without_command=True,
                            description="Create the configured server layout")
    async def create(self, ctx):
        await ctx.reply("Use /create server (or $create server) to build the configured layout.")

    @create.command(name="server", description="Create configured categories, channels, roles and access rules")
    async def create_server(self, ctx):
        if not ctx.guild:
            return await ctx.reply("This command can only be used inside a server.")
        if ctx.author.id != ctx.guild.owner_id and not await is_server_owner_or_global(ctx.author, self.bot.owner_ids):
            return await ctx.reply(f"{Emoji.error} Only the server owner or configured Hydrogen owner can run this.")
        me = ctx.guild.me
        if not me.guild_permissions.manage_channels or not me.guild_permissions.manage_roles:
            return await ctx.reply(f"{Emoji.error} Hydrogen needs Manage Channels and Manage Roles permissions.")
        await ctx.defer()
        try:
            template = self._template()
            role_permissions = await self._ai_permissions(ctx.guild, template["roles"])
            access_config = self._guild_access(ctx.guild.id)
            verified_id = snowflake(access_config.get("verified_role_id"))
            verification_id = snowflake(access_config.get("verification_channel_id"))
            if not verified_id or not verification_id:
                raise ValueError("Configure this guild's Verified role ID and verification channel ID in data/channel_access.json before running the builder.")
            if ctx.guild.get_role(verified_id) is None or ctx.guild.get_channel(verification_id) is None:
                raise ValueError("The configured Verified role or verification channel does not exist in this guild.")
            protected_ids = access_config.get("protected_channel_ids", [])
            if not isinstance(protected_ids, list) or any(not snowflake(value) for value in protected_ids):
                raise ValueError("protected_channel_ids must contain only numeric Discord channel IDs.")
            missing_ids = [str(value) for value in protected_ids if ctx.guild.get_channel(snowflake(value)) is None]
            if missing_ids:
                raise ValueError("Unknown protected channel IDs: " + ", ".join(missing_ids[:10]))
        except (OSError, ValueError, json.JSONDecodeError, AIUnavailable, AttributeError) as exc:
            return await ctx.send(f"{Emoji.error} Preflight failed; no server changes were made. {str(exc)[:250]}")

        proposed_lines = []
        privileged_proposals = []
        for base_name in template["roles"]:
            permissions = role_permissions.get(base_name, [])
            if permissions:
                proposed_lines.append(f"**{role_name(base_name)}:** {', '.join(permissions)}")
            if set(permissions) & CONFIRMATION_REQUIRED_PERMISSIONS:
                privileged_proposals.append((role_name(base_name),
                    sorted(set(permissions) & CONFIRMATION_REQUIRED_PERMISSIONS)))

        if privileged_proposals:
            lines = [
                "**Gemini proposed elevated permissions. Review the complete role plan below.**",
                "Administrator, Manage Roles, and Manage Channels will NOT be assigned unless you approve.",
                *proposed_lines
            ]
            description = "\n".join(lines)
            if len(description) > 4000:
                description = description[:3900] + "\n…(plan shortened; cancel and review the role template)"
            embed = discord.Embed(
                title="Confirm Gemini role permissions",
                description=description,
                color=discord.Color.orange()
            )
            embed.set_footer(text="Only the server owner / configured Hydrogen owner can approve. Expires in 120 seconds.")
            confirmation = RolePermissionConfirmation(ctx.author.id)
            confirmation_message = await ctx.send(embed=embed, view=confirmation)
            await confirmation.wait()
            try:
                await confirmation_message.edit(view=None)
            except discord.HTTPException:
                pass
            if not confirmation.approved:
                return await ctx.send(
                    f"{Emoji.error} Permission plan was not approved (cancelled or timed out). "
                    "No roles, categories, or channels were created."
                )

        made_roles, kept_roles, made_categories, made_channels, kept_channels = [], [], [], [], []
        for base_name in template["roles"]:
            formatted = role_name(base_name)
            if discord.utils.get(ctx.guild.roles, name=formatted):
                kept_roles.append(formatted)
                continue
            perms = discord.Permissions.none()
            for permission in role_permissions.get(base_name, []):
                setattr(perms, permission, True)
            try:
                await ctx.guild.create_role(name=formatted, permissions=perms,
                    reason=f"Hydrogen server builder requested by {ctx.author}")
                made_roles.append(formatted)
            except discord.HTTPException:
                continue

        # Create/reuse neutral classification roles, then classify every cached member.
        classification_roles = {}
        try:
            for base_name in CLASSIFICATION_ROLES:
                classification_roles[base_name] = await self._get_or_create_role(ctx.guild, base_name)

            # Force a full member list before assigning roles to every current member.
            await ctx.guild.chunk(cache=True)
            for member in ctx.guild.members:
                if self.bot.user and member.id == self.bot.user.id:
                    base_name = "Hydrogen"
                elif member.bot:
                    base_name = "Bots"
                else:
                    base_name = "Members"
                target_role = classification_roles[base_name]
                if target_role not in member.roles:
                    try:
                        await member.add_roles(
                            target_role,
                            reason="Hydrogen initial server member classification",
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        continue
        except (discord.Forbidden, discord.HTTPException) as exc:
            await ctx.send(
                f"{Emoji.warning} Server layout will continue, but classification-role setup had an error: {str(exc)[:160]}"
            )

        verified_role = ctx.guild.get_role(snowflake(access_config.get("verified_role_id")))
        for category_spec in template["categories"]:
            formatted_category = category_name(category_spec["name"])
            category = discord.utils.get(ctx.guild.categories, name=formatted_category)
            if category is None:
                # New builder categories are private by default, not accidentally public.
                overwrites = {
                    ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    me: discord.PermissionOverwrite(view_channel=True, manage_channels=True)
                }
                if verified_role:
                    overwrites[verified_role] = discord.PermissionOverwrite(view_channel=True)
                try:
                    category = await ctx.guild.create_category(
                        formatted_category, overwrites=overwrites,
                        reason=f"Hydrogen server builder requested by {ctx.author}")
                    made_categories.append(formatted_category)
                except discord.HTTPException:
                    continue
            for spec in category_spec["channels"]:
                name = channel_name(spec["name"])
                channels = category.voice_channels if spec["type"] == "voice" else category.text_channels
                if discord.utils.get(channels, name=name):
                    kept_channels.append(name)
                    continue
                try:
                    if spec["type"] == "voice":
                        await ctx.guild.create_voice_channel(name, category=category,
                            overwrites={ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                                        me: discord.PermissionOverwrite(view_channel=True),
                                        **({verified_role: discord.PermissionOverwrite(view_channel=True)} if verified_role else {})},
                            reason=f"Hydrogen server builder requested by {ctx.author}")
                    else:
                        await ctx.guild.create_text_channel(name, category=category,
                            overwrites={ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                                        me: discord.PermissionOverwrite(view_channel=True),
                                        **({verified_role: discord.PermissionOverwrite(view_channel=True)} if verified_role else {})},
                            reason=f"Hydrogen server builder requested by {ctx.author}")
                    made_channels.append(name)
                except discord.HTTPException:
                    continue

        access_ok = False
        access_note = "Verification access not applied: configure data/channel_access.json for this guild."
        if access_config:
            try:
                access_ok, access_note = await self._apply_verification_access(ctx.guild, access_config)
            except (discord.Forbidden, discord.HTTPException, TypeError, ValueError) as exc:
                access_note = f"Verification access update failed: {str(exc)[:180]}"

        await audit(self.bot, ctx.guild, "Hydrogen server builder completed",
            f"Created {len(made_roles)} roles, {len(made_categories)} categories, "
            f"{len(made_channels)} channels; verification access applied={access_ok}.", ctx.author)
        await ctx.send(
            f"{Emoji.success} **Server builder finished for {ctx.guild.name}.**\n"
            f"Roles created: {len(made_roles)} (existing kept: {len(kept_roles)})\n"
            f"Categories created: {len(made_categories)}\n"
            f"Channels created: {len(made_channels)} (existing kept: {len(kept_channels)})\n"
            f"Gemini permissions were filtered; elevated permissions required your explicit approval.\n"
            f"Classification roles: Hydrogen, Bots, Members (existing members classified; new joins handled automatically).\n"
            f"**Verification access:** {access_note}\n"
            "Existing matching roles/channels are retained and unrelated channels are not deleted."
        )


async def setup(bot):
    await bot.add_cog(ServerBuilder(bot))

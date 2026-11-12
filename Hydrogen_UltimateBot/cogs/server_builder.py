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
    "view_channel", "send_messages", "read_message_history", "embed_links",
    "attach_files", "add_reactions", "manage_messages", "moderate_members",
    "manage_threads", "view_audit_log"
}
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


class ServerBuilder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _template(self):
        with TEMPLATE_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data.get("categories"), list) or not isinstance(data.get("roles"), list):
            raise ValueError("Server template is malformed.")
        return data

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
            '{"role_permissions":{"Founder":["view_channel"],"Moderator":["view_channel","manage_messages"]}}. '
            "Choose least-privilege Discord role permissions for the listed role names. "
            "Allowed permission names only: " + ", ".join(sorted(SAFE_ROLE_PERMISSIONS)) + ". "
            "Never return Administrator, manage_guild, manage_roles, manage_channels, manage_webhooks, "
            "mention_everyone, kick_members, ban_members, or any permission outside that list. "
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
                             if isinstance(v, str) and v in SAFE_ROLE_PERMISSIONS))
            if isinstance(proposed.get(name, []), list) else []
            for name in role_names
        }

    async def _apply_verification_access(self, guild, config):
        """Apply only explicitly configured channel IDs; never create/replace verification."""
        verified_id = snowflake(config.get("verified_role_id"))
        verification_id = snowflake(config.get("verification_channel_id"))
        protected_ids = [snowflake(x) for x in config.get("protected_channel_ids", []) if snowflake(x)]
        if not verified_id or not verification_id:
            return False, "Verified role ID or verification channel ID is missing."
        verified = guild.get_role(verified_id)
        verification = guild.get_channel(verification_id)
        if verified is None or verification is None:
            return False, "Configured Verified role or verification channel was not found in this server."
        everyone = guild.default_role
        await verification.set_permissions(everyone, view_channel=True,
                                            reason="Hydrogen verification-channel access")
        await verification.set_permissions(verified, view_channel=True,
                                            reason="Hydrogen Verified-role access")
        applied = 0
        for channel_id in protected_ids:
            if channel_id == verification_id:
                continue
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            await channel.set_permissions(everyone, view_channel=False,
                                          reason="Hydrogen Verified-only channel access")
            await channel.set_permissions(verified, view_channel=True,
                                          reason="Hydrogen Verified-only channel access")
            applied += 1
        return True, f"Verification channel public; {applied} protected channels restricted to Verified."

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
        except (OSError, ValueError, json.JSONDecodeError, AIUnavailable, AttributeError) as exc:
            return await ctx.send(f"{Emoji.error} Preflight failed; no server changes were made. {str(exc)[:250]}")

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
            f"AI permissions were filtered through the safe allow-list; Administrator is blocked.\n"
            f"**Verification access:** {access_note}\n"
            "Existing matching roles/channels are retained and unrelated channels are not deleted."
        )


async def setup(bot):
    await bot.add_cog(ServerBuilder(bot))

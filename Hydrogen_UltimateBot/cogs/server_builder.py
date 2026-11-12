"""AI-assisted, allow-listed server builder. Creates only the names in data/server_template.json."""
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
    small = value.lower().translate(SMALL_CAPS).replace(" ", "-")
    return ("▫-" + small)[:100]


def role_name(value):
    return ("W X・" + value.strip())[:100]


def category_name(value):
    return ("━━━「 " + value.upper().strip() + " 」━━━")[:100]


class ServerBuilder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _template(self):
        with TEMPLATE_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data.get("categories"), list) or not isinstance(data.get("roles"), list):
            raise ValueError("Server template is malformed.")
        return data

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
        raw = await self.bot.ai.ask(
            prompt, guild_id=guild.id, purpose="server_builder_permissions"
        )
        plan = self.bot.get_cog("AIServerManager")._parse_json(raw)
        proposed = plan.get("role_permissions", {})
        if not isinstance(proposed, dict):
            raise ValueError("AI permission response did not contain role_permissions.")
        clean = {}
        for name in role_names:
            values = proposed.get(name, [])
            if not isinstance(values, list):
                values = []
            clean[name] = sorted(set(v for v in values if isinstance(v, str) and v in SAFE_ROLE_PERMISSIONS))
        return clean

    @commands.hybrid_group(name="create", invoke_without_command=True, description="Create the configured server layout")
    async def create(self, ctx):
        await ctx.reply("Use /create server (or $create server) to build the configured layout.")

    @create.command(name="server", description="Create the configured Hydrogen categories, channels, and roles")
    async def create_server(self, ctx):
        if not ctx.guild:
            return await ctx.reply("This command can only be used inside a server.")
        if ctx.author.id != ctx.guild.owner_id and not await is_server_owner_or_global(ctx.author, self.bot.owner_ids):
            return await ctx.reply(f"{Emoji.error} Only the server owner or configured Hydrogen owner can run the server builder.")
        if not ctx.guild.me.guild_permissions.manage_channels or not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.reply(f"{Emoji.error} Hydrogen needs Manage Channels and Manage Roles permissions.")
        await ctx.defer()
        try:
            template = self._template()
            role_permissions = await self._ai_permissions(ctx.guild, template["roles"])
        except (OSError, ValueError, AIUnavailable, AttributeError) as exc:
            return await ctx.send(f"{Emoji.error} AI/template preflight failed; no server changes were made. {str(exc)[:250]}")

        made_roles, kept_roles, made_categories, made_channels, kept_channels = [], [], [], [], []
        roles_by_base = {}
        for base_name in template["roles"]:
            formatted = role_name(base_name)
            existing = discord.utils.get(ctx.guild.roles, name=formatted)
            if existing:
                roles_by_base[base_name] = existing
                kept_roles.append(formatted)
                continue
            perms = discord.Permissions.none()
            for permission in role_permissions.get(base_name, []):
                setattr(perms, permission, True)
            try:
                created = await ctx.guild.create_role(
                    name=formatted, permissions=perms,
                    reason=f"Hydrogen server builder requested by {ctx.author}"
                )
            except discord.HTTPException:
                continue
            roles_by_base[base_name] = created
            made_roles.append(formatted)

        for category_spec in template["categories"]:
            base_category = category_spec["name"]
            formatted_category = category_name(base_category)
            category = discord.utils.get(ctx.guild.categories, name=formatted_category)
            if category is None:
                overwrites = {
                    ctx.guild.default_role: discord.PermissionOverwrite(view_channel=True),
                    ctx.guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True)
                }
                try:
                    category = await ctx.guild.create_category(
                        formatted_category, overwrites=overwrites,
                        reason=f"Hydrogen server builder requested by {ctx.author}"
                    )
                    made_categories.append(formatted_category)
                except discord.HTTPException:
                    continue
            else:
                kept_categories = True
            for channel_spec in category_spec["channels"]:
                formatted_channel = channel_name(channel_spec["name"])
                if channel_spec["type"] == "voice":
                    existing = discord.utils.get(category.voice_channels, name=formatted_channel)
                else:
                    existing = discord.utils.get(category.text_channels, name=formatted_channel)
                if existing:
                    kept_channels.append(formatted_channel)
                    continue
                try:
                    if channel_spec["type"] == "voice":
                        channel = await ctx.guild.create_voice_channel(
                            formatted_channel, category=category,
                            reason=f"Hydrogen server builder requested by {ctx.author}"
                        )
                    else:
                        channel = await ctx.guild.create_text_channel(
                            formatted_channel, category=category,
                            reason=f"Hydrogen server builder requested by {ctx.author}"
                        )
                    made_channels.append(channel.name)
                except discord.HTTPException:
                    continue

        await audit(
            self.bot, ctx.guild, "Hydrogen server builder completed",
            f"Created {len(made_roles)} roles, {len(made_categories)} categories, "
            f"{len(made_channels)} channels; retained existing matching objects.", ctx.author
        )
        summary = (
            f"{Emoji.success} **Server builder finished for {ctx.guild.name}.**\n"
            f"Roles created: {len(made_roles)} (existing kept: {len(kept_roles)})\n"
            f"Categories created: {len(made_categories)}\n"
            f"Channels created: {len(made_channels)} (existing kept: {len(kept_channels)})\n"
            "AI permissions were restricted to the safe allow-list; Administrator and other high-risk permissions were blocked. "
            "Existing roles/channels were not edited or deleted. Verification access is not changed by this builder."
        )
        await ctx.send(summary)


async def setup(bot):
    await bot.add_cog(ServerBuilder(bot))

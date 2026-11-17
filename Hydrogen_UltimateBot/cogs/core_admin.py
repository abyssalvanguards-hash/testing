import discord
from discord.ext import commands
from core import db
from core.logging import audit
from core.security import is_global_owner, is_bot_admin
import secrets

class HelpPaginator(discord.ui.View):
    def __init__(self, pages, owner_id):
        super().__init__(timeout=180)
        self.pages = pages
        self.owner_id = owner_id
        self.page_index = 0
        self._sync_buttons()

    def _sync_buttons(self):
        self.previous_button.disabled = self.page_index <= 0
        self.next_button.disabled = self.page_index >= len(self.pages) - 1

    async def _check_user(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who opened this help menu can change its page.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction, button):
        if not await self._check_user(interaction):
            return
        self.page_index = max(0, self.page_index - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page_index], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction, button):
        if not await self._check_user(interaction):
            return
        self.page_index = min(len(self.pages) - 1, self.page_index + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page_index], view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class CoreAdmin(commands.Cog):
    def __init__(self, bot): self.bot = bot

    async def cog_check(self, ctx):
        return ctx.guild is not None

    @commands.hybrid_command(name="prefix")
    async def prefix_cmd(self, ctx, new_prefix=None):
        if not new_prefix:
            current = await db.get_setting(ctx.guild.id, "prefix", "!")
            return await ctx.reply(f"Current prefix: `{current}`")
        if not await is_bot_admin(ctx.author, self.bot.owner_ids):
            return await ctx.reply("You need bot admin permission.")
        if len(new_prefix) > 5 or any(c.isspace() for c in new_prefix):
            return await ctx.reply("Prefix must be 1–5 non-space characters.")
        await db.set_setting(ctx.guild.id, "prefix", new_prefix)
        await audit(self.bot, ctx.guild, "Prefix changed", f"New prefix: {new_prefix}", ctx.author)
        await ctx.reply(f"Prefix changed to `{new_prefix}`. It applies immediately.")

    @commands.hybrid_command(name="owneradd")
    async def owner_add(self, ctx, user: discord.User):
        if not is_global_owner(ctx.author.id, self.bot.owner_ids):
            return await ctx.reply("Only a configured global owner can add owners.")
        await db.execute("INSERT OR IGNORE INTO bot_owners(user_id) VALUES(?)", (user.id,))
        await audit(self.bot, ctx.guild, "Bot owner added", user.mention, ctx.author)
        await ctx.reply(f"Added {user.mention} as a bot owner.")

    @commands.hybrid_command(name="adminadd")
    async def admin_add(self, ctx, user: discord.User):
        if not is_global_owner(ctx.author.id, self.bot.owner_ids):
            return await ctx.reply("Only a configured global owner can add bot admins.")
        await db.execute("INSERT OR REPLACE INTO bot_admins(user_id,added_by) VALUES(?,?)", (user.id,ctx.author.id))
        await audit(self.bot, ctx.guild, "Bot admin added", user.mention, ctx.author)
        await ctx.reply(f"Added {user.mention} as a bot admin.")

    @commands.hybrid_command(name="admindel")
    async def admin_del(self, ctx, user: discord.User):
        if not is_global_owner(ctx.author.id, self.bot.owner_ids):
            return await ctx.reply("Only a configured global owner can remove bot admins.")
        await db.execute("DELETE FROM bot_admins WHERE user_id=?", (user.id,))
        await ctx.reply(f"Removed {user.mention} from bot admins.")

    @commands.hybrid_command(name="setrole")
    async def set_role(self, ctx, kind: str, role: discord.Role):
        if not await is_bot_admin(ctx.author, self.bot.owner_ids):
            return await ctx.reply("Permission denied.")
        mapping = {"staff":"staff_role_id","admin":"admin_role_id","mod":"mod_role_id"}
        key = mapping.get(kind.lower())
        if not key: return await ctx.reply("Use staff, admin, or mod.")
        if role >= ctx.guild.me.top_role:
            return await ctx.reply("Move the bot's highest role above the selected role.")
        await db.set_setting(ctx.guild.id,key,role.id)
        await audit(self.bot,ctx.guild,"Role configuration",f"{kind}: {role.mention}",ctx.author)
        await ctx.reply(f"{kind.title()} role set to {role.mention}.")

    @commands.hybrid_command(name="setup_logs")
    async def setup_logs(self, ctx):
        if not await is_bot_admin(ctx.author, self.bot.owner_ids):
            return await ctx.reply("Permission denied.")
        names = ["logs-audit","logs-moderation","logs-members","logs-messages","logs-security"]
        category = discord.utils.get(ctx.guild.categories,name="Hydrogen Logs")
        if not category:
            category = await ctx.guild.create_category("Hydrogen Logs")
        made = []
        for name in names:
            channel = discord.utils.get(category.text_channels,name=name)
            if not channel:
                overwrites = {
                    ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    ctx.guild.me: discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
                }
                channel = await ctx.guild.create_text_channel(name,category=category,overwrites=overwrites)
                made.append(channel.name)
            if name == "logs-audit":
                await db.set_setting(ctx.guild.id,"log_channel_id",channel.id)
        await audit(self.bot,ctx.guild,"Log channels setup",f"Created: {', '.join(made) or 'none'}",ctx.author)
        await ctx.reply("Log channel setup complete. Review category access and add trusted staff if needed.")

    @commands.hybrid_command(name="createstaffrole")
    async def create_staff_role(self, ctx, preset: str, *, role_name: str):
        if not await is_bot_admin(ctx.author, self.bot.owner_ids):
            return await ctx.reply("Permission denied.")
        presets = {
            "support": dict(view_channel=True, send_messages=True, read_message_history=True,
                            embed_links=True, attach_files=True),
            "moderator": dict(view_channel=True, send_messages=True, read_message_history=True,
                              embed_links=True, attach_files=True, manage_messages=True,
                              moderate_members=True, manage_threads=True, view_audit_log=True),
            "admin": dict(view_channel=True, send_messages=True, read_message_history=True,
                          embed_links=True, attach_files=True, manage_messages=True,
                          moderate_members=True, manage_threads=True, view_audit_log=True,
                          manage_channels=True, manage_nicknames=True)
        }
        perms = presets.get(preset.lower())
        if not perms:
            return await ctx.reply("Choose a preset: `support`, `moderator`, or `admin`.")
        existing = discord.utils.get(ctx.guild.roles, name=role_name)
        if existing:
            return await ctx.reply(f"A role named **{role_name}** already exists: {existing.mention}")
        role = await ctx.guild.create_role(
            name=role_name,
            permissions=discord.Permissions(**perms),
            reason=f"Hydrogen role preset {preset} requested by {ctx.author}"
        )
        await audit(self.bot, ctx.guild, "Staff role created",
                    f"{role.mention} • preset={preset} • permissions={', '.join(k for k,v in perms.items() if v)}",
                    ctx.author)
        await ctx.reply(f"Created {role.mention} using the `{preset}` least-privilege preset. Review permissions before assigning.")

    @commands.hybrid_command(name="grantrole")
    async def grant_role(self, ctx, member: discord.Member, role: discord.Role):
        if not await is_bot_admin(ctx.author, self.bot.owner_ids):
            return await ctx.reply("Permission denied.")
        if role >= ctx.guild.me.top_role:
            return await ctx.reply("Move the bot's highest role above that role.")
        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.reply("You cannot grant a role at or above your highest role.")
        try:
            await member.add_roles(role, reason=f"Granted by {ctx.author} via Hydrogen")
        except discord.HTTPException:
            return await ctx.reply("Discord rejected the role assignment. Check permissions and hierarchy.")
        await audit(self.bot, ctx.guild, "Role granted", f"{role.mention} → {member.mention}", ctx.author)
        await ctx.reply(f"Granted {role.mention} to {member.mention}.")

    @commands.hybrid_command(name="revokerole")
    async def revoke_role(self, ctx, member: discord.Member, role: discord.Role):
        if not await is_bot_admin(ctx.author, self.bot.owner_ids):
            return await ctx.reply("Permission denied.")
        if role >= ctx.guild.me.top_role:
            return await ctx.reply("Move the bot's highest role above that role.")
        try:
            await member.remove_roles(role, reason=f"Revoked by {ctx.author} via Hydrogen")
        except discord.HTTPException:
            return await ctx.reply("Discord rejected the role removal.")
        await audit(self.bot, ctx.guild, "Role revoked", f"{role.mention} from {member.mention}", ctx.author)
        await ctx.reply(f"Removed {role.mention} from {member.mention}.")

    @commands.hybrid_command(
        name="help",
        description="Browse WardenX commands with usage, descriptions, and access requirements."
    )
    async def help_command(self, ctx):
        """Show a paginated command guide with one detailed card per command."""
        commands_list = [
            command for command in self.bot.walk_commands()
            if not command.hidden and command.qualified_name not in {"help", "bothelp"}
        ]
        commands_list.sort(key=lambda command: command.qualified_name.lower())

        pages = []
        page_size = 5
        for offset in range(0, len(commands_list), page_size):
            embed = discord.Embed(
                title="WardenX Manager • Command Guide",
                description=(
                    "Each field is one command card. It shows what the command does, "
                    "how to run it, and who is allowed to use it. "
                    "Use the buttons below to browse all commands."
                ),
                color=discord.Color.blurple()
            )
            batch = commands_list[offset:offset + page_size]
            for command in batch:
                qualified = command.qualified_name
                signature = command.signature.strip()
                slash_usage = f"/{qualified}" + (f" {signature}" if signature else "")
                prefix_usage = f"{ctx.prefix}{qualified}" + (f" {signature}" if signature else "")

                description = (
                    command.description
                    or command.short_doc
                    or command.help
                    or getattr(command.callback, "__doc__", None)
                    or f"Runs the {qualified.replace('_', ' ')} command."
                )
                description = " ".join(str(description).split())[:360]
                access = self._help_access(command)
                value = (
                    f"**What it does**\n{description}\n"
                    f"**How to use**\n`{slash_usage}`\n`{prefix_usage}`\n"
                    f"**Who can use it**\n{access}"
                )
                embed.add_field(
                    name=f"▸ {slash_usage}"[:256],
                    value=value[:1024],
                    inline=False
                )

            page_number = len(pages) + 1
            total_pages = max(1, (len(commands_list) + page_size - 1) // page_size)
            embed.set_footer(
                text=f"WardenX Manager • {len(commands_list)} commands • Page {page_number}/{total_pages}"
            )
            pages.append(embed)

        if not pages:
            pages = [discord.Embed(
                title="WardenX Manager • Command Guide",
                description="No commands are currently available.",
                color=discord.Color.blurple()
            )]

        view = HelpPaginator(pages, ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    def _help_access(self, command):
        """Infer a command's access gate from its callback and declared checks."""
        names = set()
        callback = getattr(command, "callback", None)
        code = getattr(callback, "__code__", None)
        if code:
            names.update(code.co_names)
        for check in getattr(command, "checks", []):
            check_code = getattr(check, "__code__", None)
            if check_code:
                names.update(check_code.co_names)
            names.add(getattr(check, "__qualname__", ""))

        cog_name = command.cog_name or ""
        qualified = command.qualified_name
        if cog_name == "GeminiAdmin":
            return "Configured bot owners and user IDs listed as API-key moderators."
        if "is_global_owner" in names:
            return "Configured global bot owner only."
        if "is_bot_admin" in names:
            return "Configured bot owner or bot admin."
        if "_moderator" in names:
            return "Configured server moderator/admin or bot owner."
        if cog_name == "Applications" and qualified not in {"applicationpanel", "setreview"}:
            return "Server owner or configured global bot owner (review actions)."
        if "has_permissions" in names or "has_guild_permissions" in names:
            return "Members who have the required Discord server permission."
        return "Server members; Discord channel access and any command-specific checks still apply."

async def setup(bot): await bot.add_cog(CoreAdmin(bot))

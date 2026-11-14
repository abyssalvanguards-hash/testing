import discord
from discord.ext import commands
from core import db
from core.logging import audit
from core.security import is_global_owner, is_bot_admin
import secrets

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

    @commands.hybrid_command(name="help", description="Show all Hydrogen commands, including subcommands and server-care tools.")
    async def help_command(self, ctx):
        """Show available commands and concise usage guidance."""
        embed = discord.Embed(
            title="WardenX Manager — Help",
            description=(
                f"Use slash commands with `/` or prefix commands with `{ctx.prefix}`. "
                "Command availability depends on your permissions and enabled configuration.\n"
                "Use a command's slash-command menu for its required arguments."
            ),
            color=discord.Color.blurple()
        )
        grouped = {}
        for command in sorted(self.bot.walk_commands(), key=lambda c: c.qualified_name):
            if command.hidden:
                continue
            cog_name = command.cog_name or "General"
            grouped.setdefault(cog_name, []).append(command)

        for cog_name, commands_in_group in grouped.items():
            lines = []
            for command in commands_in_group:
                qualified = command.qualified_name
                description = command.short_doc or command.help or "No description provided."
                prefix_usage = f"{ctx.prefix}{qualified}"
                slash_usage = f"/{qualified}"
                lines.append(f"`{slash_usage}` · `{prefix_usage}` — {description.splitlines()[0][:120]}")
            # Discord limits each embed field to 1024 characters.
            chunks = []
            current = ""
            for line in lines:
                candidate = f"{current}\n{line}" if current else line
                if len(candidate) > 1000:
                    if current:
                        chunks.append(current)
                    current = line[:1000]
                else:
                    current = candidate
            if current:
                chunks.append(current)
            for index, chunk in enumerate(chunks, start=1):
                if len(embed.fields) >= 24:
                    break
                field_name = cog_name if index == 1 else f"{cog_name} (continued)"
                embed.add_field(name=field_name[:256], value=chunk, inline=False)

        embed.add_field(
            name="Server care quick guide",
            value=(
                "`/busy 2d` · `/busyoff` · `/busystatus`\n"
                "`/backup create` · `/backup list` · `/backup export <id>`\n"
                "`/backup restore <id>` previews a restore; the server owner must confirm it."
            ),
            inline=False
        )
        embed.set_footer(text="WardenX Manager • Use /help anytime")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="bothelp")
    async def show_bot_help(self, ctx):
        embed=discord.Embed(title="WardenX Manager",description="Prefix commands and panels. Developer: @h_two_.",color=discord.Color.blurple())
        embed.add_field(name="Setup",value="`!setup_logs`, `!setrole staff @Role`, `!prefix ?`",inline=False)
        embed.add_field(name="Tickets",value="`!ticketconfig key #category @staff title | description`, `!ticketpanel key`",inline=False)
        embed.add_field(name="Applications",value="`!applicationpanel`",inline=False)
        embed.add_field(name="Moderation",value="`!timeout @member 10m reason`, `!warn @member reason`",inline=False)
        embed.add_field(name="Payments",value="`!paymentconfig upi ...`, `!invoice 100 upi`, `!paymentcheck <id> <txid>` (BTC/LTC)",inline=False)
        embed.add_field(name="AI Server Tools",value="`!airestructure`, `!aipermissions`, `!showplan <id>`, `!applyplan <id> CONFIRM` (server owner approval required)",inline=False)
        embed.add_field(name="AI",value="AI replies in tickets when configured; quota errors hand off to staff.",inline=False)
        await ctx.reply(embed=embed)

async def setup(bot): await bot.add_cog(CoreAdmin(bot))

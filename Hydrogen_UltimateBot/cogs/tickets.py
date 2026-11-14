import asyncio
import discord, io, re
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput, UserSelect
from datetime import datetime, timezone
from config import MAX_TICKET_PANELS, MAX_OPEN_TICKETS_PER_USER, AI_MAX_TICKET_HISTORY_MESSAGES
from core import db
from core.logging import audit
from core.security import is_bot_admin, safe_name
from core.ai import AIUnavailable
from emojis import Emoji

class TicketCreateView(View):
    def __init__(self, panel_key):
        super().__init__(timeout=None); self.panel_key=panel_key
        b=Button(label="Open Ticket",style=discord.ButtonStyle.primary,custom_id=f"hydro:ticket:create:{panel_key}",emoji=discord.PartialEmoji.from_str(Emoji.ticket))
        b.callback=self.create; self.add_item(b)
    async def create(self, interaction):
        await open_ticket(interaction,self.panel_key)

class TicketControls(View):
    def __init__(self, bot=None):
        super().__init__(timeout=None)
        self._bot = bot
        actions=[
            ("Claim", "success", "claim", Emoji.claim),
            ("Close", "danger", "close", Emoji.close),
            ("Reopen", "primary", "reopen", Emoji.reopen),
            ("Add User", "secondary", "add", Emoji.add_user),
            ("Remove User", "secondary", "remove", Emoji.remove_user),
            ("Transcript", "secondary", "transcript", Emoji.transcript),
        ]
        for label, style, action, emoji in actions:
            b=Button(
                label=label,
                style=getattr(discord.ButtonStyle,style),
                custom_id=f"hydro:ticket:{action}",
                emoji=discord.PartialEmoji.from_str(emoji)
            )
            b.callback=self.callback_for(action); self.add_item(b)
    def callback_for(self, action):
        async def callback(interaction):
            ticket=await db.fetchone("SELECT * FROM tickets WHERE channel_id=?",(interaction.channel_id,))
            if not ticket: return await interaction.response.send_message("This is not a registered ticket.",ephemeral=True)
            staff=await is_bot_admin(interaction.user,self._bot.owner_ids) if self._bot else False
            role=interaction.guild.get_role(ticket["staff_role_id"]) if ticket["staff_role_id"] else None
            staff=staff or bool(role and role in getattr(interaction.user,"roles",[])) or interaction.user.id==interaction.guild.owner_id
            owner=interaction.user.id==ticket["owner_id"]
            if action=="claim":
                if not staff: return await interaction.response.send_message("Staff only.",ephemeral=True)
                if ticket["claimed_by"]: return await interaction.response.send_message("Already claimed.",ephemeral=True)
                await db.execute("UPDATE tickets SET claimed_by=?,ai_paused=1 WHERE id=?",(interaction.user.id,ticket["id"]))
                await audit(interaction.client,interaction.guild,"Ticket claimed",f"Ticket #{ticket['id']}",interaction.user)
                return await interaction.response.send_message(f"Claimed by {interaction.user.mention}.")
            if action=="close":
                if not (staff or owner): return await interaction.response.send_message("Not allowed.",ephemeral=True)
                await db.execute("UPDATE tickets SET status='closed',closed_at=? WHERE id=?",(datetime.now(timezone.utc).isoformat(),ticket["id"]))
                await interaction.channel.set_permissions(interaction.guild.default_role,view_channel=False)
                member=interaction.guild.get_member(ticket["owner_id"])
                if member: await interaction.channel.set_permissions(member,view_channel=True,send_messages=False,read_message_history=True)
                await audit(interaction.client,interaction.guild,"Ticket closed",f"Ticket #{ticket['id']}",interaction.user)
                return await interaction.response.send_message("Ticket closed. Staff can reopen it.")
            if action=="reopen":
                if not staff: return await interaction.response.send_message("Staff only.",ephemeral=True)
                await db.execute("UPDATE tickets SET status='open',closed_at=NULL,claimed_by=NULL,ai_paused=0 WHERE id=?",(ticket["id"],))
                member=interaction.guild.get_member(ticket["owner_id"])
                if member: await interaction.channel.set_permissions(member,view_channel=True,send_messages=True,read_message_history=True)
                return await interaction.response.send_message("Ticket reopened.")
            if action in ("add","remove"):
                if not staff: return await interaction.response.send_message("Staff only.",ephemeral=True)
                return await interaction.response.send_message("Use `!ticket_add @member` or `!ticket_remove @member` in this channel.",ephemeral=True)
            if action=="transcript":
                if not (staff or owner): return await interaction.response.send_message("Not allowed.",ephemeral=True)
                return await send_transcript(interaction)
        return callback
async def open_ticket(interaction,panel_key):
    if not interaction.guild: return await interaction.response.send_message("Server only.",ephemeral=True)
    panel=await db.fetchone("SELECT * FROM ticket_panels WHERE guild_id=? AND panel_key=?",(interaction.guild.id,panel_key))
    if not panel: return await interaction.response.send_message("Panel not configured.",ephemeral=True)
    existing=await db.fetchone("SELECT channel_id FROM tickets WHERE guild_id=? AND owner_id=? AND status='open'",(interaction.guild.id,interaction.user.id))
    if existing:
        ch=interaction.guild.get_channel(existing["channel_id"])
        return await interaction.response.send_message(f"You already have an open ticket: {ch.mention if ch else 'please contact staff'}",ephemeral=True)
    category=interaction.guild.get_channel(panel["category_id"]) if panel["category_id"] else None
    role=interaction.guild.get_role(panel["staff_role_id"]) if panel["staff_role_id"] else None
    if not isinstance(category,discord.CategoryChannel) or not role:
        return await interaction.response.send_message("Ticket panel configuration is incomplete.",ephemeral=True)
    if role>=interaction.guild.me.top_role:
        return await interaction.response.send_message("Bot role must be above the ticket staff role.",ephemeral=True)
    overwrites={interaction.guild.default_role:discord.PermissionOverwrite(view_channel=False),
        interaction.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True,embed_links=True),
        role:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),
        interaction.guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True,read_message_history=True)}
    await interaction.response.defer(ephemeral=True)
    channel=await interaction.guild.create_text_channel(f"ticket-{safe_name(interaction.user.display_name)}",category=category,overwrites=overwrites)
    try:
        await db.execute("INSERT INTO tickets(guild_id,channel_id,owner_id,staff_role_id,panel_key,created_at) VALUES(?,?,?,?,?,?)",
            (interaction.guild.id,channel.id,interaction.user.id,role.id,panel_key,datetime.now(timezone.utc).isoformat()))
    except Exception:
        await channel.delete(reason="Ticket database insert failed")
        raise
    embed=discord.Embed(
        title=f"{Emoji.ticket} Support Ticket",
        description=(
            f"{Emoji.welcome} {interaction.user.mention}, describe your issue below.\n"
            f"{Emoji.ai} You can simply type your message—no mention or reply is needed.\n"
            f"{Emoji.staff} Staff can take over at any time."
        ),
        color=discord.Color.blurple()
    )
    embed.set_footer(text="WardenX Manager • Developer: @h_two_")
    await channel.send(content=f"{interaction.user.mention} {role.mention}",embed=embed,view=TicketControls(interaction.client))
    await interaction.followup.send(f"Created {channel.mention}",ephemeral=True)
    await audit(interaction.client,interaction.guild,"Ticket opened",f"{channel.mention} • panel {panel_key}",interaction.user)

async def send_transcript(interaction):
    lines=[]
    async for m in interaction.channel.history(limit=500,oldest_first=True):
        content=m.content or ""
        if m.attachments: content += " [attachments: "+", ".join(a.filename for a in m.attachments)+"]"
        lines.append(f"[{m.created_at.isoformat()}] {m.author} ({m.author.id}): {content}")
    data="\n".join(lines).encode("utf-8")
    file=discord.File(io.BytesIO(data),filename=f"ticket-{interaction.channel.id}-transcript.txt")
    await interaction.response.send_message("Transcript generated.",file=file,ephemeral=True)

class TicketCog(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
    @commands.hybrid_command(name="ticketconfig")
    async def ticket_config(self,ctx,key,category:discord.CategoryChannel,staff_role:discord.Role,*,content):
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Permission denied.")
        if "|" not in content: return await ctx.reply("Use: title | description")
        title,desc=[x.strip() for x in content.split("|",1)]
        exists=await db.fetchone("SELECT panel_key FROM ticket_panels WHERE guild_id=? AND panel_key=?",(ctx.guild.id,key))
        count=await db.fetchone("SELECT COUNT(*) AS n FROM ticket_panels WHERE guild_id=?",(ctx.guild.id,))
        if not exists and count["n"]>=MAX_TICKET_PANELS: return await ctx.reply(f"Maximum {MAX_TICKET_PANELS} panels.")
        if staff_role>=ctx.guild.me.top_role: return await ctx.reply("Bot role must be above staff role.")
        await db.execute("""INSERT INTO ticket_panels(guild_id,panel_key,title,description,category_id,staff_role_id)
            VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,panel_key) DO UPDATE SET title=excluded.title,description=excluded.description,category_id=excluded.category_id,staff_role_id=excluded.staff_role_id""",
            (ctx.guild.id,key,title,desc,category.id,staff_role.id))
        await ctx.reply(f"Configured `{key}`.")
    @commands.hybrid_command(name="ticketpanel")
    async def ticket_panel(self,ctx,key):
        panel=await db.fetchone("SELECT * FROM ticket_panels WHERE guild_id=? AND panel_key=?",(ctx.guild.id,key))
        if not panel: return await ctx.reply("Panel not found.")
        e=discord.Embed(
            title=f"{Emoji.ticket} {panel['title']}",
            description=(
                f"{Emoji.support} {panel['description']}\n\n"
                f"{Emoji.ai} Press the button below to open a support ticket."
            ),
            color=discord.Color.blurple()
        )
        e.set_footer(text="WardenX Manager • Developer: @h_two_")
        await ctx.send(embed=e,view=TicketCreateView(key))
    @commands.hybrid_command(name="ticket_add")
    async def ticket_add(self,ctx,member:discord.Member):
        t=await db.fetchone("SELECT * FROM tickets WHERE channel_id=?",(ctx.channel.id,))
        if not t: return await ctx.reply("Not a ticket channel.")
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Staff only.")
        await ctx.channel.set_permissions(member,view_channel=True,send_messages=True,read_message_history=True)
        await audit(self.bot,ctx.guild,"Ticket member added",f"{member.mention} in {ctx.channel.mention}",ctx.author)
        await ctx.reply(f"Added {member.mention}.")
    @commands.hybrid_command(name="ticket_remove")
    async def ticket_remove(self,ctx,member:discord.Member):
        t=await db.fetchone("SELECT * FROM tickets WHERE channel_id=?",(ctx.channel.id,))
        if not t: return await ctx.reply("Not a ticket channel.")
        if not await is_bot_admin(ctx.author,self.bot.owner_ids): return await ctx.reply("Staff only.")
        if member.id==t["owner_id"]: return await ctx.reply("Cannot remove the ticket owner.")
        await ctx.channel.set_permissions(member,overwrite=None)
        await audit(self.bot,ctx.guild,"Ticket member removed",f"{member.mention} from {ctx.channel.mention}",ctx.author)
        await ctx.reply(f"Removed {member.mention}.")
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or not self.bot.ai:
            return

        ticket = await db.fetchone(
            "SELECT * FROM tickets WHERE channel_id=? AND status='open'",
            (message.channel.id,)
        )
        if not ticket:
            return

        # Keep AI paused after a staff request or claim until the ticket is reopened.
        if ticket.get("ai_paused") or ticket.get("claimed_by"):
            return

        staff_request = re.search(
            r"\b(?:"
            r"request(?:ing)?\s+(?:a\s+)?staff|"
            r"need\s+(?:a\s+)?staff|"
            r"talk\s+to\s+(?:a\s+)?(?:human|staff|agent)|"
            r"speak\s+to\s+(?:a\s+)?(?:human|staff|agent)|"
            r"human\s+(?:support|agent)|real\s+person|"
            r"staff\s+(?:please|help)|"
            r"(?:can|could|may)\s+i\s+(?:talk|speak)\s+to\s+(?:a\s+)?staff|"
            r"(?:get|call|ping)\s+(?:me\s+)?(?:a\s+)?staff|"
            r"please\s+(?:get|call|ping)\s+(?:a\s+)?staff|"
            r"human\s+help|"
            r"escalate\s+(?:this\s+)?(?:to\s+)?staff"
            r")\b",
            message.content,
            re.IGNORECASE
        )
        if staff_request:
            await db.execute("UPDATE tickets SET ai_paused=1 WHERE id=?", (ticket["id"],))
            staff_role = message.guild.get_role(ticket["staff_role_id"]) if ticket["staff_role_id"] else None
            await message.channel.send(
                f"{Emoji.staff} {staff_role.mention if staff_role else 'Staff'}, "
                f"{message.author.mention} requested human support. AI replies are paused for this ticket."
            )
            await audit(self.bot, message.guild, "Ticket staff requested", f"Ticket #{ticket['id']}", message.author)
            return

        # Any human message in an open ticket triggers the assistant. No mention
        # or reply-to-bot is required.
        try:
            history = []
            # Keep context short and fetch newest messages first to reduce
            # Discord history overhead, then restore chronological order.
            limit = max(4, min(int(AI_MAX_TICKET_HISTORY_MESSAGES), 12))
            async for item in message.channel.history(limit=limit, oldest_first=False):
                if item.content:
                    history.append(f"{item.author.display_name}: {item.content[:600]}")
            history.reverse()

            prompt = (
                "You are WardenX Manager, the support assistant in a Discord ticket. "
                "Reply directly to the latest user message. Be concise, clear, and helpful. "
                "Users do not need to mention or reply to you. "
                "Never approve refunds, payments, bans, role grants, or policy exceptions. "
                "Escalate uncertain or account-specific actions to staff.\n\n"
                + "\n".join(history)
            )

            async with message.channel.typing():
                answer = await self.bot.ai.ask(
                    prompt, message.guild.id, message.author.id, "ticket"
                )
            await message.reply(answer, mention_author=False)

        except AIUnavailable as exc:
            await message.channel.send(
                f"AI unavailable — staff handoff required: `{exc}`"
            )
            staff = message.guild.get_role(ticket["staff_role_id"]) if ticket["staff_role_id"] else None
            if staff:
                await message.channel.send(f"{staff.mention} please assist.")
            for owner_id in self.bot.owner_ids:
                try:
                    user = self.bot.get_user(owner_id) or await self.bot.fetch_user(owner_id)
                    await user.send(
                        f"WardenX AI handoff in {message.guild.name} / "
                        f"#{message.channel.name}: {exc}"
                    )
                except discord.HTTPException:
                    pass
        except discord.HTTPException:
            return
    async def cog_load(self):
        # Persistent views are registered in bot.setup_hook after DB init.
        pass

async def setup(bot):
    cog=TicketCog(bot); await bot.add_cog(cog)
    bot.add_view(TicketControls(bot))

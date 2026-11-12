"""Optional outbound connector for the separate Hydrogen web panel.

Disabled by default. Polling is bounded and failures never stop the Discord bot.
Only safe informational requests are handled until the confirmation workflow is
fully wired; unsupported management requests are explicitly rejected.
"""
import asyncio
import logging
import os
import aiohttp
import discord
from discord.ext import commands, tasks
from core.security import is_server_owner_or_global

log = logging.getLogger(__name__)


class PanelConnector(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.enabled = os.getenv("PANEL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.base_url = os.getenv("PANEL_API_URL", "").strip().rstrip("/")
        self.secret = os.getenv("PANEL_SHARED_SECRET", "").strip()
        try:
            self.interval = max(15, min(600, int(os.getenv("PANEL_POLL_INTERVAL", "45"))))
        except ValueError:
            self.interval = 45
        self._session = None
        if self.enabled and self.base_url.startswith("https://") and self.secret:
            self.poll_panel.change_interval(seconds=self.interval)
            self.poll_panel.start()
        elif self.enabled:
            log.error("Panel enabled but PANEL_API_URL must use HTTPS and PANEL_SHARED_SECRET must be set; connector remains stopped.")

    async def cog_unload(self):
        self.poll_panel.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    async def _http(self):
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=8, connect=4)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"Authorization": "Bearer " + self.secret},
            )
        return self._session

    async def _report(self, command_id, status, result):
        session = await self._http()
        try:
            async with session.post(
                self.base_url + "/api/bot/result",
                json={"id": command_id, "status": status, "result": str(result)[:1200]},
            ) as response:
                if response.status >= 400:
                    log.warning("Panel result update failed: HTTP %s", response.status)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            log.warning("Could not report panel command result; will continue polling.")

    async def _handle(self, item):
        command_id = int(item.get("id", 0))
        guild_id = int(item.get("guild_id", 0))
        user_id = int(item.get("user_id", 0))
        command = str(item.get("command", "")).strip()
        guild = self.bot.get_guild(guild_id)
        if not guild:
            await self._report(command_id, "rejected", "Hydrogen is not in that server.")
            return
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None
        if member is None or not await is_server_owner_or_global(member, self.bot.owner_ids):
            await self._report(command_id, "rejected", "Only the server owner or configured Hydrogen owner can submit panel commands.")
            return

        normalized = command.casefold().strip()
        if normalized in {"status", "server status", "show server status"}:
            result = (
                f"Server: {guild.name}\nMembers: {guild.member_count}\n"
                f"Channels: {len(guild.channels)}\nRoles: {len(guild.roles)}\n"
                f"Hydrogen: online"
            )
            await self._report(command_id, "completed", result)
            return

        # Do not allow free-form text to become a Discord action. The next
        # implementation stage adds allow-listed Gemini plans and owner approval.
        await self._report(
            command_id, "needs_confirmation",
            "This request is queued safely, but this action is not enabled yet. "
            "Only server status is currently supported by the panel connector. "
            "Server changes, busy mode, and backups will be enabled after their "
            "preview, owner-confirmation, and restore safeguards are implemented."
        )

    @tasks.loop(seconds=45)
    async def poll_panel(self):
        try:
            session = await self._http()
            async with session.get(self.base_url + "/api/bot/commands") as response:
                if response.status == 401:
                    log.error("Panel rejected bot authentication. Check PANEL_SHARED_SECRET.")
                    return
                response.raise_for_status()
                data = await response.json()
            for item in data.get("commands", [])[:5]:
                try:
                    await self._handle(item)
                except Exception:
                    log.exception("Panel command handling failed.")
                    await self._report(int(item.get("id", 0)), "failed", "Hydrogen could not process this request.")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            log.warning("Panel is unreachable; Discord bot continues normally.")
        except Exception:
            log.exception("Unexpected optional panel connector error.")

    @poll_panel.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(PanelConnector(bot))

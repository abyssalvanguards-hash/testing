"""Optional outbound connector for the separate WardenX Manager web panel.

Disabled by default. Polling is bounded and failures never stop the Discord bot.
Gemini may draft allow-listed plans. Every server mutation remains behind the
existing actual-owner Discord confirmation command.
"""
import asyncio
import logging
import os
import json
import re
import aiohttp
import discord
from core import db
from discord.ext import commands, tasks
from core.security import is_server_owner_or_global, safe_name

log = logging.getLogger(__name__)

PANEL_ACTIONS = {"restructure", "permissions", "busy_status", "backup_list", "unsupported"}
PANEL_PERMISSION_ALLOWLIST = {"view_channel", "send_messages", "read_message_history", "embed_links", "attach_files", "add_reactions"}


def parse_panel_action(raw):
    """Parse Gemini classifier output into a strict action enum."""
    if not isinstance(raw, str):
        return "unsupported"
    cleaned = re.sub(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$", "", raw.strip(), flags=re.I)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return "unsupported"
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except (TypeError, ValueError, json.JSONDecodeError):
        return "unsupported"
    action = parsed.get("action") if isinstance(parsed, dict) else None
    return action if isinstance(action, str) and action in PANEL_ACTIONS else "unsupported"


def clean_panel_permission_names(values):
    """Keep only known string permission names and remove duplicates."""
    if not isinstance(values, list):
        return []
    return sorted({value for value in values if isinstance(value, str)} & PANEL_PERMISSION_ALLOWLIST)


def clean_panel_name(value):
    """Normalize a model-proposed Discord channel/category name."""
    if not isinstance(value, str) or not value.strip():
        return ""
    # safe_name intentionally falls back to "member" for user-facing names;
    # for model-proposed Discord structure, reject punctuation-only input.
    normalized = re.sub(r"[^a-z0-9-]", "-", value.strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")[:80]
    return safe_name(normalized) if normalized else ""


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
        self._claim_tokens = {}
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
                json={"id": command_id, "claim_token": self._claim_tokens.get(command_id, ""), "status": status, "result": str(result)[:1200]},
            ) as response:
                if response.status >= 400:
                    log.warning("Panel result update failed: HTTP %s", response.status)
                else:
                    self._claim_tokens.pop(command_id, None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            log.warning("Could not report panel command result; will continue polling.")

    async def _handle(self, item):
        command_id = int(item.get("id", 0))
        token = item.get("claim_token")
        if isinstance(token, str) and token:
            self._claim_tokens[command_id] = token
        guild_id = int(item.get("guild_id", 0))
        user_id = int(item.get("user_id", 0))
        command = str(item.get("command", "")).strip()
        guild = self.bot.get_guild(guild_id)
        if not guild:
            await self._report(command_id, "rejected", "WardenX Manager is not in that server.")
            return
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None
        if member is None or not await is_server_owner_or_global(member, self.bot.owner_ids):
            await self._report(command_id, "rejected", "Only the server owner or configured WardenX Manager owner can submit panel commands.")
            return

        normalized = command.casefold().strip()
        if normalized in {"status", "server status", "show server status"}:
            result = (
                f"Server: {guild.name}\nMembers: {guild.member_count}\n"
                f"Channels: {len(guild.channels)}\nRoles: {len(guild.roles)}\n"
                f"WardenX Manager: online"
            )
            await self._report(command_id, "completed", result)
            return

        # Ask Gemini only to classify the request into a tiny allow-list.
        # AI output never becomes executable code or an arbitrary Discord action.
        try:
            raw = await self.bot.ai.ask(
                "Classify the user's Discord server request. Return ONLY JSON "
                "with one key action, whose value is exactly one of: "
                "restructure, permissions, busy_status, backup_list, unsupported. "
                "Use restructure for proposed channel/category organization; "
                "permissions for least-privilege channel-role review; busy_status "
                "for checking server-care mode; backup_list for listing backups. "
                "Anything else is unsupported. User request: " + command[:1500],
                guild_id=guild.id, user_id=user_id, purpose="panel_intent"
            )
            action = parse_panel_action(raw)
        except Exception as exc:
            await self._report(command_id, "failed", f"Gemini could not classify this request: {type(exc).__name__}.")
            return

        if action == "busy_status":
            row = await db.fetchone("SELECT expires_at FROM busy_states WHERE guild_id=?", (guild.id,))
            result = "Server-care mode is off." if not row else f"Server-care mode expiry: {row['expires_at']}."
            await self._report(command_id, "completed", result)
            return
        if action == "backup_list":
            rows = await db.fetchall("SELECT id,created_at,description FROM server_backups WHERE guild_id=? ORDER BY id DESC LIMIT 10", (guild.id,))
            result = "\n".join(f"#{r['id']} • {r['created_at']} • {r['description'] or 'Server structure'}" for r in rows) or "No backups found."
            await self._report(command_id, "completed", result)
            return
        if action in {"restructure", "permissions"}:
            try:
                manager = self.bot.get_cog("AIServerManager")
                if manager is None:
                    raise RuntimeError("AI server manager is not loaded")
                scan = await manager._scan(guild)
                if action == "restructure":
                    prompt = ("Return ONLY JSON with keys summary, categories (array of category names), "
                              "channels (array of {name,category}), rename (array of {channel_id,new_name}). "
                              "Suggest create/rename only; never delete. Names <=90 chars. Snapshot: "
                              + json.dumps(scan, ensure_ascii=False))
                else:
                    prompt = ("Return ONLY JSON with keys summary and changes [{channel,role,allow,deny}]. "
                              "Use existing names only and only these permissions: "
                              + ", ".join(sorted(__import__("cogs.ai_server_manager", fromlist=["ALLOWED_PERMS"]).ALLOWED_PERMS))
                              + ". Never grant administrator or manage_guild. Snapshot: "
                              + json.dumps(scan, ensure_ascii=False))
                plan = manager._parse_json(await self.bot.ai.ask(
                    prompt, guild_id=guild.id, user_id=user_id, purpose="panel_" + action
                ))
                if action == "restructure":
                    clean = {"summary": str(plan.get("summary", "Proposed server organization"))[:500], "categories": [], "channels": [], "rename": []}
                    categories = plan.get("categories", [])
                    channels = plan.get("channels", [])
                    renames = plan.get("rename", [])
                    if not all(isinstance(x, list) for x in (categories, channels, renames)):
                        raise ValueError("Gemini returned invalid restructuring fields.")
                    for name in categories[:20]:
                        normalized = clean_panel_name(name)
                        if normalized:
                            clean["categories"].append(normalized)
                    for item in channels[:60]:
                        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("category"), str):
                            channel_name = clean_panel_name(item["name"])
                            category_name = clean_panel_name(item["category"])
                            if channel_name and category_name:
                                clean["channels"].append({"name": channel_name, "category": category_name})
                    valid_ids = {ch.id for ch in guild.channels}
                    for item in renames[:30]:
                        if isinstance(item, dict) and str(item.get("channel_id", "")).isdigit() and int(item["channel_id"]) in valid_ids and isinstance(item.get("new_name"), str):
                            new_name = clean_panel_name(item["new_name"])
                            if new_name:
                                clean["rename"].append({"channel_id": int(item["channel_id"]), "new_name": new_name})
                else:
                    from cogs.ai_server_manager import ALLOWED_PERMS
                    clean = {"summary": str(plan.get("summary", "Least-privilege permission review"))[:500], "changes": []}
                    changes = plan.get("changes", [])
                    if not isinstance(changes, list):
                        raise ValueError("Gemini returned invalid permission changes.")
                    for item in changes[:60]:
                        if not isinstance(item, dict): continue
                        channel = discord.utils.get(guild.channels, name=str(item.get("channel", "")))
                        role = discord.utils.get(guild.roles, name=str(item.get("role", "")))
                        if not channel or not role or role.is_default() or role.managed: continue
                        raw_allow = item.get("allow", [])
                        raw_deny = item.get("deny", [])
                        if not isinstance(raw_allow, list) or not isinstance(raw_deny, list): continue
                        allow = clean_panel_permission_names(raw_allow)
                        deny = sorted(set(clean_panel_permission_names(raw_deny)) - set(allow))
                        clean["changes"].append({"channel_id": channel.id, "channel": channel.name, "role_id": role.id, "role": role.name, "allow": allow, "deny": deny})
                if action == "restructure" and not (clean["categories"] or clean["channels"] or clean["rename"]):
                    raise ValueError("Gemini did not produce any valid restructuring actions.")
                if action == "permissions" and not clean["changes"]:
                    raise ValueError("Gemini did not produce any valid permission changes.")
                cur = await db.execute(
                    "INSERT INTO server_change_plans(guild_id,created_by,kind,payload,status,created_at) VALUES(?,?,?,?, 'pending', datetime('now'))",
                    (guild.id, user_id, action, json.dumps(clean, ensure_ascii=False))
                )
                await self._report(command_id, "needs_confirmation",
                    f"Gemini prepared preview plan #{cur.lastrowid}: {clean['summary']}\n"
                    f"Review in Discord with /showplan {cur.lastrowid}. Nothing has changed. "
                    f"Only the actual server owner can apply it using /applyplan {cur.lastrowid} CONFIRM.")
                return
            except Exception as exc:
                log.exception("Panel Gemini plan creation failed")
                await self._report(command_id, "failed", f"Could not create a safe plan: {type(exc).__name__}.")
                return

        await self._report(command_id, "rejected",
            "That request is outside the panel's supported actions. Supported: server status, server restructuring plan, permission review plan, busy-mode status, and backup list. No changes were made.")

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
                    await self._report(int(item.get("id", 0)), "failed", "WardenX Manager could not process this request.")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            log.warning("Panel is unreachable; Discord bot continues normally.")
        except Exception:
            log.exception("Unexpected optional panel connector error.")

    @poll_panel.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(PanelConnector(bot))

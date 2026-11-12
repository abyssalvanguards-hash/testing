"""Hydrogen AI server planning with explicit owner approval.

AI output is treated as untrusted data. Only allow-listed create/rename and
role permission operations are accepted. No channel/category/role deletion.
"""
import json
import re
import discord
from discord.ext import commands
from core import db
from core.security import is_server_owner_or_global, safe_name
from core.logging import audit
from core.ai import AIUnavailable

ALLOWED_PERMS = {
    "view_channel", "send_messages", "read_message_history", "embed_links",
    "attach_files", "add_reactions", "manage_messages", "moderate_members",
    "manage_threads", "view_audit_log", "manage_channels", "manage_nicknames"
}

class AIServerManager(commands.Cog):
    def __init__(self, bot): self.bot = bot

    async def _scan(self, guild):
        roles = [{"name": r.name, "managed": r.managed, "permissions": [p for p,v in r.permissions if v and p in ALLOWED_PERMS]}
                 for r in guild.roles if not r.is_default()]
        channels = []
        for c in guild.channels:
            item = {"name": c.name, "type": "category" if isinstance(c, discord.CategoryChannel) else "text" if isinstance(c, discord.TextChannel) else "voice" if isinstance(c, discord.VoiceChannel) else "other"}
            if getattr(c, "category", None): item["category"] = c.category.name
            channels.append(item)
        return {"guild": guild.name, "roles": roles[:100], "channels": channels[:200]}

    async def _save_plan(self, ctx, kind, plan):
        payload = json.dumps(plan, ensure_ascii=False)
        cur = await db.execute("INSERT INTO server_change_plans(guild_id,created_by,kind,payload,status,created_at) VALUES(?,?,?,?, 'pending', datetime('now'))",
                               (ctx.guild.id, ctx.author.id, kind, payload))
        plan_id = cur.lastrowid
        await audit(self.bot, ctx.guild, "AI server plan created", f"Plan #{plan_id} • {kind} • awaiting owner approval", ctx.author)
        return plan_id

    @commands.hybrid_command(name="airestructure")
    async def ai_restructure(self, ctx):
        if not await is_server_owner_or_global(ctx.author, self.bot.owner_ids):
            return await ctx.reply("Only the server owner or configured WardenX Manager owner can request a restructuring plan.")
        scan = await self._scan(ctx.guild)
        prompt = ("You are planning a Discord server structure. Return ONLY JSON with keys summary, categories (array of category names), "
                  "channels (array of objects with name and category), and rename (array of objects with channel_id and new_name). "
                  "Preserve existing content. Never recommend deletion. Only suggest create/rename. Keep names <= 90 chars. "
                  "Current server snapshot:\n" + json.dumps(scan, ensure_ascii=False))
        try:
            raw = await self.bot.ai.ask(prompt, guild_id=ctx.guild.id, user_id=ctx.author.id, purpose="server_restructure")
            plan = self._parse_json(raw)
            clean = {"summary": str(plan.get("summary", "Proposed server organization"))[:500], "categories": [], "channels": [], "rename": []}
            for name in plan.get("categories", [])[:20]:
                if isinstance(name, str) and name.strip(): clean["categories"].append(name.strip()[:90])
            for item in plan.get("channels", [])[:60]:
                if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("category"), str):
                    clean["channels"].append({"name": item["name"][:90], "category": item["category"][:90]})
            existing_ids = {c.id for c in ctx.guild.channels}
            for item in plan.get("rename", [])[:30]:
                if isinstance(item, dict) and str(item.get("channel_id", "")).isdigit() and int(item["channel_id"]) in existing_ids and isinstance(item.get("new_name"), str):
                    clean["rename"].append({"channel_id": int(item["channel_id"]), "new_name": item["new_name"][:90]})
            pid = await self._save_plan(ctx, "restructure", clean)
            summary = [f"**Plan #{pid} — preview only**", clean["summary"], f"Create categories: {len(clean['categories'])}", f"Create channels: {len(clean['channels'])}", f"Rename channels: {len(clean['rename'])}", "No items will be deleted. Nothing has changed yet.", f"Review with `{ctx.prefix}showplan {pid}`. Apply only as the server owner with `{ctx.prefix}applyplan {pid} CONFIRM`. "]
            await ctx.reply("\n".join(summary))
        except (AIUnavailable, ValueError, TypeError, json.JSONDecodeError) as e:
            await ctx.reply(f"Could not create a safe plan: {str(e)[:300]}")

    @commands.hybrid_command(name="aipermissions")
    async def ai_permissions(self, ctx):
        if not await is_server_owner_or_global(ctx.author, self.bot.owner_ids):
            return await ctx.reply("Only the server owner or configured WardenX Manager owner can request a permission plan.")
        scan = await self._scan(ctx.guild)
        prompt = ("Review this Discord server snapshot and propose least-privilege permission overwrites. Return ONLY JSON: "
                  "{summary, changes:[{channel, role, allow:[permission_names], deny:[permission_names]}]}. "
                  "Use only roles/channels that exist in snapshot. Never grant administrator, manage_guild, manage_roles, "
                  "manage_webhooks, mention_everyone, or permissions not in the allow-list. Prefer small changes. "
                  "Snapshot:\n" + json.dumps(scan, ensure_ascii=False))
        try:
            raw = await self.bot.ai.ask(prompt, guild_id=ctx.guild.id, user_id=ctx.author.id, purpose="permission_review")
            plan = self._parse_json(raw); clean = {"summary": str(plan.get("summary", "Least-privilege permission review"))[:500], "changes": []}
            for item in plan.get("changes", [])[:60]:
                if not isinstance(item, dict): continue
                channel = discord.utils.get(ctx.guild.channels, name=str(item.get("channel", "")))
                role = discord.utils.get(ctx.guild.roles, name=str(item.get("role", "")))
                if not channel or not role or role.is_default() or role.managed: continue
                allow = sorted(set(item.get("allow", [])) & ALLOWED_PERMS)
                deny = sorted(set(item.get("deny", [])) & ALLOWED_PERMS)
                clean["changes"].append({"channel_id": channel.id, "channel": channel.name, "role_id": role.id, "role": role.name, "allow": allow, "deny": deny})
            pid = await self._save_plan(ctx, "permissions", clean)
            await ctx.reply(f"**Permission plan #{pid} — preview only**\n{clean['summary']}\nProposed channel/role overwrites: {len(clean['changes'])}. Administrator and dangerous permissions are blocked. Nothing changed. Review with `{ctx.prefix}showplan {pid}`; server owner applies with `{ctx.prefix}applyplan {pid} CONFIRM`.")
        except (AIUnavailable, ValueError, TypeError, json.JSONDecodeError) as e:
            await ctx.reply(f"Could not create a safe permission plan: {str(e)[:300]}")

    @commands.hybrid_command(name="showplan")
    async def show_plan(self, ctx, plan_id: int):
        row = await db.fetchone("SELECT * FROM server_change_plans WHERE id=? AND guild_id=?", (plan_id, ctx.guild.id))
        if not row: return await ctx.reply("Plan not found.")
        p = json.loads(row["payload"])
        lines = [f"**Plan #{plan_id} • {row['kind']} • {row['status']}**", str(p.get("summary", ""))]
        if row["kind"] == "restructure":
            lines += ["Categories: " + (", ".join(p.get("categories", [])) or "none"), "Channels: " + (", ".join(f"{x['name']} → {x['category']}" for x in p.get("channels", [])) or "none"), "Renames: " + (", ".join(f"{x['channel_id']} → {x['new_name']}" for x in p.get("rename", [])) or "none")]
        else:
            lines += [f"{x['channel']} / {x['role']}: allow={','.join(x['allow']) or 'none'}; deny={','.join(x['deny']) or 'none'}" for x in p.get("changes", [])]
        for i in range(0, len(lines), 1):
            pass
        await ctx.reply("\n".join(lines)[:1900])

    @commands.hybrid_command(name="applyplan")
    async def apply_plan(self, ctx, plan_id: int, confirmation: str = ""):
        if ctx.author.id != ctx.guild.owner_id:
            return await ctx.reply("Only the actual Discord server owner can apply server-wide plans.")
        if confirmation != "CONFIRM": return await ctx.reply(f"Nothing changed. Re-run `{ctx.prefix}applyplan {plan_id} CONFIRM` after reviewing the plan.")
        row = await db.fetchone("SELECT * FROM server_change_plans WHERE id=? AND guild_id=?", (plan_id, ctx.guild.id))
        if not row or row["status"] != "pending": return await ctx.reply("Pending plan not found or already processed.")
        p = json.loads(row["payload"]); bot_member = ctx.guild.me
        if not bot_member.guild_permissions.manage_channels and row["kind"] == "restructure": return await ctx.reply("Bot needs Manage Channels permission.")
        if row["kind"] == "permissions" and not bot_member.guild_permissions.manage_roles: return await ctx.reply("Bot needs Manage Roles permission.")
        results = []
        try:
            if row["kind"] == "restructure":
                cats = {c.name: c for c in ctx.guild.categories}
                for name in p.get("categories", []):
                    if name not in cats: cats[name] = await ctx.guild.create_category(name, reason=f"Hydrogen plan #{plan_id}"); results.append("Created category " + name)
                for item in p.get("channels", []):
                    cat = cats.get(item["category"]) or discord.utils.get(ctx.guild.categories, name=item["category"])
                    if cat and not discord.utils.get(ctx.guild.text_channels, name=item["name"]):
                        await ctx.guild.create_text_channel(item["name"], category=cat, reason=f"Hydrogen plan #{plan_id}"); results.append("Created #" + item["name"])
                for item in p.get("rename", []):
                    channel = ctx.guild.get_channel(item["channel_id"])
                    if channel and channel.id != ctx.channel.id and channel.name != item["new_name"]:
                        await channel.edit(name=safe_name(item["new_name"]), reason=f"Hydrogen plan #{plan_id}"); results.append(f"Renamed channel {item['channel_id']}")
            elif row["kind"] == "permissions":
                for item in p.get("changes", []):
                    channel = ctx.guild.get_channel(item["channel_id"]); role = ctx.guild.get_role(item["role_id"])
                    if not channel or not role or role.is_default() or role.managed or role >= bot_member.top_role: continue
                    overwrite = channel.overwrites_for(role)
                    for perm in item["allow"]: setattr(overwrite, perm, True)
                    for perm in item["deny"]: setattr(overwrite, perm, False)
                    await channel.set_permissions(role, overwrite=overwrite, reason=f"Hydrogen plan #{plan_id}")
                    results.append(f"Updated {channel.name} / {role.name}")
            await db.execute("UPDATE server_change_plans SET status='applied' WHERE id=?", (plan_id,))
            await audit(self.bot, ctx.guild, "AI server plan applied", f"Plan #{plan_id} • {len(results)} changes", ctx.author)
            await ctx.reply("Plan applied.\n" + ("\n".join(results[:20]) or "No changes were needed."))
        except discord.HTTPException as e:
            await db.execute("UPDATE server_change_plans SET status='failed' WHERE id=?", (plan_id,))
            await audit(self.bot, ctx.guild, "AI server plan failed", f"Plan #{plan_id}: {type(e).__name__}", ctx.author)
            await ctx.reply("Discord rejected part of the plan. Some earlier actions may already have succeeded; inspect the server and audit log before retrying.")

    @staticmethod
    def _parse_json(raw):
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start: raise ValueError("AI did not return a JSON plan.")
        obj = json.loads(raw[start:end+1])
        if not isinstance(obj, dict): raise ValueError("Invalid plan format.")
        return obj

async def setup(bot): await bot.add_cog(AIServerManager(bot))

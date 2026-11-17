import asyncio, os, logging, json
from pathlib import Path
import discord
from discord.ext import commands
from dotenv import load_dotenv
import config
from core import db
from core.ai import GeminiService

try:
    import private_config as private
except Exception:
    private = None

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TOKEN = getattr(private, "DISCORD_TOKEN", None) or os.getenv("DISCORD_TOKEN")
CONFIG_PATH = Path(__file__).with_name("config.json")
try:
    BOT_CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    BOT_CONFIG = {}

OWNER_IDS = list(dict.fromkeys(
    [int(x) for x in (getattr(private, "OWNER_IDS", []) if private else [])]
    + [int(x) for x in BOT_CONFIG.get("owners", {}).get("user_ids", [])]
))
API_KEY_MODERATOR_IDS = {
    int(x) for x in BOT_CONFIG.get("permissions", {}).get("api_key_moderator_user_ids", [])
}
AI_KEYS = getattr(private, "GEMINI_API_KEYS", []) if private else []


class HydrogenBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        intents.invites = True
        super().__init__(command_prefix=self.prefix_resolver, intents=intents, help_command=None)
        self.owner_ids = [int(x) for x in OWNER_IDS]
        self._synced_guilds = set()
        ai_config = BOT_CONFIG.get("ai", {})
        model = ai_config.get("model") or (getattr(private, "GEMINI_MODEL", None) if private else None)
        fallback_model = ai_config.get("fallback_model") or (getattr(private, "GEMINI_FALLBACK_MODEL", None) if private else None)
        self.api_key_moderator_ids = set(API_KEY_MODERATOR_IDS)
        self.ai = GeminiService(AI_KEYS, model, fallback_model)
        self.ai.owner_notifier = self.notify_ai_owner

    async def notify_ai_owner(self, event, key_number, detail):
        labels = {
            "invalid_key": "API key rejected",
            "authorization_failure": "API authorization failed",
            "quota_or_rate_limit": "Project quota or rate limit reached"
        }
        message = (
            f"WardenX Gemini alert: {labels.get(event, 'Key health event')}\\n"
            f"Key slot: #{key_number} (secret hidden)\\n"
            f"Details: {detail}\\n"
            "Check Google AI Studio/Cloud quota and key restrictions. "
            "A new key in the same Google project may share the same project quota."
        )
        for owner_id in self.owner_ids:
            try:
                user = self.get_user(owner_id) or await self.fetch_user(owner_id)
                await user.send(message)
            except Exception:
                logging.warning("Could not DM configured owner ID %s about Gemini status.", owner_id)

    async def prefix_resolver(self, bot, message):
        if not message.guild:
            return config.DEFAULT_PREFIX
        return await db.get_setting(message.guild.id, "prefix", config.DEFAULT_PREFIX)

    async def setup_hook(self):
        await db.init_db()
        extensions = [
            "core_admin", "tickets", "applications", "moderation", "community",
            "payments", "setup_wizard", "ai_server_manager",
            "gemini_admin", "invite_rewards", "server_builder", "server_care", "panel_connector"
        ]
        for name in extensions:
            await self.load_extension(f"cogs.{name}")

    async def on_ready(self):
        logging.info("WardenX Manager online as %s | guilds=%s", self.user, len(self.guilds))
        for guild in self.guilds:
            await db.ensure_guild(guild.id)
            if guild.id in self._synced_guilds:
                continue
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                self._synced_guilds.add(guild.id)
                logging.info("Synced %s slash commands to %s (%s)", len(synced), guild.name, guild.id)
            except discord.HTTPException as exc:
                logging.error("Slash-command sync failed for guild %s (%s): %s", guild.name, guild.id, exc)
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="WardenX Manager")
        )

    async def on_guild_join(self, guild):
        await db.ensure_guild(guild.id)
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            self._synced_guilds.add(guild.id)
        except discord.HTTPException as exc:
            logging.error("Slash-command sync failed for new guild %s: %s", guild.id, exc)


if __name__ == "__main__":
    if not TOKEN or "PASTE_" in TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in private_config.py or environment before running.")
    HydrogenBot().run(TOKEN)

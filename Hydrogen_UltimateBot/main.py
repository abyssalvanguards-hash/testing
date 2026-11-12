import asyncio, os, logging, importlib.util
import discord
from discord.ext import commands
from dotenv import load_dotenv
import config
from core import db
from core.ai import GeminiService
try:
    import private_config as private
except Exception:
    private=None

load_dotenv()
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TOKEN=getattr(private,"DISCORD_TOKEN",None) or os.getenv("DISCORD_TOKEN")
OWNER_IDS=getattr(private,"OWNER_IDS",[]) if private else []
AI_KEYS=getattr(private,"GEMINI_API_KEYS",[]) if private else []

class HydrogenBot(commands.Bot):
    def __init__(self):
        intents=discord.Intents.default()
        intents.guilds=True; intents.members=True; intents.message_content=True
        super().__init__(command_prefix=self.prefix_resolver,intents=intents,help_command=None)
        self.owner_ids=[int(x) for x in OWNER_IDS]
        self._synced_guilds=set()
        self.ai=GeminiService(AI_KEYS, getattr(private, "GEMINI_MODEL", "gemini-3.8-flash") if private else "gemini-3.8-flash")
    async def prefix_resolver(self,bot,message):
        if not message.guild: return config.DEFAULT_PREFIX
        return await db.get_setting(message.guild.id,"prefix",config.DEFAULT_PREFIX)
    async def setup_hook(self):
        await db.init_db()
        for name in ["core_admin","tickets","applications","moderation","community","payments","slots","setup_wizard","ai_server_manager","gemini_admin"]:
            await self.load_extension(f"cogs.{name}")
        # Persistent application view is registered by its extension.
        # Slash commands are synced per guild in on_ready for immediate visibility.
    async def on_ready(self):
        print(f"Hydrogen UltimateBot online as {self.user} | guilds={len(self.guilds)}")
        # Guild-scoped sync makes slash commands appear immediately instead of
        # waiting for Discord's global-command propagation window.
        for guild in self.guilds:
            if guild.id in self._synced_guilds:
                continue
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                self._synced_guilds.add(guild.id)
                logging.info("Synced %s slash commands to %s (%s)", len(synced), guild.name, guild.id)
            except discord.HTTPException as exc:
                logging.error("Slash-command sync failed for guild %s (%s): %s", guild.name, guild.id, exc)
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,name="Hydrogen UltimateBot"))
    async def on_guild_join(self,guild):
        await db.ensure_guild(guild.id)
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            self._synced_guilds.add(guild.id)
        except discord.HTTPException as exc:
            logging.error("Slash-command sync failed for new guild %s: %s", guild.id, exc)

if __name__=="__main__":
    if not TOKEN or "PASTE_" in TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in private_config.py or environment before running.")
    bot=HydrogenBot()
    bot.run(TOKEN)

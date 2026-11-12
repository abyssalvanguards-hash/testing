"""Owner-only Gemini key selection and diagnostics."""
import discord
from discord.ext import commands

from core.ai import AIUnavailable
from core.security import is_global_owner


class GeminiAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _owner_only(self, ctx):
        if not is_global_owner(ctx.author.id, self.bot.owner_ids):
            await ctx.reply(
                "Only configured WardenX Manager bot owners can manage Gemini keys.",
                ephemeral=True
            )
            return False
        return True

    @commands.hybrid_group(
        name="gemini",
        invoke_without_command=True,
        description="Manage WardenX Manager Gemini settings"
    )
    async def gemini(self, ctx):
        await ctx.reply(
            "Use `gemini status`, `gemini keys`, `gemini switch <number>`, or `gemini test`.",
            ephemeral=True
        )

    @gemini.command(name="status", description="Show the active Gemini key number and model")
    async def status(self, ctx):
        if not await self._owner_only(ctx):
            return
        ai = self.bot.ai
        total = len(ai.keys)
        selected = ai.active_key_index + 1 if ai.active_key else 0
        key_status = "Configured" if ai.active_key else "Not configured"
        await ctx.reply(
            f"**Gemini status**\nModel: `{ai.model}`\n"
            f"Configured keys: `{total}`\nActive key: `{selected}`\n"
            f"Active key status: **{key_status}**",
            ephemeral=True
        )

    @gemini.command(name="keys", description="List configured Gemini key slots without revealing key data")
    async def keys(self, ctx):
        if not await self._owner_only(ctx):
            return
        ai = self.bot.ai
        if not ai.keys:
            return await ctx.reply(
                "No Gemini keys are configured in private_config.py.",
                ephemeral=True
            )
        lines = []
        for index in range(len(ai.keys)):
            marker = " ← active" if index == ai.active_key_index else ""
            lines.append(f"`{index + 1}` • Configured{marker}")
        await ctx.reply(
            "**Configured Gemini key slots**\n" + "\n".join(lines),
            ephemeral=True
        )

    @gemini.command(name="switch", description="Manually select a configured Gemini key")
    async def switch(self, ctx, number: int):
        if not await self._owner_only(ctx):
            return
        try:
            selected = self.bot.ai.switch_key(number)
        except ValueError as exc:
            return await ctx.reply(
                f"{exc} Use `/gemini keys` to see configured key numbers.",
                ephemeral=True
            )
        await ctx.reply(
            f"Selected Gemini key **{selected}**. This selection is saved across restarts.",
            ephemeral=True
        )

    @gemini.command(name="test", description="Send a small test request using the selected Gemini key")
    async def test(self, ctx):
        if not await self._owner_only(ctx):
            return
        await ctx.defer(ephemeral=True)
        try:
            result = await self.bot.ai.ask(
                "Reply with exactly: WardenX Manager Gemini connection OK.",
                purpose="owner_test"
            )
            await ctx.send(
                f"Gemini test succeeded using key **{self.bot.ai.active_key_index + 1}**.\n"
                f"`{result[:300]}`",
                ephemeral=True
            )
        except AIUnavailable as exc:
            await ctx.send(f"Gemini test failed: {exc}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(GeminiAdmin(bot))

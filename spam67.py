import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from permissions import SUPER_USER_ID as AUTHORIZED_SAY_USER_ID

SPAM_CHANNEL_ID = 1541766493258260550
SPAM_MESSAGE = "67"
SPAM_INTERVAL_SECONDS = 1.5


class Spam67(commands.Cog):
    """Owner-only novelty toggle -- NOT for real spam. Paced at 1.5s/message
    (well under Discord's rate limits) so sustained use doesn't trip
    Discord's own automated abuse detection. Runs until /spam67 off, a bot
    restart, or the cog unloading -- no message cap, so don't forget it's on."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: asyncio.Task | None = None

    def cog_unload(self):
        if self._running():
            self._task.cancel()

    def _running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _loop(self, channel: discord.TextChannel):
        try:
            while True:
                try:
                    await channel.send(SPAM_MESSAGE)
                except discord.HTTPException:
                    pass
                await asyncio.sleep(SPAM_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None  # lets /spam67 on work again after a stop

    async def _start(self) -> str:
        if self._running():
            return "Already running."
        channel = self.bot.get_channel(SPAM_CHANNEL_ID)
        if channel is None:
            return "Can't find the configured channel -- check SPAM_CHANNEL_ID in spam67.py."
        self._task = asyncio.create_task(self._loop(channel))
        return f"🟢 Started -- posting \"{SPAM_MESSAGE}\" in {channel.mention} every {SPAM_INTERVAL_SECONDS}s. Runs until you run this again with `off`."

    async def _stop(self) -> str:
        if not self._running():
            return "It's not running."
        self._task.cancel()
        return "🔴 Stopped."

    @app_commands.command(name="spam67", description="[Owner] Toggle the 67 spam loop")
    @app_commands.describe(action="Turn it on or off")
    @app_commands.choices(action=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    async def spam67(self, interaction: discord.Interaction, action: app_commands.Choice[str]):
        if interaction.user.id != AUTHORIZED_SAY_USER_ID:
            await interaction.response.send_message("You can't use this command.", ephemeral=True)
            return
        result = await (self._start() if action.value == "on" else self._stop())
        await interaction.response.send_message(result, ephemeral=True)

    @commands.command(name="spam67")
    async def spam67_text(self, ctx: commands.Context, action: str = None):
        if ctx.author.id != AUTHORIZED_SAY_USER_ID:
            return  # stay quiet, same as say_text/sync_text
        action = (action or "").lower()
        if action not in ("on", "off"):
            await ctx.reply(f"Usage: `{ctx.prefix}spam67 on` or `{ctx.prefix}spam67 off`", mention_author=False)
            return
        result = await (self._start() if action == "on" else self._stop())
        await ctx.reply(result, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Spam67(bot))

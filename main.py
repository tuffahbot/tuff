import asyncio
import logging
import os
import shutil

import discord
from discord.ext import commands
from dotenv import load_dotenv

from database import init_db, DB_PATH

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("COMMAND_PREFIX", "?")

intents = discord.Intents.default()
intents.message_content = True   # required for XP-on-message, prefix commands, and deleted/edited message logging
intents.members = True           # required for role assignment on level-up, warn/kick/ban targeting, join/leave logging
intents.voice_states = True      # required for music playback and join-to-create voice channels

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
bot.start_time = discord.utils.utcnow()  # process start, for /uptime -- set once here, not in on_ready (which can refire on reconnects)

STARTUP_EXTENSIONS = (
    "general",
    "music",
    "leveling",
    "moderation",
    "voice",
    "eventlogs",
    "giveaways",
    "emoji",
    "autorole",
    "polls",
    "roles",
    "fun",
    "modapps",
    "afk",
)


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        log.info(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        log.exception(f"Failed to sync slash commands: {e}")
    await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Activity(type=discord.ActivityType.listening, name="/play | /rank"),
    )


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    # If the command's cog (e.g. Moderation, Emoji) or the command itself
    # already has its own error handler, let that handle it and don't also
    # reply here -- otherwise commands like ?ban end up sending two replies.
    if ctx.cog and ctx.cog.has_error_handler():
        return
    if ctx.command and ctx.command.has_error_handler():
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have permission to do that.")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`. Check `{ctx.prefix}help {ctx.command}`.")
        return
    log.exception("Unhandled command error", exc_info=error)
    try:
        await ctx.send(f"Something went wrong: `{error}`")
    except discord.Forbidden:
        pass


async def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set. Add it to your .env or Railway environment variables.")

    if shutil.which("ffmpeg") is None:
        log.warning(
            "ffmpeg was not found on PATH -- /play will fail with 'ffmpeg was not found' until this is fixed. "
            "On Railway this means the aptPkgs/nixPkgs entry in nixpacks.toml isn't being picked up (often a "
            "stale build cache) -- try a fresh deploy with the build cache cleared. Locally: apt install ffmpeg "
            "/ brew install ffmpeg."
        )
    else:
        log.info(f"ffmpeg found at {shutil.which('ffmpeg')}")

    if DB_PATH == "bot.db":
        log.warning(
            "DB_PATH is not set -- using the default 'bot.db' in the container's own filesystem, "
            "which is WIPED on every redeploy/restart. If you meant to persist data on a Railway "
            "Volume, set DB_PATH on the WORKER SERVICE's own Variables tab (not just Project "
            "Settings -> Shared Variables -- that alone doesn't inject it into the service)."
        )
    elif not DB_PATH.startswith("/"):
        log.warning(
            f"DB_PATH is set to '{DB_PATH}', which isn't an absolute path (doesn't start with '/') "
            "-- it'll be treated as relative to the container's working directory, NOT your mounted "
            "volume, so data won't survive a redeploy. This is usually a stray space or typo in the "
            "Railway variable's value -- it should be exactly /data/bot.db."
        )
    else:
        log.info(f"Using database at {DB_PATH}")

    init_db()

    async with bot:
        for ext in STARTUP_EXTENSIONS:
            await bot.load_extension(ext)
            log.info(f"Loaded extension: {ext}")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

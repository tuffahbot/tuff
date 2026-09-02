import re
from collections import deque

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from logsutil import send_log
from permissions import SUPER_USER_ID

SPAM_MESSAGE_THRESHOLD = 5   # this many messages...
SPAM_WINDOW_SECONDS = 5      # ...within this many seconds gets the whole burst deleted


class AutoMod(commands.Cog):
    """Banned-word filter + basic spam/flood detection. Staff (anyone with
    Manage Messages) are exempt from both."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._pattern_cache: dict[int, re.Pattern | None] = {}
        self._recent_messages: dict[tuple[int, int], deque] = {}  # (guild_id, user_id) -> recent message timestamps

    def _get_pattern(self, guild_id: int) -> re.Pattern | None:
        if guild_id not in self._pattern_cache:
            self._rebuild_pattern(guild_id)
        return self._pattern_cache[guild_id]

    def _rebuild_pattern(self, guild_id: int):
        words = db.get_automod_words(guild_id)
        if not words:
            self._pattern_cache[guild_id] = None
            return
        escaped = [re.escape(w) for w in words]
        self._pattern_cache[guild_id] = re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)

    async def _check_spam(self, message: discord.Message) -> bool:
        """Flood/spam detection: SPAM_MESSAGE_THRESHOLD messages within
        SPAM_WINDOW_SECONDS from the same person gets the WHOLE burst
        deleted, not just the message that tipped it over. Returns True if
        this message was handled as spam (so on_message knows not to also
        run the word filter on an already-deleted message)."""
        key = (message.guild.id, message.author.id)
        history = self._recent_messages.setdefault(key, deque(maxlen=SPAM_MESSAGE_THRESHOLD))
        history.append(message)

        if len(history) < SPAM_MESSAGE_THRESHOLD:
            return False
        elapsed = (message.created_at - history[0].created_at).total_seconds()
        if elapsed > SPAM_WINDOW_SECONDS:
            return False

        burst = list(history)
        history.clear()  # reset so this doesn't re-trigger on every single message until they actually slow down

        try:
            await message.channel.delete_messages(burst)
        except discord.HTTPException:
            # bulk delete can fail (e.g. a message already gone) -- fall back to one-by-one
            for m in burst:
                try:
                    await m.delete()
                except discord.HTTPException:
                    pass

        try:
            await message.channel.send(f"🛡️ {message.author.mention}, slow down -- that looked like spam.", delete_after=6)
        except discord.Forbidden:
            pass

        log_embed = discord.Embed(
            title="🛡️ AutoMod: Spam Removed",
            description=(
                f"**Author:** {message.author.mention}\n"
                f"**Channel:** {message.channel.mention}\n"
                f"**Trigger:** {SPAM_MESSAGE_THRESHOLD} messages within {SPAM_WINDOW_SECONDS}s\n"
                f"**Messages deleted:** {len(burst)}\n"
                f"**Last message:** {message.content[:1000] or '*(no text content)*'}"
            ),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        await send_log(self.bot, log_embed)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not db.get_automod_enabled(message.guild.id):
            return
        if message.author.id == SUPER_USER_ID or message.author.guild_permissions.manage_messages:
            return  # staff exempt

        if await self._check_spam(message):
            return  # already deleted -- don't also run the word filter on it

        pattern = self._get_pattern(message.guild.id)
        if pattern is None:
            return
        match = pattern.search(message.content)
        if not match:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        try:
            await message.channel.send(
                f"🛡️ {message.author.mention}, that message was removed for containing a blocked word.",
                delete_after=6,
            )
        except discord.Forbidden:
            pass

        log_embed = discord.Embed(
            title="🛡️ AutoMod: Message Removed",
            description=(
                f"**Author:** {message.author.mention}\n"
                f"**Channel:** {message.channel.mention}\n"
                f"**Matched:** ||{match.group(0)}||\n"
                f"**Content:** {message.content[:1000]}"
            ),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        await send_log(self.bot, log_embed)

    # ---------------- Slash commands ----------------

    automod = app_commands.Group(name="automod", description="Configure the banned-word filter and spam detection", default_permissions=discord.Permissions(manage_guild=True))

    @automod.command(name="enable", description="[Admin] Turn AutoMod on (word filter + spam detection)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_enable(self, interaction: discord.Interaction):
        db.set_automod_enabled(interaction.guild_id, True)
        await interaction.response.send_message("🛡️ AutoMod enabled (word filter + spam detection).", ephemeral=True)

    @automod.command(name="disable", description="[Admin] Turn AutoMod off (word filter + spam detection)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_disable(self, interaction: discord.Interaction):
        db.set_automod_enabled(interaction.guild_id, False)
        await interaction.response.send_message("AutoMod disabled.", ephemeral=True)

    @automod.command(name="addword", description="[Admin] Add a word/phrase to the filter")
    @app_commands.describe(word="The word or phrase to block")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_addword(self, interaction: discord.Interaction, word: str):
        word = word.strip().lower()
        if not word:
            await interaction.response.send_message("Give me an actual word.", ephemeral=True)
            return
        added = db.add_automod_word(interaction.guild_id, word)
        self._rebuild_pattern(interaction.guild_id)
        await interaction.response.send_message(
            f"✅ Added `{word}` to the filter." if added else f"`{word}` is already filtered.", ephemeral=True
        )

    @automod.command(name="removeword", description="[Admin] Remove a word/phrase from the filter")
    @app_commands.describe(word="The word or phrase to unblock")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_removeword(self, interaction: discord.Interaction, word: str):
        word = word.strip().lower()
        removed = db.remove_automod_word(interaction.guild_id, word)
        self._rebuild_pattern(interaction.guild_id)
        await interaction.response.send_message(
            f"🧹 Removed `{word}` from the filter." if removed else f"`{word}` wasn't in the filter.", ephemeral=True
        )

    @automod.command(name="words", description="List filtered words")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_words(self, interaction: discord.Interaction):
        words = db.get_automod_words(interaction.guild_id)
        if not words:
            await interaction.response.send_message("No words are filtered yet.", ephemeral=True)
            return
        await interaction.response.send_message("Filtered words: " + ", ".join(f"`{w}`" for w in words), ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Server permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    # ---------------- Prefix commands ----------------

    @commands.group(name="automod", invoke_without_command=True)
    async def automod_text(self, ctx: commands.Context):
        await ctx.reply(
            f"Usage: `{ctx.prefix}automod enable`, `{ctx.prefix}automod disable`, "
            f"`{ctx.prefix}automod addword <word>`, `{ctx.prefix}automod removeword <word>`, `{ctx.prefix}automod words`",
            mention_author=False,
        )

    @automod_text.command(name="enable")
    @commands.has_permissions(manage_guild=True)
    async def automod_text_enable(self, ctx: commands.Context):
        db.set_automod_enabled(ctx.guild.id, True)
        await ctx.reply("🛡️ AutoMod enabled (word filter + spam detection).", mention_author=False)

    @automod_text.command(name="disable")
    @commands.has_permissions(manage_guild=True)
    async def automod_text_disable(self, ctx: commands.Context):
        db.set_automod_enabled(ctx.guild.id, False)
        await ctx.reply("AutoMod disabled.", mention_author=False)

    @automod_text.command(name="addword")
    @commands.has_permissions(manage_guild=True)
    async def automod_text_addword(self, ctx: commands.Context, *, word: str):
        word = word.strip().lower()
        added = db.add_automod_word(ctx.guild.id, word)
        self._rebuild_pattern(ctx.guild.id)
        await ctx.reply(f"✅ Added `{word}` to the filter." if added else f"`{word}` is already filtered.", mention_author=False)

    @automod_text.command(name="removeword")
    @commands.has_permissions(manage_guild=True)
    async def automod_text_removeword(self, ctx: commands.Context, *, word: str):
        word = word.strip().lower()
        removed = db.remove_automod_word(ctx.guild.id, word)
        self._rebuild_pattern(ctx.guild.id)
        await ctx.reply(f"🧹 Removed `{word}` from the filter." if removed else f"`{word}` wasn't in the filter.", mention_author=False)

    @automod_text.command(name="words")
    @commands.has_permissions(manage_guild=True)
    async def automod_text_words(self, ctx: commands.Context):
        words = db.get_automod_words(ctx.guild.id)
        if not words:
            await ctx.reply("No words are filtered yet.", mention_author=False)
            return
        await ctx.reply("Filtered words: " + ", ".join(f"`{w}`" for w in words), mention_author=False)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Server permission to do that.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Usage: `{ctx.prefix}automod addword <word>`", mention_author=False)
        else:
            print(f"AutoMod prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))

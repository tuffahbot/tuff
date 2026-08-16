import asyncio
import re

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")
MAX_CONCURRENT_CREATES = 4  # keep Discord's emoji-creation rate limit happy
MAX_LISTED = 40  # cap how many emoji we actually list out in a results embed


class Emoji(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _find_emojis(self, *texts: str) -> list[tuple[str, str, str]]:
        """Returns a list of (animated_flag, name, id) tuples, de-duplicated by id."""
        seen = {}
        for text in texts:
            for animated, name, emoji_id in EMOJI_RE.findall(text or ""):
                seen[emoji_id] = (animated, name, emoji_id)
        return list(seen.values())

    async def _download(self, session: aiohttp.ClientSession, animated: bool, emoji_id: str) -> bytes | None:
        ext = "gif" if animated else "png"
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
        except aiohttp.ClientError:
            return None

    async def _create_one(self, guild: discord.Guild, session: aiohttp.ClientSession, name: str, animated: bool,
                           emoji_id: str, reason: str, sem: asyncio.Semaphore) -> tuple[discord.Emoji | None, str | None]:
        """Returns (created_emoji, None) on success or (None, name) on failure."""
        image_bytes = await self._download(session, animated, emoji_id)
        if image_bytes is None:
            return None, name
        async with sem:  # only the actual Discord API call needs throttling, not the CDN download
            try:
                new_emoji = await guild.create_custom_emoji(name=name, image=image_bytes, reason=reason)
                return new_emoji, None
            except discord.HTTPException:
                return None, name

    async def _steal_many(self, guild: discord.Guild, requester, found: list[tuple[str, str, str]]) -> tuple[list[discord.Emoji], list[str]]:
        """Downloads + creates every emoji in `found` concurrently. Returns (added_emojis, failed_names)."""
        sem = asyncio.Semaphore(MAX_CONCURRENT_CREATES)
        reason = f"Stolen by {requester}"
        async with aiohttp.ClientSession() as session:
            tasks = [
                self._create_one(guild, session, name, bool(animated), emoji_id, reason, sem)
                for animated, name, emoji_id in found
            ]
            results = await asyncio.gather(*tasks)
        added = [emoji for emoji, _ in results if emoji is not None]
        failed = [name for _, name in results if name is not None]
        return added, failed

    def _results_text(self, added: list[discord.Emoji], failed: list[str]) -> str:
        lines = []
        if added:
            lines.append("✅ Added: " + " ".join(str(e) for e in added))
        if failed:
            lines.append("❌ Couldn't add: " + ", ".join(failed) + " (name taken, emoji slots full, or file too big)")
        return "\n".join(lines) or "Nothing was added."

    def _results_embed(self, added: list[discord.Emoji], failed: list[str], *, source_name: str = None) -> discord.Embed:
        title = "Emoji Steal Results" + (f" — from {source_name}" if source_name else "")
        embed = discord.Embed(title=title, color=discord.Color.green() if added else discord.Color.red())

        if added:
            text = " ".join(str(e) for e in added[:MAX_LISTED])
            if len(added) > MAX_LISTED:
                text += f"\n*+{len(added) - MAX_LISTED} more*"
            embed.add_field(name=f"✅ Added ({len(added)})", value=text[:1024], inline=False)
        if failed:
            text = ", ".join(failed[:MAX_LISTED])
            if len(failed) > MAX_LISTED:
                text += f", +{len(failed) - MAX_LISTED} more"
            embed.add_field(name=f"❌ Couldn't add ({len(failed)})", value=(text or "?")[:1024], inline=False)
        if not added and not failed:
            embed.description = "Nothing was added."
        return embed

    # ---------------- Steal pasted/replied emoji ----------------

    @commands.command(name="steal")
    @commands.has_permissions(manage_emojis_and_stickers=True)
    async def steal(self, ctx: commands.Context, *, emojis: str = ""):
        """Steals custom emoji(s) into this server. Either paste the emoji(s)
        after the command, or reply to a message containing them and just
        run the command with no arguments."""
        sources = [emojis]
        if ctx.message.reference and ctx.message.reference.resolved:
            ref = ctx.message.reference.resolved
            if isinstance(ref, discord.Message):
                sources.append(ref.content)

        found = self._find_emojis(*sources)
        if not found:
            await ctx.reply("Couldn't find any custom emoji there -- paste one/reply to a message with one.", mention_author=False, delete_after=8)
            return

        added, failed = await self._steal_many(ctx.guild, ctx.author, found)
        await ctx.reply(self._results_text(added, failed), mention_author=False)

    @steal.error
    async def steal_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Emojis and Stickers permission to do that.", mention_author=False, delete_after=8)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.reply("I need the Manage Emojis and Stickers permission to do that.", mention_author=False, delete_after=8)

    @app_commands.command(name="steal", description="Steal custom emoji(s) into this server")
    @app_commands.describe(emojis="Paste the custom emoji(s) you want to steal")
    @app_commands.checks.has_permissions(manage_emojis_and_stickers=True)
    async def steal_slash(self, interaction: discord.Interaction, emojis: str):
        found = self._find_emojis(emojis)
        if not found:
            await interaction.response.send_message("Couldn't find any custom emoji in that -- paste the actual emoji(s), not just the name.", ephemeral=True)
            return
        await interaction.response.defer()
        added, failed = await self._steal_many(interaction.guild, interaction.user, found)
        await interaction.followup.send(self._results_text(added, failed))

    # ---------------- Steal every emoji from a whole server ----------------

    def _resolve_source_guild(self, current_guild_id: int, server_id: int) -> tuple[discord.Guild | None, str | None]:
        source = self.bot.get_guild(server_id)
        if source is None:
            return None, "I'm not in that server (or the ID's wrong) -- I can only steal from servers I'm already a member of."
        if source.id == current_guild_id:
            return None, "That's this server."
        if not source.emojis:
            return None, f"**{source.name}** doesn't have any custom emoji."
        return source, None

    @app_commands.command(name="stealserver", description="Steal every custom emoji from another server the bot is in")
    @app_commands.describe(server_id="The source server's ID (bot must already be a member of it)")
    @app_commands.checks.has_permissions(manage_emojis_and_stickers=True)
    async def stealserver(self, interaction: discord.Interaction, server_id: str):
        try:
            source_id = int(server_id)
        except ValueError:
            await interaction.response.send_message("That doesn't look like a valid server ID.", ephemeral=True)
            return

        source, error = self._resolve_source_guild(interaction.guild_id, source_id)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        await interaction.response.defer()
        await interaction.followup.send(f"⏳ Found **{len(source.emojis)}** emoji in **{source.name}** -- stealing now, this can take a bit for a lot of emoji.")
        found = [("a" if e.animated else "", e.name, str(e.id)) for e in source.emojis]
        added, failed = await self._steal_many(interaction.guild, interaction.user, found)
        await interaction.followup.send(embed=self._results_embed(added, failed, source_name=source.name))

    @commands.command(name="stealserver")
    @commands.has_permissions(manage_emojis_and_stickers=True)
    async def stealserver_text(self, ctx: commands.Context, server_id: int):
        source, error = self._resolve_source_guild(ctx.guild.id, server_id)
        if error:
            await ctx.reply(error, mention_author=False)
            return

        await ctx.reply(f"⏳ Found **{len(source.emojis)}** emoji in **{source.name}** -- stealing now, this can take a bit for a lot of emoji.", mention_author=False)
        found = [("a" if e.animated else "", e.name, str(e.id)) for e in source.emojis]
        added, failed = await self._steal_many(ctx.guild, ctx.author, found)
        await ctx.send(embed=self._results_embed(added, failed, source_name=source.name))

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Emojis and Stickers permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if ctx.command and ctx.command.name == "steal":
            return  # handled by steal_error above
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Emojis and Stickers permission to do that.", mention_author=False, delete_after=8)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.reply("I need the Manage Emojis and Stickers permission to do that.", mention_author=False, delete_after=8)
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("That server ID needs to be a plain number.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Usage: `{ctx.prefix}stealserver <server_id>`", mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Emoji(bot))

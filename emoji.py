import re

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")


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

    async def _steal(self, guild: discord.Guild, requester, found: list[tuple[str, str, str]]) -> str:
        """Does the actual downloading/creating and returns a result message."""
        added, failed = [], []
        async with aiohttp.ClientSession() as session:
            for animated, name, emoji_id in found:
                ext = "gif" if animated else "png"
                url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
                try:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            failed.append(name)
                            continue
                        image_bytes = await resp.read()
                    new_emoji = await guild.create_custom_emoji(name=name, image=image_bytes, reason=f"Stolen by {requester}")
                    added.append(str(new_emoji))
                except discord.HTTPException:
                    failed.append(name)

        lines = []
        if added:
            lines.append("✅ Added: " + " ".join(added))
        if failed:
            lines.append("❌ Couldn't add: " + ", ".join(failed) + " (name taken, emoji slots full, or file too big)")
        return "\n".join(lines) or "Nothing was added."

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

        result = await self._steal(ctx.guild, ctx.author, found)
        await ctx.reply(result, mention_author=False)

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
        result = await self._steal(interaction.guild, interaction.user, found)
        await interaction.followup.send(result)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Emojis and Stickers permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Emoji(bot))

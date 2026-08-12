import re

import aiohttp
import discord
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
                    new_emoji = await ctx.guild.create_custom_emoji(name=name, image=image_bytes, reason=f"Stolen by {ctx.author}")
                    added.append(str(new_emoji))
                except discord.HTTPException:
                    failed.append(name)

        lines = []
        if added:
            lines.append("✅ Added: " + " ".join(added))
        if failed:
            lines.append("❌ Couldn't add: " + ", ".join(failed) + " (name taken, emoji slots full, or file too big)")
        await ctx.reply("\n".join(lines) or "Nothing was added.", mention_author=False)

    @steal.error
    async def steal_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Emojis and Stickers permission to do that.", mention_author=False, delete_after=8)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.reply("I need the Manage Emojis and Stickers permission to do that.", mention_author=False, delete_after=8)


async def setup(bot: commands.Bot):
    await bot.add_cog(Emoji(bot))

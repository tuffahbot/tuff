import random

import discord
from discord import app_commands
from discord.ext import commands


def ship_percent() -> float:
    """Random 'compatibility' score, 0.00 - 100.00, reroll each time."""
    return round(random.uniform(0, 100), 2)


def ship_name(name_a: str, name_b: str) -> str:
    half_a = name_a[: max(1, len(name_a) // 2)]
    half_b = name_b[len(name_b) // 2:] or name_b
    return (half_a + half_b).title()


def heart_bar(pct: float, length: int = 14) -> str:
    filled = round((pct / 100) * length)
    return "❤️" * filled + "🤍" * (length - filled)


def verdict_for(pct: float) -> str:
    if pct >= 90:
        return "Soulmates 💍"
    if pct >= 70:
        return "Strong match 💕"
    if pct >= 40:
        return "Could go either way 🤷"
    if pct >= 15:
        return "...rough 💔"
    return "Please don't 🚫"


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _ship_embed(self, user_a: discord.Member, user_b: discord.Member) -> discord.Embed:
        if user_a.id == user_b.id:
            pct, bar, verdict = 100.0, heart_bar(100), "Self love 🪞✨"
            name = user_a.display_name
        else:
            pct = ship_percent()
            bar = heart_bar(pct)
            verdict = verdict_for(pct)
            name = ship_name(user_a.display_name, user_b.display_name)

        embed = discord.Embed(
            title=f"💘 {name}",
            description=f"{user_a.mention} × {user_b.mention}\n\n{bar}\n**{pct}%** · {verdict}",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        embed.set_footer(text="Run it again for a new roll 🎲")
        return embed

    @app_commands.command(name="ship", description="Ship two members and get a compatibility score")
    @app_commands.describe(user1="First person", user2="Second person (defaults to you)")
    async def ship(self, interaction: discord.Interaction, user1: discord.Member, user2: discord.Member = None):
        target_b = user2 or interaction.user
        await interaction.response.send_message(embed=self._ship_embed(user1, target_b))

    @commands.command(name="ship")
    async def ship_text(self, ctx: commands.Context, user1: discord.Member, user2: discord.Member = None):
        target_b = user2 or ctx.author
        await ctx.reply(embed=self._ship_embed(user1, target_b), mention_author=False)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply("Couldn't find that member.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Usage: `{ctx.prefix}ship <member> [member2]`", mention_author=False)
        else:
            print(f"Fun prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))

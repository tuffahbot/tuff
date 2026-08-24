import random

import discord
from discord import app_commands
from discord.ext import commands

import database as db


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

    def _ship_embed(self, guild: discord.Guild, user_a: discord.Member, user_b: discord.Member) -> discord.Embed:
        override = db.get_ship_override(guild.id, user_a.id, user_b.id)
        pinned = override is not None

        if user_a.id == user_b.id and not pinned:
            pct, verdict = 100.0, "Self love 🪞✨"
            name = user_a.display_name
        else:
            pct = override if pinned else ship_percent()
            verdict = verdict_for(pct)
            name = user_a.display_name if user_a.id == user_b.id else ship_name(user_a.display_name, user_b.display_name)
        bar = heart_bar(pct)

        embed = discord.Embed(
            title=f"💘 {name}",
            description=f"{user_a.mention} × {user_b.mention}\n\n{bar}\n**{pct}%** · {verdict}",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        embed.set_footer(text="📌 Pinned by an admin -- always the same result" if pinned else "Run it again for a new roll 🎲")
        return embed

    @app_commands.command(name="ship", description="Ship two members and get a compatibility score")
    @app_commands.describe(user1="First person", user2="Second person (defaults to you)")
    async def ship(self, interaction: discord.Interaction, user1: discord.Member, user2: discord.Member = None):
        target_b = user2 or interaction.user
        await interaction.response.send_message(embed=self._ship_embed(interaction.guild, user1, target_b))

    @commands.command(name="ship")
    async def ship_text(self, ctx: commands.Context, user1: discord.Member, user2: discord.Member = None):
        target_b = user2 or ctx.author
        await ctx.reply(embed=self._ship_embed(ctx.guild, user1, target_b), mention_author=False)

    # ---------------- Admin: pin a fixed ship percent for a pair ----------------

    @app_commands.command(name="shipset", description="[Admin] Pin a fixed compatibility % for two members' ship result")
    @app_commands.describe(user1="First person", user2="Second person", percent="The percent to always show for this pair (0-100)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shipset(self, interaction: discord.Interaction, user1: discord.Member, user2: discord.Member, percent: app_commands.Range[float, 0, 100]):
        db.set_ship_override(interaction.guild_id, user1.id, user2.id, percent, interaction.user.id)
        await interaction.response.send_message(
            f"📌 {user1.mention} × {user2.mention} will now always ship at **{percent}%**, until cleared with `/shipclear`.",
            ephemeral=True,
        )

    @app_commands.command(name="shipclear", description="[Admin] Remove a pinned ship percent, back to random")
    @app_commands.describe(user1="First person", user2="Second person")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shipclear(self, interaction: discord.Interaction, user1: discord.Member, user2: discord.Member):
        removed = db.remove_ship_override(interaction.guild_id, user1.id, user2.id)
        if removed:
            await interaction.response.send_message(f"🧹 Cleared the pinned percent for {user1.mention} × {user2.mention} -- back to random rolls.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{user1.mention} × {user2.mention} didn't have a pinned percent.", ephemeral=True)

    @commands.command(name="shipset")
    @commands.has_permissions(manage_guild=True)
    async def shipset_text(self, ctx: commands.Context, user1: discord.Member, user2: discord.Member, percent: float):
        percent = max(0.0, min(100.0, percent))
        db.set_ship_override(ctx.guild.id, user1.id, user2.id, percent, ctx.author.id)
        await ctx.reply(f"📌 {user1.mention} × {user2.mention} will now always ship at **{percent}%**, until cleared with `{ctx.prefix}shipclear`.", mention_author=False)

    @commands.command(name="shipclear")
    @commands.has_permissions(manage_guild=True)
    async def shipclear_text(self, ctx: commands.Context, user1: discord.Member, user2: discord.Member):
        removed = db.remove_ship_override(ctx.guild.id, user1.id, user2.id)
        if removed:
            await ctx.reply(f"🧹 Cleared the pinned percent for {user1.mention} × {user2.mention} -- back to random rolls.", mention_author=False)
        else:
            await ctx.reply(f"{user1.mention} × {user2.mention} didn't have a pinned percent.", mention_author=False)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Server permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Server permission to do that.", mention_author=False)
        elif isinstance(error, commands.MemberNotFound):
            await ctx.reply("Couldn't find that member.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Usage: `{ctx.prefix}ship <member> [member2]`, `{ctx.prefix}shipset <member1> <member2> <percent>`, `{ctx.prefix}shipclear <member1> <member2>`", mention_author=False)
        else:
            print(f"Fun prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))

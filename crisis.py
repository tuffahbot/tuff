import discord
from discord import app_commands
from discord.ext import commands


def build_resources_embed() -> discord.Embed:
    embed = discord.Embed(
        title="💛 You don't have to go through this alone",
        description=(
            "If you're struggling, or having thoughts of suicide, please reach out to one of these. "
            "They're free, confidential, and there right now -- there's no reason too big or too small to use them."
        ),
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="🇺🇸 United States",
        value=(
            "**988 Suicide & Crisis Lifeline** — call or text **988**, or chat at [988lifeline.org](https://988lifeline.org)\n"
            "**Crisis Text Line** — text **HOME** to **741741**\n"
            "**The Trevor Project** (LGBTQ+, ages 13-24) — call **1-866-488-7386**"
        ),
        inline=False,
    )
    embed.add_field(
        name="🌎 Outside the US",
        value="[Find a crisis line in your country](https://www.iasp.info/resources/Crisis_Centres/) — International Association for Suicide Prevention",
        inline=False,
    )
    embed.set_footer(text="If you're in immediate danger, please contact local emergency services.")
    return embed


class Crisis(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="kms", description="Get free, confidential crisis support resources")
    async def kms(self, interaction: discord.Interaction):
        embed = build_resources_embed()
        await interaction.response.send_message(embed=embed)
        try:
            await interaction.user.send(embed=embed)
        except discord.Forbidden:
            pass  # not fatal -- they already have it in the channel

    @commands.command(name="kms")
    async def kms_text(self, ctx: commands.Context):
        embed = build_resources_embed()
        await ctx.reply(embed=embed, mention_author=False)
        try:
            await ctx.author.send(embed=embed)
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Crisis(bot))

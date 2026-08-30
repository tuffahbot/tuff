from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

import database as db

BOOST_MULTIPLIER = 5
BOOST_DURATION = timedelta(hours=1)
BOOST_COOLDOWN = timedelta(hours=24)
PANEL_BUTTON_CUSTOM_ID = "xpboost_claim"  # static -- same on every panel, survives restarts with no per-message tracking needed


def build_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="⚡ Daily XP Boost",
        description=(
            f"Hit the button below for **{BOOST_MULTIPLIER}x XP** on your messages for the next hour.\n\n"
            "One claim per person, every 24 hours -- everyone's own claim and cooldown is tracked separately."
        ),
        color=discord.Color.yellow(),
    )
    return embed


def format_remaining(delta: timedelta) -> str:
    total_minutes = max(1, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class XPBoostView(discord.ui.View):
    """The 'Claim' button. custom_id is static (not per-message), so one
    bot.add_view(XPBoostView()) at cog_load re-attaches it to every panel
    message at once, including ones posted before a restart -- same trick
    as the confessions/suggestions panel buttons."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim 5x XP Boost", emoji="⚡", style=discord.ButtonStyle.success, custom_id=PANEL_BUTTON_CUSTOM_ID)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        now = datetime.now(timezone.utc)
        row = db.get_xp_boost(interaction.guild_id, interaction.user.id)

        if row and row["last_claimed_at"]:
            since_last = now - datetime.fromisoformat(row["last_claimed_at"])
            if since_last < BOOST_COOLDOWN:
                remaining = format_remaining(BOOST_COOLDOWN - since_last)
                await interaction.response.send_message(f"⏳ Already claimed today -- try again in {remaining}.", ephemeral=True)
                return

        expires_at = now + BOOST_DURATION
        db.claim_xp_boost(interaction.guild_id, interaction.user.id, expires_at.isoformat(), now.isoformat())
        await interaction.response.send_message(
            f"⚡ Boost activated! You're earning **{BOOST_MULTIPLIER}x XP** for the next hour. Come back tomorrow for another.",
            ephemeral=True,
        )


class XPBoost(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(XPBoostView())  # global button, works on every panel message at once

    @app_commands.command(name="xpboostpanel", description="[Admin] Post the daily XP boost panel in this channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def xpboostpanel(self, interaction: discord.Interaction):
        try:
            await interaction.channel.send(embed=build_panel_embed(), view=XPBoostView())
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to post in this channel.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Panel posted.", ephemeral=True)

    @commands.command(name="xpboostpanel")
    @commands.has_permissions(manage_guild=True)
    async def xpboostpanel_text(self, ctx: commands.Context):
        try:
            await ctx.send(embed=build_panel_embed(), view=XPBoostView())
        except discord.Forbidden:
            await ctx.reply("I don't have permission to post in this channel.", mention_author=False)
            return
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Server permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Server permission to do that.", mention_author=False)
        else:
            print(f"XPBoost prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(XPBoost(bot))

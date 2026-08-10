import datetime
import random
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database as db

DURATION_RE = re.compile(r"^(\d+)([smhd])$")
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> int | None:
    """Parses strings like '30s', '10m', '2h', '1d' into a number of seconds."""
    match = DURATION_RE.match(text.strip().lower())
    if not match:
        return None
    amount, unit = match.groups()
    return int(amount) * UNIT_SECONDS[unit]


class GiveawayView(discord.ui.View):
    """Persistent view (timeout=None + static custom_id) so the Enter button
    keeps working across bot restarts."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.green, custom_id="giveaway_enter")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        giveaway = db.get_giveaway(interaction.message.id)
        if not giveaway or giveaway["ended"]:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        added = db.add_giveaway_entry(interaction.message.id, interaction.user.id)
        if added:
            count = len(db.get_giveaway_entries(interaction.message.id))
            await interaction.response.send_message(f"🎉 You're entered! ({count} entries so far)", ephemeral=True)
        else:
            await interaction.response.send_message("You're already entered in this giveaway.", ephemeral=True)


class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(GiveawayView())
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    giveaway_group = app_commands.Group(name="giveaway", description="Run server giveaways")

    @giveaway_group.command(name="start", description="Start a giveaway")
    @app_commands.describe(prize="What's being given away", duration="e.g. 30s, 10m, 2h, 1d", winners="Number of winners")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def start(self, interaction: discord.Interaction, prize: str, duration: str, winners: app_commands.Range[int, 1, 20] = 1):
        seconds = parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message("Invalid duration. Use e.g. `30s`, `10m`, `2h`, `1d`.", ephemeral=True)
            return

        ends_at = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
        embed = discord.Embed(
            title="🎉 Giveaway!",
            description=f"**{prize}**\n\nClick below to enter!",
            color=discord.Color.fuchsia(),
        )
        embed.add_field(name="Winners", value=str(winners))
        embed.add_field(name="Ends", value=discord.utils.format_dt(ends_at, style="R"))
        embed.set_footer(text=f"Hosted by {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed, view=GiveawayView())
        message = await interaction.original_response()
        db.create_giveaway(message.id, interaction.guild_id, interaction.channel_id, prize, winners, interaction.user.id, ends_at.isoformat())

    @giveaway_group.command(name="end", description="End a giveaway early and pick winners")
    @app_commands.describe(message_id="The giveaway message ID")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def end(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            ok = await self._finish_giveaway(int(message_id))
        except ValueError:
            ok = False
        await interaction.followup.send("Ended." if ok else "Couldn't find that giveaway (or it already ended).", ephemeral=True)

    @giveaway_group.command(name="reroll", description="Reroll winners for an ended giveaway")
    @app_commands.describe(message_id="The giveaway message ID")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reroll(self, interaction: discord.Interaction, message_id: str):
        try:
            giveaway = db.get_giveaway(int(message_id))
        except ValueError:
            giveaway = None
        if not giveaway:
            await interaction.response.send_message("Giveaway not found.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._announce_winners(giveaway)
        await interaction.followup.send("Rerolled.", ephemeral=True)

    @tasks.loop(seconds=30)
    async def check_giveaways(self):
        for giveaway in db.get_active_giveaways():
            ends_at = datetime.datetime.fromisoformat(giveaway["ends_at"])
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=datetime.timezone.utc)
            if discord.utils.utcnow() >= ends_at:
                await self._finish_giveaway(giveaway["message_id"])

    @check_giveaways.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def _finish_giveaway(self, message_id: int) -> bool:
        giveaway = db.get_giveaway(message_id)
        if not giveaway or giveaway["ended"]:
            return False
        db.mark_giveaway_ended(message_id)
        await self._announce_winners(giveaway)
        return True

    async def _announce_winners(self, giveaway):
        channel = self.bot.get_channel(giveaway["channel_id"])
        if channel is None:
            return
        entries = db.get_giveaway_entries(giveaway["message_id"])

        if not entries:
            await channel.send(f"🎉 The giveaway for **{giveaway['prize']}** ended, but nobody entered.")
            return

        picks = random.sample(entries, k=min(giveaway["winners"], len(entries)))
        mentions = ", ".join(f"<@{uid}>" for uid in picks)
        await channel.send(f"🎉 Congratulations {mentions}! You won **{giveaway['prize']}**!")

        try:
            message = await channel.fetch_message(giveaway["message_id"])
            if message.embeds:
                embed = message.embeds[0]
                embed.description = f"**{giveaway['prize']}**\n\n🎉 Ended — winner(s): {mentions}"
                embed.color = discord.Color.dark_grey()
                await message.edit(embed=embed, view=None)
        except (discord.NotFound, discord.Forbidden):
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))

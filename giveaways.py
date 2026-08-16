import random
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database as db
from polls import parse_duration  # same "30m/2h/1d" parser used by /poll


def build_giveaway_embed(prize: str, winner_count: int, host, ends_at, entry_count: int, *, ended: bool = False, winners: list = None) -> discord.Embed:
    if not ended:
        embed = discord.Embed(
            title=f"🎉 {prize}",
            description=(
                f"Click the button below to enter!\n\n"
                f"**Winners:** {winner_count}\n"
                f"**Ends:** {discord.utils.format_dt(ends_at, 'R')} ({discord.utils.format_dt(ends_at, 'f')})\n"
                f"**Hosted by:** {host.mention if hasattr(host, 'mention') else host}\n"
                f"**Entries:** {entry_count}"
            ),
            color=discord.Color.blurple(),
        )
    else:
        if winners:
            winner_text = ", ".join(w.mention for w in winners)
        else:
            winner_text = "No valid entries -- nobody won."
        embed = discord.Embed(
            title=f"🎉 {prize}",
            description=(
                f"**Winner{'s' if winner_count != 1 else ''}:** {winner_text}\n"
                f"**Hosted by:** {host.mention if hasattr(host, 'mention') else host}\n"
                f"**Entries:** {entry_count}"
            ),
            color=discord.Color.dark_grey(),
        )
        embed.set_footer(text="Giveaway ended")
    return embed


class GiveawayView(discord.ui.View):
    def __init__(self, cog: "Giveaways", message_id: int, entry_count: int = 0, disabled: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        self.message_id = message_id
        button = discord.ui.Button(
            label=f"Enter ({entry_count})",
            emoji="🎉",
            style=discord.ButtonStyle.blurple,
            custom_id=f"giveaway_enter:{message_id}",
            disabled=disabled,
        )
        button.callback = self._enter
        self.add_item(button)

    async def _enter(self, interaction: discord.Interaction):
        await self.cog.toggle_entry(interaction, self.message_id)


class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        for row in db.get_active_giveaways():
            count = len(db.get_giveaway_entries(row["message_id"]))
            self.bot.add_view(GiveawayView(self, row["message_id"], count), message_id=row["message_id"])
        self._check_due.start()

    def cog_unload(self):
        self._check_due.cancel()

    # ---------------- Core logic shared by slash + prefix ----------------

    async def toggle_entry(self, interaction: discord.Interaction, message_id: int):
        row = db.get_giveaway(message_id)
        if row is None or row["ended"]:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return

        if db.remove_giveaway_entry(message_id, interaction.user.id):
            joined = False
        else:
            db.add_giveaway_entry(message_id, interaction.user.id)
            joined = True

        count = len(db.get_giveaway_entries(message_id))
        ends_at = datetime.fromisoformat(row["ends_at"])
        host = self.bot.get_user(row["host_id"]) or f"<@{row['host_id']}>"

        embed = build_giveaway_embed(row["prize"], row["winners"], host, ends_at, count)
        await interaction.response.edit_message(embed=embed, view=GiveawayView(self, message_id, count))
        await interaction.followup.send("🎉 You're entered!" if joined else "Entry removed.", ephemeral=True)

    async def create_giveaway(self, channel: discord.abc.Messageable, guild_id: int, host, prize: str, winners: int, duration_str: str):
        delta = parse_duration(duration_str)
        if delta is None:
            return None, "Couldn't parse that duration -- try something like `10m`, `2h`, or `1d`."
        if winners < 1:
            return None, "Winners must be at least 1."

        ends_at = datetime.now(timezone.utc) + delta

        embed = build_giveaway_embed(prize, winners, host, ends_at, 0)
        message = await channel.send(embed=embed)
        view = GiveawayView(self, message.id, 0)
        await message.edit(view=view)
        db.create_giveaway(message.id, guild_id, channel.id, prize, winners, host.id, ends_at.isoformat())
        self.bot.add_view(view, message_id=message.id)
        return message, None

    def _pick_winners(self, message_id: int, winner_count: int) -> list[int]:
        entries = db.get_giveaway_entries(message_id)
        if not entries:
            return []
        return random.sample(entries, k=min(winner_count, len(entries)))

    async def finish_giveaway(self, row, *, reroll: bool = False):
        """Ends (or rerolls) a giveaway. Returns (embed, winner_users)."""
        message_id = row["message_id"]
        winner_ids = self._pick_winners(message_id, row["winners"])

        winners = []
        for uid in winner_ids:
            user = self.bot.get_user(uid)
            if user is None:
                try:
                    user = await self.bot.fetch_user(uid)
                except discord.NotFound:
                    continue
            winners.append(user)

        if not reroll:
            db.mark_giveaway_ended(message_id)

        host = self.bot.get_user(row["host_id"]) or f"<@{row['host_id']}>"
        count = len(db.get_giveaway_entries(message_id))
        embed = build_giveaway_embed(row["prize"], row["winners"], host, None, count, ended=True, winners=winners)

        try:
            channel = self.bot.get_channel(row["channel_id"]) or await self.bot.fetch_channel(row["channel_id"])
            message = await channel.fetch_message(message_id)
            await message.edit(embed=embed, view=GiveawayView(self, message_id, count, disabled=True))
            if winners:
                mentions = ", ".join(w.mention for w in winners)
                verb = "New winner(s)" if reroll else "Congrats"
                await channel.send(f"🎉 {verb}: {mentions} -- you won **{row['prize']}**!")
            elif not reroll:
                await channel.send(f"🎉 The giveaway for **{row['prize']}** ended with no entries.")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        for winner in winners:
            try:
                await winner.send(f"🎉 You won **{row['prize']}**!")
            except discord.Forbidden:
                pass

        return embed, winners

    @tasks.loop(seconds=30)
    async def _check_due(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in db.get_active_giveaways():
            if row["ends_at"] <= now_iso:
                await self.finish_giveaway(row)

    @_check_due.before_loop
    async def _before_check_due(self):
        await self.bot.wait_until_ready()

    # ---------------- Slash commands ----------------

    giveaway = app_commands.Group(name="giveaway", description="Run giveaways", default_permissions=discord.Permissions(manage_guild=True))

    @giveaway.command(name="start", description="[Admin] Start a giveaway")
    @app_commands.describe(prize="What's being given away", duration="e.g. 10m, 2h, 1d", winners="How many winners")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def giveaway_start(self, interaction: discord.Interaction, prize: str, duration: str, winners: app_commands.Range[int, 1, 50] = 1):
        await interaction.response.defer(ephemeral=True)
        message, error = await self.create_giveaway(interaction.channel, interaction.guild_id, interaction.user, prize, winners, duration)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(f"🎉 Giveaway started: {message.jump_url}", ephemeral=True)

    @giveaway.command(name="end", description="[Admin] End a giveaway early and pick winners")
    @app_commands.describe(message_id="The giveaway message's ID")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def giveaway_end(self, interaction: discord.Interaction, message_id: str):
        row = self._get_row_or_none(message_id)
        if row is None or row["ended"]:
            await interaction.response.send_message("No active giveaway with that message ID.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.finish_giveaway(row)
        await interaction.followup.send("✅ Giveaway ended.", ephemeral=True)

    @giveaway.command(name="reroll", description="[Admin] Pick new winner(s) for an ended giveaway")
    @app_commands.describe(message_id="The giveaway message's ID")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def giveaway_reroll(self, interaction: discord.Interaction, message_id: str):
        row = self._get_row_or_none(message_id)
        if row is None or not row["ended"]:
            await interaction.response.send_message("That's not an ended giveaway I can reroll.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.finish_giveaway(row, reroll=True)
        await interaction.followup.send("✅ Rerolled.", ephemeral=True)

    def _get_row_or_none(self, message_id_str: str):
        try:
            return db.get_giveaway(int(message_id_str))
        except ValueError:
            return None

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Server permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    # ---------------- Prefix commands ----------------

    @commands.group(name="giveaway", invoke_without_command=True)
    async def giveaway_text(self, ctx: commands.Context):
        await ctx.reply(
            f"Usage: `{ctx.prefix}giveaway start <winners> <duration> <prize>`, "
            f"`{ctx.prefix}giveaway end <message_id>`, `{ctx.prefix}giveaway reroll <message_id>`",
            mention_author=False,
        )

    @giveaway_text.command(name="start")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_text_start(self, ctx: commands.Context, winners: int, duration: str, *, prize: str):
        message, error = await self.create_giveaway(ctx.channel, ctx.guild.id, ctx.author, prize, winners, duration)
        if error:
            await ctx.reply(error, mention_author=False)
            return
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @giveaway_text.command(name="end")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_text_end(self, ctx: commands.Context, message_id: int):
        row = db.get_giveaway(message_id)
        if row is None or row["ended"]:
            await ctx.reply("No active giveaway with that message ID.", mention_author=False)
            return
        await self.finish_giveaway(row)
        await ctx.reply("✅ Giveaway ended.", mention_author=False)

    @giveaway_text.command(name="reroll")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_text_reroll(self, ctx: commands.Context, message_id: int):
        row = db.get_giveaway(message_id)
        if row is None or not row["ended"]:
            await ctx.reply("That's not an ended giveaway I can reroll.", mention_author=False)
            return
        await self.finish_giveaway(row, reroll=True)
        await ctx.reply("✅ Rerolled.", mention_author=False)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Server permission to do that.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Usage: `{ctx.prefix}giveaway start <winners> <duration> <prize>`", mention_author=False)
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("Check your arguments -- winners/message ID need to be plain numbers.", mention_author=False)
        else:
            print(f"Giveaway prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))

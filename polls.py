import json
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database as db

MAX_OPTIONS = 10
OPTION_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

DURATION_RE = re.compile(r"^(\d+)([smhd])$")
DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> timedelta | None:
    match = DURATION_RE.match(text.strip().lower())
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    return timedelta(seconds=amount * DURATION_UNITS[unit])


def build_poll_embed(question: str, options: list[str], counts: dict, total: int, *, ended: bool = False, ends_at: datetime = None, creator=None) -> discord.Embed:
    lines = []
    for i, opt in enumerate(options):
        count = counts.get(i, 0)
        pct = int((count / total) * 100) if total else 0
        bar_len = int((count / total) * 14) if total else 0
        bar = "🟩" * bar_len + "⬜" * (14 - bar_len)
        lines.append(f"{OPTION_EMOJIS[i]} **{opt}**\n{bar} `{count} vote(s) · {pct}%`")

    embed = discord.Embed(
        title=("🔒 Poll Ended: " if ended else "📊 ") + question,
        description="\n\n".join(lines),
        color=discord.Color.dark_gray() if ended else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    if not ended and ends_at:
        embed.add_field(name="Closes", value=discord.utils.format_dt(ends_at, "R"), inline=False)
    footer = f"{total} total vote(s)"
    if creator:
        footer += f" · Poll by {creator}"
    embed.set_footer(text=footer)
    return embed


class PollView(discord.ui.View):
    """Buttons for casting a vote. Rebuilt (with disabled=True) when a poll ends."""

    def __init__(self, cog: "Polls", message_id: int, options: list[str], disabled: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        self.message_id = message_id
        self.options = options
        for i, opt in enumerate(options):
            label = opt if len(opt) <= 80 else opt[:77] + "..."
            button = discord.ui.Button(
                label=label,
                emoji=OPTION_EMOJIS[i],
                style=discord.ButtonStyle.secondary,
                custom_id=f"poll_vote:{message_id}:{i}",
                row=i // 5,
                disabled=disabled,
            )
            button.callback = self._make_callback(i)
            self.add_item(button)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            await self.cog.handle_vote(interaction, self.message_id, self.options, index)
        return callback


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Re-attach live views for any poll that was still open when the bot last stopped.
        for poll_row in db.get_active_polls():
            options = json.loads(poll_row["options"])
            view = PollView(self, poll_row["message_id"], options)
            self.bot.add_view(view, message_id=poll_row["message_id"])
        self._check_due_polls.start()

    def cog_unload(self):
        self._check_due_polls.cancel()

    # ---------------- Shared logic ----------------

    async def handle_vote(self, interaction: discord.Interaction, message_id: int, options: list[str], index: int):
        poll_row = db.get_poll(message_id)
        if poll_row is None or poll_row["ended"]:
            await interaction.response.send_message("This poll has ended.", ephemeral=True)
            return
        db.set_poll_vote(message_id, interaction.user.id, index)
        counts = db.get_poll_vote_counts(message_id)
        total = sum(counts.values())
        ends_at = datetime.fromisoformat(poll_row["ends_at"]) if poll_row["ends_at"] else None
        embed = build_poll_embed(poll_row["question"], options, counts, total, ends_at=ends_at, creator=f"<@{poll_row['creator_id']}>")
        await interaction.response.edit_message(embed=embed, view=PollView(self, message_id, options))

    async def create_poll(self, channel: discord.abc.Messageable, guild_id: int, creator, question: str, options: list[str], duration_str: str | None):
        """Returns (message, error_message)."""
        ends_at = None
        if duration_str:
            delta = parse_duration(duration_str)
            if delta is None:
                return None, "Couldn't parse that duration -- try something like `30m`, `2h`, or `1d`."
            ends_at = datetime.now(timezone.utc) + delta

        embed = build_poll_embed(question, options, {}, 0, ends_at=ends_at, creator=str(creator))
        message = await channel.send(embed=embed)
        view = PollView(self, message.id, options)
        await message.edit(view=view)
        db.create_poll(message.id, guild_id, channel.id, question, options, creator.id, ends_at.isoformat() if ends_at else None)
        self.bot.add_view(view, message_id=message.id)
        return message, None

    async def end_poll(self, poll_row) -> discord.Embed:
        """Marks a poll ended in the DB and updates the live message. Returns the final embed."""
        message_id = poll_row["message_id"]
        options = json.loads(poll_row["options"])
        counts = db.get_poll_vote_counts(message_id)
        total = sum(counts.values())
        db.mark_poll_ended(message_id)
        embed = build_poll_embed(poll_row["question"], options, counts, total, ended=True, creator=f"<@{poll_row['creator_id']}>")

        try:
            channel = self.bot.get_channel(poll_row["channel_id"]) or await self.bot.fetch_channel(poll_row["channel_id"])
            message = await channel.fetch_message(message_id)
            await message.edit(embed=embed, view=PollView(self, message_id, options, disabled=True))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        return embed

    def _can_end(self, user: discord.Member, poll_row) -> bool:
        return user.id == poll_row["creator_id"] or user.guild_permissions.manage_messages

    @tasks.loop(seconds=30)
    async def _check_due_polls(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        for poll_row in db.get_due_polls(now_iso):
            await self.end_poll(poll_row)

    @_check_due_polls.before_loop
    async def _before_check_due_polls(self):
        await self.bot.wait_until_ready()

    # ---------------- Slash commands (/poll ...) ----------------

    poll = app_commands.Group(name="poll", description="Create and manage polls")

    @poll.command(name="create", description="Start a poll")
    @app_commands.describe(
        question="The poll question",
        options="Answer choices separated by commas (2-10)",
        duration="Optional auto-close time, e.g. 30m, 2h, 1d",
    )
    async def poll_create(self, interaction: discord.Interaction, question: str, options: str, duration: str = None):
        opts = [o.strip() for o in options.split(",") if o.strip()]
        if len(opts) < 2:
            await interaction.response.send_message("Give at least 2 options, separated by commas.", ephemeral=True)
            return
        if len(opts) > MAX_OPTIONS:
            await interaction.response.send_message(f"Max {MAX_OPTIONS} options.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        message, error = await self.create_poll(interaction.channel, interaction.guild_id, interaction.user, question, opts, duration)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(f"Poll started: {message.jump_url}", ephemeral=True)

    @poll.command(name="end", description="End a poll early and lock in the results")
    @app_commands.describe(message_id="The poll message's ID (right-click the poll -> Copy Message ID)")
    async def poll_end(self, interaction: discord.Interaction, message_id: str):
        try:
            mid = int(message_id)
        except ValueError:
            await interaction.response.send_message("That doesn't look like a valid message ID.", ephemeral=True)
            return
        poll_row = db.get_poll(mid)
        if poll_row is None or poll_row["ended"]:
            await interaction.response.send_message("No active poll with that message ID.", ephemeral=True)
            return
        if not self._can_end(interaction.user, poll_row):
            await interaction.response.send_message("Only the poll's creator or a moderator can end it early.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.end_poll(poll_row)
        await interaction.followup.send("✅ Poll ended.", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    # ---------------- Prefix commands (?poll ...) ----------------

    @commands.group(name="poll", invoke_without_command=True)
    async def poll_text(self, ctx: commands.Context):
        await ctx.reply(
            f"Usage: `{ctx.prefix}poll create <question> | <option1> | <option2> [| more options] [| duration]`\n"
            f"Example: `{ctx.prefix}poll create Best pizza topping? | Pepperoni | Mushroom | Pineapple | 1h`\n"
            f"`{ctx.prefix}poll end <message_id>`",
            mention_author=False,
        )

    @poll_text.command(name="create")
    async def poll_text_create(self, ctx: commands.Context, *, body: str):
        parts = [p.strip() for p in body.split("|") if p.strip()]
        if len(parts) < 3:
            await ctx.reply(
                f"Need a question and at least 2 options, separated by `|`. "
                f"Example: `{ctx.prefix}poll create Best pizza topping? | Pepperoni | Mushroom | Pineapple`",
                mention_author=False,
            )
            return

        question = parts[0]
        opts = parts[1:]
        duration = None
        # If there are more than 2 options left and the last segment looks like a
        # duration (e.g. "1h"), treat it as the auto-close time instead of an option.
        if len(opts) > 2 and parse_duration(opts[-1]) is not None:
            duration = opts[-1]
            opts = opts[:-1]

        if len(opts) > MAX_OPTIONS:
            await ctx.reply(f"Max {MAX_OPTIONS} options.", mention_author=False)
            return

        message, error = await self.create_poll(ctx.channel, ctx.guild.id, ctx.author, question, opts, duration)
        if error:
            await ctx.reply(error, mention_author=False)
            return
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @poll_text.command(name="end")
    async def poll_text_end(self, ctx: commands.Context, message_id: int):
        poll_row = db.get_poll(message_id)
        if poll_row is None or poll_row["ended"]:
            await ctx.reply("No active poll with that message ID.", mention_author=False)
            return
        if not self._can_end(ctx.author, poll_row):
            await ctx.reply("Only the poll's creator or a moderator can end it early.", mention_author=False)
            return
        await self.end_poll(poll_row)
        await ctx.reply("✅ Poll ended.", mention_author=False)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Missing argument: `{error.param.name}`. Check `{ctx.prefix}poll` for usage.", mention_author=False)
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("Check your arguments -- the message ID needs to be a plain number.", mention_author=False)
        else:
            print(f"Poll prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Polls(bot))

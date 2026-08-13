import asyncio
import json

import discord
from discord import app_commands
from discord.ext import commands

import database as db

# ---------------------------------------------------------------------------
# The moderator application questions, in order. Each is (label, question) --
# label is used as the embed field title / short name, question is the full
# text sent in the DM.
# ---------------------------------------------------------------------------
QUESTIONS = [
    ("Discord Username", "What is your full Discord username?"),
    ("Discord User ID", "Please provide your unique 18-digit Discord User ID."),
    ("Age", "How old are you?"),
    ("Timezone", "What is your current timezone?"),
    ("Daily Hours", "How many hours per week can you realistically dedicate to moderating this server?"),
    ("Active Hours", "During what times of the day are you most active on Discord?"),
    ("Outside Commitments", "Do you have any outside commitments (school, work, other servers) that might impact your availability?"),
    ("Motivation", "Why do you want to join our specific moderation team?"),
    ("Value Add", "What unique strengths or perspectives will you bring to the staff team?"),
    ("Definition of a Mod", "In your own words, what does it mean to be a good moderator?"),
    ("Mass Spam", "A group of raid accounts suddenly joins the general chat and starts spamming dangerous links or NSFW images. What are your immediate actions?"),
    ("Rule-Breaking Friend", "You notice a close friend or a highly active, respected member of the server violating a major rule. How do you handle the situation?"),
    ("Public Argument", "Two members are having an aggressive, heated political argument in a public text channel. Walk us through how you de-escalate it."),
    ("Handling Toxicity", "A member is targeted with severe harassment or racial slurs. What steps do you take, and what is your immediate punishment choice?"),
    ("Dealing with Backlash", "A member starts publicly insulting and trolling you directly because you deleted their message. How do you respond?"),
]

APPLY_CUSTOM_ID = "1536596759768342589"
QUESTION_TIMEOUT = 600  # seconds per question before the application auto-cancels


class ApplyView(discord.ui.View):
    """The button on the panel message. Static custom_id, one view works for
    every panel post -- registered once in cog_load for persistence."""

    def __init__(self, cog: "ModApps"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Apply Now", emoji="📝", style=discord.ButtonStyle.success, custom_id=APPLY_CUSTOM_ID)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.start_application(interaction)


class ReviewView(discord.ui.View):
    """Accept/Deny buttons on a submitted application. custom_id encodes the
    application's DB id so it can be rebuilt and re-registered on restart."""

    def __init__(self, cog: "ModApps", app_id: int, disabled: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        self.app_id = app_id

        accept = discord.ui.Button(label="Accept", emoji="✅", style=discord.ButtonStyle.success,
                                    custom_id=f"modapp_accept:{app_id}", disabled=disabled)
        deny = discord.ui.Button(label="Deny", emoji="❌", style=discord.ButtonStyle.danger,
                                  custom_id=f"modapp_deny:{app_id}", disabled=disabled)
        accept.callback = self._make_callback("accepted")
        deny.callback = self._make_callback("denied")
        self.add_item(accept)
        self.add_item(deny)

    def _make_callback(self, decision: str):
        async def callback(interaction: discord.Interaction):
            await self.cog.review_application(interaction, self.app_id, decision)
        return callback


class ModApps(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active: set[tuple[int, int]] = set()  # (guild_id, user_id) mid-application right now

    async def cog_load(self):
        self.bot.add_view(ApplyView(self))
        for app_row in db.get_pending_applications():
            self.bot.add_view(ReviewView(self, app_row["id"]))

    # ---------------- Panel embed / setup ----------------

    def _panel_embed(self) -> discord.Embed:
        return discord.Embed(
            title="🛡️ Moderator Applications",
            description=(
                "Interested in joining the mod team? Click **Apply Now** below and I'll DM you "
                f"the application -- it's {len(QUESTIONS)} questions, so set aside a few minutes.\n\n"
                "Make sure your DMs are open to members of this server before applying."
            ),
            color=discord.Color.blurple(),
        )

    async def _do_panel(self, channel: discord.abc.Messageable):
        await channel.send(embed=self._panel_embed(), view=ApplyView(self))

    modapp = app_commands.Group(
        name="modapp",
        description="Set up and manage moderator applications",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @modapp.command(name="panel", description="[Admin] Post the Apply Now panel in this channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def modapp_panel(self, interaction: discord.Interaction):
        if db.get_mod_app_channel(interaction.guild_id) is None:
            await interaction.response.send_message(
                "Set a review channel first with `/modapp channel` -- that's where finished applications get posted.",
                ephemeral=True,
            )
            return
        await self._do_panel(interaction.channel)
        await interaction.response.send_message("✅ Panel posted.", ephemeral=True)

    @modapp.command(name="channel", description="[Admin] Set where finished applications get posted for review")
    @app_commands.describe(channel="Where completed applications should show up")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def modapp_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db.set_mod_app_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(f"✅ Applications will now be posted in {channel.mention}.", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Server permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    @commands.group(name="modapp", invoke_without_command=True)
    async def modapp_text(self, ctx: commands.Context):
        await ctx.reply(
            f"Usage: `{ctx.prefix}modapp channel <#channel>` then `{ctx.prefix}modapp panel`",
            mention_author=False,
        )

    @modapp_text.command(name="panel")
    @commands.has_permissions(manage_guild=True)
    async def modapp_text_panel(self, ctx: commands.Context):
        if db.get_mod_app_channel(ctx.guild.id) is None:
            await ctx.reply(f"Set a review channel first with `{ctx.prefix}modapp channel <#channel>`.", mention_author=False)
            return
        await self._do_panel(ctx.channel)

    @modapp_text.command(name="channel")
    @commands.has_permissions(manage_guild=True)
    async def modapp_text_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        db.set_mod_app_channel(ctx.guild.id, channel.id)
        await ctx.reply(f"✅ Applications will now be posted in {channel.mention}.", mention_author=False)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Server permission to do that.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Usage: `{ctx.prefix}modapp channel <#channel>`", mention_author=False)
        else:
            print(f"ModApps prefix command error: {error}")

    # ---------------- The DM application flow ----------------

    async def start_application(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        key = (guild.id, user.id)

        review_channel_id = db.get_mod_app_channel(guild.id)
        if review_channel_id is None:
            await interaction.response.send_message(
                "This server hasn't set up applications yet -- ask an admin to run `/modapp channel` first.",
                ephemeral=True,
            )
            return

        if db.get_pending_application(guild.id, user.id) is not None:
            await interaction.response.send_message("You already have an application pending review -- sit tight!", ephemeral=True)
            return

        if key in self._active:
            await interaction.response.send_message("You've already got an application in progress in your DMs.", ephemeral=True)
            return

        try:
            await user.send(
                f"📋 **Moderator Application -- {guild.name}**\n"
                f"I'll ask {len(QUESTIONS)} questions one at a time. Just reply here with your answer to each. "
                "Type `cancel` at any point to stop."
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I can't DM you -- please enable DMs from server members (Privacy Settings) and try again.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("📬 Check your DMs!", ephemeral=True)
        self._active.add(key)
        self.bot.loop.create_task(self._run_application(user, guild, review_channel_id))

    async def _run_application(self, user: discord.User, guild: discord.Guild, review_channel_id: int):
        key = (guild.id, user.id)

        def check(m: discord.Message) -> bool:
            return m.author.id == user.id and isinstance(m.channel, discord.DMChannel)

        answers = []
        try:
            for label, question in QUESTIONS:
                await user.send(f"**{label}**\n{question}")
                while True:
                    try:
                        msg = await self.bot.wait_for("message", check=check, timeout=QUESTION_TIMEOUT)
                    except asyncio.TimeoutError:
                        await user.send("⏱️ Application timed out from inactivity. Click **Apply Now** again if you'd still like to apply.")
                        return

                    text = msg.content.strip()
                    if text.lower() == "cancel":
                        await user.send("❌ Application cancelled.")
                        return
                    if not text:
                        await user.send("Please send a text answer (attachments/empty messages aren't captured) -- try again.")
                        continue
                    if label == "Discord User ID" and not text.isdigit():
                        await user.send("That doesn't look like a numeric Discord User ID -- try again (just the number, no `@` or mention).")
                        continue

                    answers.append({"label": label, "question": question, "answer": text[:1000]})
                    break

            app_id = db.create_application(guild.id, user.id, answers)
            await user.send("✅ Application submitted! The mod team will review it and get back to you.")

            channel = self.bot.get_channel(review_channel_id) or await self.bot.fetch_channel(review_channel_id)
            embed = self._application_embed(user, answers, status="pending")
            try:
                await channel.send(embed=embed, view=ReviewView(self, app_id))
            except discord.HTTPException:
                await user.send("⚠️ Your application was saved, but I couldn't post it in the review channel -- let a mod know.")
        finally:
            self._active.discard(key)

    def _application_embed(self, user, answers: list, *, status: str, reviewer=None) -> discord.Embed:
        color = {"pending": discord.Color.gold(), "accepted": discord.Color.green(), "denied": discord.Color.red()}[status]
        title = {
            "pending": "📋 New Moderator Application",
            "accepted": "✅ Application Accepted",
            "denied": "❌ Application Denied",
        }[status]
        embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
        embed.set_author(name=str(user), icon_url=getattr(user, "display_avatar", None) and user.display_avatar.url)
        for item in answers:
            embed.add_field(name=item["label"], value=(item["answer"] or "*(no answer)*")[:1024], inline=False)
        footer = f"Applicant ID: {user.id}"
        if reviewer:
            footer += f" · Reviewed by {reviewer}"
        embed.set_footer(text=footer)
        return embed

    async def review_application(self, interaction: discord.Interaction, app_id: int, decision: str):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need the Manage Server permission to review applications.", ephemeral=True)
            return

        app_row = db.get_application(app_id)
        if app_row is None:
            await interaction.response.send_message("Couldn't find that application anymore.", ephemeral=True)
            return
        if app_row["status"] != "pending":
            await interaction.response.send_message(f"This application was already marked **{app_row['status']}**.", ephemeral=True)
            return

        db.set_application_status(app_id, decision, interaction.user.id)

        answers = json.loads(app_row["answers"])
        applicant = self.bot.get_user(app_row["user_id"]) or await self.bot.fetch_user(app_row["user_id"])
        embed = self._application_embed(applicant, answers, status=decision, reviewer=interaction.user)
        await interaction.response.edit_message(embed=embed, view=ReviewView(self, app_id, disabled=True))

        try:
            if decision == "accepted":
                await applicant.send(
                    f"🎉 Congrats! Your moderator application for **{interaction.guild.name}** was **accepted**. "
                    "Someone from staff will reach out with next steps."
                )
            else:
                await applicant.send(
                    f"Thanks for applying to be a moderator for **{interaction.guild.name}**. "
                    "Unfortunately your application wasn't accepted this time around."
                )
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ModApps(bot))

import asyncio
import json
import re

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from permissions import SUPER_USER_ID

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

APPLY_CUSTOM_ID = "modapp_apply_button"
QUESTION_TIMEOUT = 600  # seconds per question before the application auto-cancels

TRIAL_MOD_ROLE_ID = 1538731669954109492  # given automatically when an application is accepted (trial period, not full mod)


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
    """Accept/Deny/Message buttons on a submitted application. custom_id
    encodes the application's DB id so it can be rebuilt and re-registered
    on restart. Accept/Deny disable once a decision is made; Message stays
    usable indefinitely so staff can follow up with an applicant later."""

    def __init__(self, cog: "ModApps", app_id: int, disabled: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        self.app_id = app_id

        accept = discord.ui.Button(label="Accept", emoji="✅", style=discord.ButtonStyle.success,
                                    custom_id=f"modapp_accept:{app_id}", disabled=disabled)
        deny = discord.ui.Button(label="Deny", emoji="❌", style=discord.ButtonStyle.danger,
                                  custom_id=f"modapp_deny:{app_id}", disabled=disabled)
        message_btn = discord.ui.Button(label="Message", emoji="💬", style=discord.ButtonStyle.secondary,
                                         custom_id=f"modapp_message:{app_id}")
        accept.callback = self._make_callback("accepted")
        deny.callback = self._make_callback("denied")
        message_btn.callback = self._message_callback
        self.add_item(accept)
        self.add_item(deny)
        self.add_item(message_btn)

    def _make_callback(self, decision: str):
        async def callback(interaction: discord.Interaction):
            await self.cog.review_application(interaction, self.app_id, decision)
        return callback

    async def _message_callback(self, interaction: discord.Interaction):
        await self.cog.start_relay(interaction, self.app_id)


class ModApps(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active: set[tuple[int, int]] = set()  # (guild_id, user_id) mid-application right now

    async def cog_load(self):
        self.bot.add_view(ApplyView(self))
        for app_row in db.get_all_applications():
            self.bot.add_view(ReviewView(self, app_row["id"], disabled=(app_row["status"] != "pending")))

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
                posted = await channel.send(embed=embed, view=ReviewView(self, app_id))
                db.set_application_message_id(app_id, posted.id)
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
        if interaction.user.id != SUPER_USER_ID and not interaction.user.guild_permissions.manage_guild:
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

        role_warning = None
        if decision == "accepted":
            role_warning = await self._assign_trial_mod_role(interaction.guild, app_row["user_id"], interaction.user)

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

        if role_warning:
            await interaction.followup.send(role_warning, ephemeral=True)

    async def _assign_trial_mod_role(self, guild: discord.Guild, user_id: int, granter) -> str | None:
        """Gives the accepted applicant the trial mod role. Returns a warning string if it couldn't."""
        role = guild.get_role(TRIAL_MOD_ROLE_ID)
        if role is None:
            return "⚠️ Accepted, but the configured trial mod role no longer exists -- check `TRIAL_MOD_ROLE_ID` in `modapps.py`."

        me = guild.me
        if not me.guild_permissions.manage_roles:
            return "⚠️ Accepted, but I don't have the Manage Roles permission -- couldn't assign the trial mod role."
        if role >= me.top_role:
            return f"⚠️ Accepted, but **{role.name}** is above my own top role -- move my role above it to auto-assign."

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                return "⚠️ Accepted, but that member isn't in the server anymore -- couldn't assign the trial mod role."

        try:
            await member.add_roles(role, reason=f"Mod application accepted by {granter} (trial)")
        except discord.Forbidden:
            return "⚠️ Accepted, but Discord refused the role assignment -- check permissions/hierarchy."
        return None

    @modapp.command(name="refreshbuttons", description="[Admin] Add the Message button to already-posted pending applications")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def modapp_refreshbuttons(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await self._refresh_buttons(interaction.guild)
        await interaction.followup.send(result, ephemeral=True)

    @modapp_text.command(name="refreshbuttons")
    @commands.has_permissions(manage_guild=True)
    async def modapp_text_refreshbuttons(self, ctx: commands.Context):
        result = await self._refresh_buttons(ctx.guild)
        await ctx.reply(result, mention_author=False)

    async def _refresh_buttons(self, guild: discord.Guild) -> str:
        """Backfill the Message button (and message_id) onto pending applications
        that were posted before that feature existed. One-time fix -- new
        applications already store their message_id when they're created."""
        review_channel_id = db.get_mod_app_channel(guild.id)
        if review_channel_id is None:
            return "No review channel set for this server."

        channel = self.bot.get_channel(review_channel_id) or await self.bot.fetch_channel(review_channel_id)
        pending_by_user = {row["user_id"]: row for row in db.get_pending_applications() if row["guild_id"] == guild.id}
        if not pending_by_user:
            return "No pending applications to refresh."

        footer_re = re.compile(r"Applicant ID: (\d+)")
        refreshed = 0
        try:
            async for message in channel.history(limit=200):
                if message.author.id != self.bot.user.id or not message.embeds:
                    continue
                footer_text = message.embeds[0].footer.text or ""
                match = footer_re.search(footer_text)
                if not match:
                    continue
                user_id = int(match.group(1))
                row = pending_by_user.get(user_id)
                if row is None:
                    continue
                if row["message_id"] == message.id:
                    continue  # already up to date
                try:
                    await message.edit(view=ReviewView(self, row["id"]))
                    db.set_application_message_id(row["id"], message.id)
                    refreshed += 1
                except discord.HTTPException:
                    continue
        except discord.Forbidden:
            return f"I can't read message history in {channel.mention}."

        if refreshed == 0:
            return "Nothing to refresh -- pending applications already have the Message button, or I couldn't find their posts in the last 200 messages."
        return f"✅ Refreshed {refreshed} application post(s) -- the Message button should show up now."

    # ---------------- DM relay ("type as bot" to an applicant) ----------------
    # Clicking "Message" on an application spins up a thread on that post.
    # Anything staff types in the thread gets sent to the applicant as the
    # bot itself (not under the staff member's name) -- and the applicant's
    # DM replies get relayed back into the thread automatically.

    async def start_relay(self, interaction: discord.Interaction, app_id: int):
        if interaction.user.id != SUPER_USER_ID and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need the Manage Server permission to do that.", ephemeral=True)
            return

        app_row = db.get_application(app_id)
        if app_row is None:
            await interaction.response.send_message("Couldn't find that application anymore.", ephemeral=True)
            return

        existing = db.get_relay_by_user(app_row["user_id"])
        if existing is not None:
            thread = interaction.guild.get_channel_or_thread(existing["thread_id"])
            if thread is not None:
                await interaction.response.send_message(f"Already relaying to this applicant in {thread.mention}.", ephemeral=True)
                return

        applicant = self.bot.get_user(app_row["user_id"]) or await self.bot.fetch_user(app_row["user_id"])

        await interaction.response.defer(ephemeral=True)
        try:
            thread = await interaction.channel.create_thread(
                name=f"app-{applicant.name}"[:100],
                message=interaction.message,
                auto_archive_duration=1440,
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"Couldn't create a thread: {e}", ephemeral=True)
            return

        db.create_relay(thread.id, interaction.guild_id, app_row["user_id"], app_id)
        await thread.send(
            f"💬 **DM relay started with {applicant}.**\n"
            "Anything sent in this thread gets DMed to them as the bot -- they won't see your name. "
            f"Their replies show up here too. Run `/modapp closerelay` (or `{self.bot.command_prefix}modapp closerelay`) here when you're done."
        )
        await interaction.followup.send(f"Started a relay: {thread.mention}", ephemeral=True)

    @modapp.command(name="closerelay", description="[Admin] Stop relaying messages in this thread")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def modapp_closerelay(self, interaction: discord.Interaction):
        result = await self._close_relay(interaction.channel)
        await interaction.response.send_message(result, ephemeral=True)

    @modapp_text.command(name="closerelay")
    @commands.has_permissions(manage_guild=True)
    async def modapp_text_closerelay(self, ctx: commands.Context):
        result = await self._close_relay(ctx.channel)
        await ctx.reply(result, mention_author=False)

    async def _close_relay(self, channel) -> str:
        if not isinstance(channel, discord.Thread):
            return "Run this inside the relay thread."
        relay = db.get_relay_by_thread(channel.id)
        if relay is None:
            return "No active relay in this thread."
        db.close_relay(channel.id)
        try:
            await channel.edit(archived=True, locked=True)
        except discord.HTTPException:
            pass
        return "🔒 Relay closed -- messages here will no longer reach the applicant."

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Staff typing in a relay thread -> DMed to the applicant as the bot.
        if isinstance(message.channel, discord.Thread):
            relay = db.get_relay_by_thread(message.channel.id)
            if relay is None:
                return
            if message.author.id != SUPER_USER_ID and not message.author.guild_permissions.manage_guild:
                return  # only staff messages get relayed out
            if not message.content:
                return  # skip attachment-only/empty messages, nothing to relay

            applicant = self.bot.get_user(relay["user_id"]) or await self.bot.fetch_user(relay["user_id"])
            try:
                await applicant.send(message.content)
                try:
                    await message.add_reaction("✅")
                except discord.HTTPException:
                    pass
            except discord.Forbidden:
                await message.channel.send("⚠️ Couldn't DM them -- they may have DMs closed or blocked the bot.")
            return

        # Applicant replying in DMs -> relayed back into the thread.
        if message.guild is None:
            relay = db.get_relay_by_user(message.author.id)
            if relay is None:
                return
            thread = self.bot.get_channel(relay["thread_id"])
            if thread is None:
                try:
                    thread = await self.bot.fetch_channel(relay["thread_id"])
                except (discord.NotFound, discord.Forbidden):
                    return
            embed = discord.Embed(description=message.content or "*(no text)*", color=discord.Color.blurple())
            embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
            try:
                await thread.send(embed=embed)
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ModApps(bot))

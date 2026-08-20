import time

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from logsutil import send_log

MAX_CONFESSION_LENGTH = 1500
CONFESSION_COOLDOWN_SECONDS = 60
PANEL_BUTTON_CUSTOM_ID = "confess_open_modal"  # static -- same on every panel, so this survives restarts with no per-message tracking needed

PANEL_EMBED_TITLE = "🤫 Anonymous Confessions"
PANEL_EMBED_DESCRIPTION = (
    "Got something to get off your chest? Hit the button below.\n\n"
    "Nobody -- not even staff -- sees who posted it. Your confession goes "
    "straight into this channel under a number, not your name."
)


def build_confession_embed(number: int, content: str) -> discord.Embed:
    return discord.Embed(
        title=f"Anonymous Confession (#{number})",
        description=f"\"{content}\"",
        color=discord.Color.dark_purple(),
        timestamp=discord.utils.utcnow(),
    )


def build_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title=PANEL_EMBED_TITLE,
        description=PANEL_EMBED_DESCRIPTION,
        color=discord.Color.dark_purple(),
    )
    embed.set_footer(text="Reports go to staff privately -- confessions themselves stay anonymous to everyone else.")
    return embed


class ConfessionModal(discord.ui.Modal, title="Anonymous Confession"):
    content = discord.ui.TextInput(
        label="Your confession",
        style=discord.TextStyle.paragraph,
        placeholder="Say whatever you want -- nobody will see your name.",
        max_length=MAX_CONFESSION_LENGTH,
        required=True,
    )

    def __init__(self, cog: "Confessions"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_confession(interaction, str(self.content))

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Something went wrong: {error}", ephemeral=True)


class ConfessionPanelView(discord.ui.View):
    """The persistent 'Submit a Confession' button posted in the confessions
    channel. custom_id is static (not per-message), so a single
    bot.add_view(ConfessionPanelView(cog)) at cog_load re-attaches it to
    EVERY panel message at once, including ones posted before a restart."""

    def __init__(self, cog: "Confessions"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Submit a Confession", emoji="📝", style=discord.ButtonStyle.blurple, custom_id=PANEL_BUTTON_CUSTOM_ID)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel_id = db.get_confessions_channel(interaction.guild_id)
        if channel_id is None or channel_id != interaction.channel_id:
            await interaction.response.send_message("This panel isn't hooked up to a confessions channel anymore -- ask an admin to run `/confessions setup`.", ephemeral=True)
            return
        await interaction.response.send_modal(ConfessionModal(self.cog))


class ConfessionReportView(discord.ui.View):
    """Attached to each individual confession message. custom_id is unique
    per confession, so unlike the panel button this one DOES need
    per-message re-registration -- see cog_load and
    db.get_recent_confessions_with_message()'s docstring for the bound on
    how far back that goes after a restart."""

    def __init__(self, cog: "Confessions", confession_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.confession_id = confession_id
        button = discord.ui.Button(
            label="Report",
            emoji="🚩",
            style=discord.ButtonStyle.secondary,
            custom_id=f"confess_report:{confession_id}",
        )
        button.callback = self._report
        self.add_item(button)

    async def _report(self, interaction: discord.Interaction):
        await self.cog.handle_report(interaction, self.confession_id)


class Confessions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}  # (guild_id, user_id) -> last submission time
        self.bot.add_view(ConfessionPanelView(self))  # global button, works on every panel message at once

    async def cog_load(self):
        for row in db.get_recent_confessions_with_message():
            self.bot.add_view(ConfessionReportView(self, row["id"]), message_id=row["message_id"])

    # ---------------- Shared logic ----------------

    def _resolve_channel(self, guild: discord.Guild) -> tuple[discord.TextChannel | None, str | None]:
        channel_id = db.get_confessions_channel(guild.id)
        if channel_id is None:
            return None, "Confessions aren't set up in this server yet -- ask an admin to run `/confessions setup`."
        channel = guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return None, "The configured confessions channel is missing -- ask an admin to re-run `/confessions setup` or `/confessions channel`."
        return channel, None

    async def _post_panel(self, channel: discord.TextChannel) -> str | None:
        """Posts (or re-posts) the submit panel in `channel`. Returns an error message, or None on success."""
        try:
            await channel.send(embed=build_panel_embed(), view=ConfessionPanelView(self))
        except discord.Forbidden:
            return f"I don't have permission to post in {channel.mention}."
        return None

    async def handle_confession(self, interaction: discord.Interaction, content: str):
        channel, error = self._resolve_channel(interaction.guild)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        key = (interaction.guild_id, interaction.user.id)
        now = time.time()
        last = self._cooldowns.get(key, 0)
        if now - last < CONFESSION_COOLDOWN_SECONDS:
            remaining = int(CONFESSION_COOLDOWN_SECONDS - (now - last))
            await interaction.response.send_message(f"Slow down -- wait {remaining}s before your next confession.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        number = db.next_confession_number(interaction.guild_id)
        confession_id = db.create_confession(interaction.guild_id, number, interaction.user.id, content)
        embed = build_confession_embed(number, content)

        try:
            message = await channel.send(embed=embed, view=ConfessionReportView(self, confession_id))
        except discord.Forbidden:
            await interaction.followup.send(f"I don't have permission to post in {channel.mention} anymore -- ask an admin to check my access there.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"Something went wrong posting your confession: {e}", ephemeral=True)
            return

        db.set_confession_message(confession_id, None, message.id)
        self.bot.add_view(ConfessionReportView(self, confession_id), message_id=message.id)
        self._cooldowns[key] = now

        await interaction.followup.send(f"✅ Your confession was posted anonymously: {message.jump_url}", ephemeral=True)

    async def handle_report(self, interaction: discord.Interaction, confession_id: int):
        row = db.get_confession(confession_id)
        if row is None:
            await interaction.response.send_message("Couldn't find this confession anymore.", ephemeral=True)
            return

        log_embed = discord.Embed(
            title="🚩 Confession Reported",
            description=(
                f"**Confession:** #{row['number']}\n"
                f"**Reported by:** {interaction.user.mention}\n\n"
                f"{row['content'][:1000]}"
            ),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        log_embed.set_footer(text=f"Confession author ID: {row['user_id']} (staff-only -- not shown publicly)")
        await send_log(self.bot, log_embed)
        await interaction.response.send_message("🚩 Reported to staff. Thanks for keeping this space respectful.", ephemeral=True)

    # ---------------- Slash commands ----------------

    @app_commands.command(name="confess", description="Submit an anonymous confession")
    async def confess(self, interaction: discord.Interaction):
        channel, error = self._resolve_channel(interaction.guild)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.send_modal(ConfessionModal(self))

    confessions_group = app_commands.Group(name="confessions", description="Configure anonymous confessions", default_permissions=discord.Permissions(manage_guild=True))

    @confessions_group.command(name="setup", description="[Admin] Create a new confessions channel with the submit panel")
    @app_commands.describe(name="Channel name (default: confessions)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def confessions_setup(self, interaction: discord.Interaction, name: str = "confessions"):
        guild = interaction.guild
        if not guild.me.guild_permissions.manage_channels:
            await interaction.response.send_message("I need the Manage Channels permission to create it.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, create_public_threads=False, add_reactions=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, manage_messages=True),
        }
        try:
            channel = await guild.create_text_channel(name, overwrites=overwrites, reason=f"Confessions setup by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("Discord refused to create the channel -- check my Manage Channels permission.", ephemeral=True)
            return

        db.set_confessions_channel(guild.id, channel.id)
        error = await self._post_panel(channel)
        if error:
            await interaction.followup.send(f"✅ Created {channel.mention}, but {error[0].lower()}{error[1:]}", ephemeral=True)
            return
        await interaction.followup.send(f"✅ Confessions are set up in {channel.mention} -- the submit panel is posted there.", ephemeral=True)

    @confessions_group.command(name="channel", description="[Admin] Use an existing channel for confessions instead")
    @app_commands.describe(channel="The text channel to post confessions in")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def confessions_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db.set_confessions_channel(interaction.guild_id, channel.id)
        try:
            await channel.set_permissions(interaction.guild.default_role, send_messages=False, reason="Confessions channel -- submissions go through the panel, not typed messages")
        except discord.Forbidden:
            pass  # not critical -- confessions will still post fine, people just could also type directly in there
        error = await self._post_panel(channel)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Confessions will now post in {channel.mention} -- the submit panel is posted there.", ephemeral=True)

    @confessions_group.command(name="panel", description="[Admin] Re-post the submit panel in the configured confessions channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def confessions_panel(self, interaction: discord.Interaction):
        channel, error = self._resolve_channel(interaction.guild)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        error = await self._post_panel(channel)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Panel re-posted in {channel.mention}.", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Server permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    # ---------------- Prefix fallback ----------------
    # Confession submission needs a modal popup, which text commands can't
    # open -- so ?confess just points people at the slash command instead.

    @commands.command(name="confess")
    async def confess_text(self, ctx: commands.Context):
        await ctx.reply("Use the slash command `/confess` instead -- it opens a popup form that text commands can't do.", mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Confessions(bot))

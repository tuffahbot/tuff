import discord
from discord import app_commands
from discord.ext import commands

from permissions import SUPER_USER_ID as AUTHORIZED_SAY_USER_ID

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
        "/autorole set <role> — [admin] auto-assign a role to new members",
        "/autorole remove — [admin] turn autorole off",
        "/autorole view — see the current autorole",
        "/rolegive <member> <role> — [admin] give someone a role",
        "/roleremove <member> <role> — [admin] take a role away",
    ],
    "🎈 Fun": [
        "/ship <member> [member2] — see how compatible two people are (defaults to you)",
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
}


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
    )
    async def say(self, interaction: discord.Interaction, message: str, channel: discord.TextChannel = None, reply_to: str = None):
        if interaction.user.id != AUTHORIZED_SAY_USER_ID:
            await interaction.response.send_message("You can't use this command.", ephemeral=True)
            return

        target = channel or interaction.channel

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
            await target.send(message, reference=reference, mention_author=False)
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

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if ctx.command and ctx.command.name in ("say", "sync"):
            return  # silent on purpose, see say_text/sync_text
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Missing argument: `{error.param.name}`.", mention_author=False)
        else:
            print(f"General prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))

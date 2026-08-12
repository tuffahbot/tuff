import discord
from discord import app_commands
from discord.ext import commands

# Only this Discord user ID can use /say.
AUTHORIZED_SAY_USER_ID = 1503282641221320815

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
        "/giveaway reroll <message_id> — [admin] reroll winners",
    ],
    "🔊 Voice": [
        "Join the **Join to Create** voice channel to get your own temporary VC",
        "It's deleted automatically once everyone leaves",
    ],
    "🕵️ Utility": [
        "/snipe — show the last deleted message in this channel",
    ],
    "🛡️ Moderation (mod role or matching Discord permission required)": [
        "/warn, /warnings, /clearwarnings, /removewarning",
        "/kick, /ban, /unban",
        "/timeout, /untimeout",
        "/purge <amount, max 1000>, /slowmode <seconds>",
        "Mod actions are logged to the logs channel instead of posting in chat",
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
    @app_commands.describe(message="What to say", channel="Where to say it (defaults to this channel)")
    async def say(self, interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
        if interaction.user.id != AUTHORIZED_SAY_USER_ID:
            await interaction.response.send_message("You can't use this command.", ephemeral=True)
            return

        target = channel or interaction.channel
        try:
            await target.send(message)
        except discord.Forbidden:
            await interaction.response.send_message(f"I don't have permission to send messages in {target.mention}.", ephemeral=True)
            return
        await interaction.response.send_message(f"Sent in {target.mention}.", ephemeral=True)

    @commands.command(name="say")
    async def say_text(self, ctx: commands.Context, channel: discord.TextChannel = None, *, message: str):
        if ctx.author.id != AUTHORIZED_SAY_USER_ID:
            return  # stay quiet -- don't reveal the command exists to anyone else
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        target = channel or ctx.channel
        try:
            await target.send(message)
        except discord.Forbidden:
            await ctx.author.send(f"I don't have permission to send messages in {target.mention}.")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if ctx.command and ctx.command.name == "say":
            return  # silent on purpose, see say_text
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Missing argument: `{error.param.name}`.", mention_author=False)
        else:
            print(f"General prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))

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

    @app_commands.command(name="help", description="Everything this bot can do")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Bot Commands", color=discord.Color.blurple())
        if interaction.guild and interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        for section, lines in HELP_SECTIONS.items():
            embed.add_field(name=section, value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))

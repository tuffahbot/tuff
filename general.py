import discord
from discord import app_commands
from discord.ext import commands

HELP_SECTIONS = {
    "🎵 Music": [
        "/play <query> — play a song by name or URL",
        "/pause, /resume, /skip, /stop, /leave",
        "/queue, /nowplaying, /volume <0-100>, /loop",
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

    @app_commands.command(name="help", description="List all available commands")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Bot Commands", color=discord.Color.blurple())
        if interaction.guild and interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        for section, lines in HELP_SECTIONS.items():
            embed.add_field(name=section, value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))

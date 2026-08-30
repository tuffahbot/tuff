import discord

LOGS_CHANNEL_ID = 1543459712731844768


async def send_log(bot, embed: discord.Embed):
    """Send an embed to the configured logs channel. No-ops quietly if the
    channel can't be found or the bot lacks permission to post there --
    logging should never be the thing that breaks a command."""
    channel = bot.get_channel(LOGS_CHANNEL_ID)
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass

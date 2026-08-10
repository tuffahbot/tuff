import discord
from discord import app_commands
from discord.ext import commands

from logsutil import send_log


class EventLogs(commands.Cog):
    """
    Posts an audit trail to the logs channel (deleted/edited messages, member
    joins/leaves) and powers /snipe, which recalls the most recently deleted
    message per channel.

    NOTE: Discord only gives us a deleted/edited message's content if the bot
    already had that message cached (i.e. it saw it get sent). If the bot
    hadn't seen a message before it was deleted, Discord doesn't send its
    content at all -- that shows up here as "no text content".
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_deleted: dict[int, dict] = {}  # channel_id -> info about last deleted message

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot:
            return

        self.last_deleted[message.channel.id] = {
            "author": message.author,
            "content": message.content,
            "attachments": [a.url for a in message.attachments],
            "deleted_at": discord.utils.utcnow(),
        }

        embed = discord.Embed(title="🗑️ Message Deleted", color=discord.Color.red(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Author", value=message.author.mention, inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(
            name="Content",
            value=message.content or "*(no text content)*",
            inline=False,
        )
        if message.attachments:
            embed.add_field(name="Attachments", value="\n".join(a.url for a in message.attachments), inline=False)
        embed.set_footer(text=f"User ID: {message.author.id}")
        await send_log(self.bot, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or before.content == after.content:
            return

        embed = discord.Embed(title="✏️ Message Edited", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Author", value=before.author.mention, inline=True)
        embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        embed.add_field(name="Before", value=(before.content or "*(empty)*")[:1024], inline=False)
        embed.add_field(name="After", value=(after.content or "*(empty)*")[:1024], inline=False)
        embed.set_footer(text=f"User ID: {before.author.id}")
        await send_log(self.bot, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        embed = discord.Embed(title="📥 Member Joined", color=discord.Color.green(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Member", value=f"{member.mention} ({member})", inline=False)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, style="R"), inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"User ID: {member.id}")
        await send_log(self.bot, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        embed = discord.Embed(title="📤 Member Left", color=discord.Color.dark_grey(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Member", value=f"{member.mention} ({member})", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"User ID: {member.id}")
        await send_log(self.bot, embed)

    @app_commands.command(name="snipe", description="Show the most recently deleted message in this channel")
    async def snipe(self, interaction: discord.Interaction):
        data = self.last_deleted.get(interaction.channel.id)
        if not data:
            await interaction.response.send_message("Nothing to snipe here.", ephemeral=True)
            return

        embed = discord.Embed(
            description=data["content"] or "*(no text content)*",
            color=discord.Color.blurple(),
            timestamp=data["deleted_at"],
        )
        embed.set_author(name=str(data["author"]), icon_url=data["author"].display_avatar.url)
        if data["attachments"]:
            embed.add_field(name="Attachments", value="\n".join(data["attachments"]), inline=False)
        embed.set_footer(text="Deleted")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(EventLogs(bot))

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import database as db

MAX_REASON_LENGTH = 200
AFK_TAG = "[AFK] "


class AFK(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _since(self, row) -> datetime:
        return datetime.fromisoformat(row["since"]).replace(tzinfo=timezone.utc)

    async def _try_set_nick(self, member: discord.Member, nick: str | None):
        me = member.guild.me
        if not me.guild_permissions.manage_nicknames:
            return
        if member.id != me.id and member.top_role >= me.top_role:
            return  # can't touch someone at/above my own top role (covers the owner too, who'll just 403)
        try:
            await member.edit(nick=nick, reason="AFK toggle")
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _go_afk(self, member: discord.Member, reason: str | None) -> str:
        existing = db.get_afk(member.guild.id, member.id)
        if existing is None:
            # First time going AFK -- remember the real nickname so we can restore it later.
            original_nick = member.nick
            db.set_afk(member.guild.id, member.id, reason, original_nick)
            tagged = f"{AFK_TAG}{member.display_name}"[:32]
            await self._try_set_nick(member, tagged)
        else:
            # Already AFK, just updating the reason -- keep the original nick we already saved.
            db.set_afk(member.guild.id, member.id, reason, existing["original_nick"])
        return "💤 You're now AFK" + (f": {reason}" if reason else ".")

    async def _clear_afk(self, member: discord.Member, row) -> None:
        db.remove_afk(member.guild.id, member.id)
        await self._try_set_nick(member, row["original_nick"])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # If this message IS a "?afk" invocation, the command below will handle
        # state itself -- checking here too would race against it (the command
        # can finish writing the new AFK row before this listener's read runs,
        # making it look like the person "sent a message while AFK" when really
        # this message was the one that just set it).
        ctx = await self.bot.get_context(message)
        is_afk_command = ctx.command is not None and ctx.command.qualified_name == "afk"

        # Sending any other message clears your own AFK.
        if not is_afk_command:
            row = db.get_afk(message.guild.id, message.author.id)
            if row is not None:
                await self._clear_afk(message.author, row)
                try:
                    await message.channel.send(f"👋 Welcome back, {message.author.mention} -- AFK removed.", delete_after=8)
                except discord.Forbidden:
                    pass

        # Let the sender know if they pinged someone who's AFK.
        if message.mentions:
            notices = []
            for user in message.mentions:
                if user.id == message.author.id:
                    continue
                afk_row = db.get_afk(message.guild.id, user.id)
                if afk_row is None:
                    continue
                ago = discord.utils.format_dt(self._since(afk_row), "R")
                reason = afk_row["reason"] or "AFK"
                notices.append(f"💤 {user.mention} is AFK ({ago}): {reason}")
            if notices:
                try:
                    # allowed_mentions=none() is the fix here -- without it, anything
                    # someone put in their AFK reason (@everyone, a role, another
                    # user) fires as a REAL ping the moment someone mentions them.
                    # The AFK user's own mention above still renders/highlights
                    # normally; this only stops it (and the reason text) from
                    # actually notifying anyone.
                    await message.channel.send("\n".join(notices), allowed_mentions=discord.AllowedMentions.none())
                except discord.Forbidden:
                    pass

    @app_commands.command(name="afk", description="Mark yourself as AFK")
    @app_commands.describe(reason="What you're away for (optional)")
    async def afk(self, interaction: discord.Interaction, reason: str = None):
        reason = reason[:MAX_REASON_LENGTH] if reason else None
        text = await self._go_afk(interaction.user, reason)
        await interaction.response.send_message(text, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="afk")
    async def afk_text(self, ctx: commands.Context, *, reason: str = None):
        reason = reason[:MAX_REASON_LENGTH] if reason else None
        text = await self._go_afk(ctx.author, reason)
        await ctx.reply(text, mention_author=False, allowed_mentions=discord.AllowedMentions.none())

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))

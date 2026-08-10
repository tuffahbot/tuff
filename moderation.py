from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from logsutil import send_log

# Anyone with one of these role IDs can use ALL moderation commands below,
# regardless of their Discord permissions. Add/remove IDs as needed.
MOD_ROLE_IDS = {
    1536186361378381924,
    1536186651632738335,
    1536175409199194202,
}


def mod_embed(title: str, description: str, color=discord.Color.orange()) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())


def has_mod_access(**required_perms: bool):
    """
    Passes if the member has one of MOD_ROLE_IDS, OR has all the given
    Discord permissions (e.g. has_mod_access(kick_members=True)).
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        if any(role.id in MOD_ROLE_IDS for role in member.roles):
            return True
        perms = member.guild_permissions
        return all(getattr(perms, perm_name, False) == expected for perm_name, expected in required_perms.items())

    return app_commands.check(predicate)


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log_and_confirm(self, interaction: discord.Interaction, embed: discord.Embed):
        """Mod actions no longer post in the regular channel -- the full
        embed goes to the logs channel, and the moderator gets a short
        ephemeral confirmation only they can see."""
        embed.set_footer(text=f"By {interaction.user} ({interaction.user.id})")
        await send_log(self.bot, embed)
        if not interaction.response.is_done():
            await interaction.response.send_message(f"✅ {embed.title}", ephemeral=True)
        else:
            await interaction.followup.send(f"✅ {embed.title}", ephemeral=True)

    # ---------------- Warnings ----------------

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    @has_mod_access(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        warning_id = db.add_warning(interaction.guild_id, member.id, interaction.user.id, reason)
        count = len(db.get_warnings(interaction.guild_id, member.id))

        embed = mod_embed("⚠️ Member Warned", f"{member.mention} was warned (#{warning_id}).\n**Reason:** {reason}\n**Total warnings:** {count}")
        await self._log_and_confirm(interaction, embed)
        try:
            await member.send(f"You were warned in **{interaction.guild.name}**.\nReason: {reason}")
        except discord.Forbidden:
            pass

    @app_commands.command(name="warnings", description="List a member's warnings")
    @app_commands.describe(member="Member to check")
    @has_mod_access(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        rows = db.get_warnings(interaction.guild_id, member.id)
        if not rows:
            await interaction.response.send_message(f"{member.mention} has no warnings.", ephemeral=True)
            return

        lines = [f"**#{r['id']}** — {r['reason']} (by <@{r['moderator_id']}>, {r['created_at']})" for r in rows]
        await interaction.response.send_message(
            embed=mod_embed(f"Warnings for {member.display_name}", "\n".join(lines)), ephemeral=True
        )

    @app_commands.command(name="clearwarnings", description="Clear all warnings for a member")
    @app_commands.describe(member="Member to clear warnings for")
    @has_mod_access(manage_guild=True)
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member):
        count = db.clear_warnings(interaction.guild_id, member.id)
        embed = mod_embed("🧹 Warnings Cleared", f"Cleared {count} warning(s) for {member.mention}.")
        await self._log_and_confirm(interaction, embed)

    @app_commands.command(name="removewarning", description="Remove a single warning by its ID")
    @app_commands.describe(warning_id="The warning ID (shown in /warnings)")
    @has_mod_access(manage_guild=True)
    async def removewarning(self, interaction: discord.Interaction, warning_id: int):
        removed = db.remove_warning(interaction.guild_id, warning_id)
        if removed:
            embed = mod_embed("🧹 Warning Removed", f"Removed warning #{warning_id}.")
            await self._log_and_confirm(interaction, embed)
        else:
            await interaction.response.send_message(f"No warning with ID #{warning_id} found.", ephemeral=True)

    # ---------------- Kick / Ban ----------------

    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.describe(member="Member to kick", reason="Reason for the kick")
    @has_mod_access(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("You can't kick someone with an equal or higher role.", ephemeral=True)
            return
        try:
            await member.send(f"You were kicked from **{interaction.guild.name}**.\nReason: {reason}")
        except discord.Forbidden:
            pass
        await member.kick(reason=f"{reason} — by {interaction.user}")
        embed = mod_embed("👢 Member Kicked", f"{member.mention} was kicked.\n**Reason:** {reason}")
        await self._log_and_confirm(interaction, embed)

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.describe(member="Member to ban", reason="Reason for the ban", delete_days="Days of message history to delete (0-7)")
    @has_mod_access(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided", delete_days: app_commands.Range[int, 0, 7] = 0):
        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("You can't ban someone with an equal or higher role.", ephemeral=True)
            return
        try:
            await member.send(f"You were banned from **{interaction.guild.name}**.\nReason: {reason}")
        except discord.Forbidden:
            pass
        await member.ban(reason=f"{reason} — by {interaction.user}", delete_message_seconds=delete_days * 86400)
        embed = mod_embed("🔨 Member Banned", f"{member.mention} was banned.\n**Reason:** {reason}", discord.Color.red())
        await self._log_and_confirm(interaction, embed)

    @app_commands.command(name="unban", description="Unban a user by ID")
    @app_commands.describe(user_id="The Discord user ID to unban")
    @has_mod_access(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str):
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user)
            embed = mod_embed("🔓 Member Unbanned", f"Unbanned **{user}**.")
            await self._log_and_confirm(interaction, embed)
        except (discord.NotFound, ValueError):
            await interaction.response.send_message("That user ID isn't banned or doesn't exist.", ephemeral=True)

    # ---------------- Timeout ----------------

    @app_commands.command(name="timeout", description="Timeout (mute) a member for a number of minutes")
    @app_commands.describe(member="Member to time out", minutes="Duration in minutes (max 40320 = 28 days)", reason="Reason")
    @has_mod_access(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason provided"):
        until = discord.utils.utcnow() + timedelta(minutes=minutes)
        await member.timeout(until, reason=f"{reason} — by {interaction.user}")
        embed = mod_embed("🔇 Member Timed Out", f"{member.mention} is timed out for {minutes} minute(s).\n**Reason:** {reason}")
        await self._log_and_confirm(interaction, embed)

    @app_commands.command(name="untimeout", description="Remove a member's timeout")
    @app_commands.describe(member="Member to remove timeout from")
    @has_mod_access(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        await member.timeout(None, reason=f"Timeout removed by {interaction.user}")
        embed = mod_embed("🔊 Timeout Removed", f"Removed timeout for {member.mention}.")
        await self._log_and_confirm(interaction, embed)

    # ---------------- Channel management ----------------

    @app_commands.command(name="purge", description="Bulk delete messages in this channel")
    @app_commands.describe(amount="How many messages to delete (max 1000)")
    @has_mod_access(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1000]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        embed = mod_embed("🧹 Messages Purged", f"Deleted {len(deleted)} message(s) in {interaction.channel.mention}.")
        embed.set_footer(text=f"By {interaction.user} ({interaction.user.id})")
        await send_log(self.bot, embed)
        await interaction.followup.send(f"Deleted {len(deleted)} message(s).", ephemeral=True)

    @app_commands.command(name="slowmode", description="Set slowmode delay for this channel")
    @app_commands.describe(seconds="Delay in seconds (0 to disable, max 21600)")
    @has_mod_access(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=seconds)
        desc = "Slowmode disabled." if seconds == 0 else f"Slowmode set to {seconds} second(s)."
        embed = mod_embed("🐢 Slowmode Updated", f"{desc} ({interaction.channel.mention})")
        await self._log_and_confirm(interaction, embed)

    # ---------------- Error handling for this cog ----------------

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, (app_commands.MissingPermissions, app_commands.CheckFailure)):
            await interaction.response.send_message(
                "You don't have permission to use this command (need the mod role or the relevant Discord permission).",
                ephemeral=True,
            )
        elif isinstance(error, discord.Forbidden):
            await interaction.response.send_message("I don't have permission to do that (check my role position).", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))

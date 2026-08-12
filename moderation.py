from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from logsutil import send_log

# ---------------------------------------------------------------------------
# Rank hierarchy: Owner > Administrator > Moderator > everyone else.
# Each rank can moderate anyone below it, but never someone at its own rank
# or higher -- e.g. an Administrator can kick/ban/timeout a Moderator or a
# regular member, but NOT another Administrator or the Owner. Regular
# members (nobody, tier 0) can be moderated by anyone with command access.
# ---------------------------------------------------------------------------
TIER_OWNER = 3
TIER_ADMIN = 2
TIER_MOD = 1

ROLE_TIERS = {
    1536175409199194202: TIER_OWNER,
    1536186361378381924: TIER_ADMIN,
    1536186651632738335: TIER_MOD,
}


def get_tier(member: discord.Member) -> int:
    return max((ROLE_TIERS.get(role.id, 0) for role in member.roles), default=0)


def can_moderate(actor: discord.Member, target: discord.Member) -> bool:
    """Whether actor is allowed to run a moderation action on target."""
    if actor.id == target.id:
        return False
    target_tier = get_tier(target)
    if target_tier == 0:
        return True  # regular members are fair game for anyone with command access
    return get_tier(actor) > target_tier


def mod_embed(title: str, description: str, color=discord.Color.orange()) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())


def has_mod_access(**required_perms: bool):
    """
    Passes if the member holds the Owner, Administrator, or Moderator role
    (see ROLE_TIERS), OR has all the given Discord permissions (e.g.
    has_mod_access(kick_members=True)). This only gates whether the command
    can be used at all -- can_moderate() above still applies per-target.
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        if get_tier(member) > 0:
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

    async def _check_hierarchy(self, interaction: discord.Interaction, target: discord.Member) -> bool:
        if can_moderate(interaction.user, target):
            return True
        if interaction.user.id == target.id:
            msg = "You can't use that on yourself."
        else:
            msg = "You can't moderate someone at your rank or above."
        await interaction.response.send_message(msg, ephemeral=True)
        return False

    # ---------------- Warnings ----------------

    @app_commands.command(name="warn", description="Give someone a warning")
    @app_commands.describe(member="Who", reason="What they did")
    @has_mod_access(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if not await self._check_hierarchy(interaction, member):
            return
        warning_id = db.add_warning(interaction.guild_id, member.id, interaction.user.id, reason)
        count = len(db.get_warnings(interaction.guild_id, member.id))

        embed = mod_embed("⚠️ Member Warned", f"{member.mention} was warned (#{warning_id}).\n**Reason:** {reason}\n**Total warnings:** {count}")
        await self._log_and_confirm(interaction, embed)
        try:
            await member.send(f"You were warned in **{interaction.guild.name}**.\nReason: {reason}")
        except discord.Forbidden:
            pass

    @app_commands.command(name="warnings", description="Pull up someone's warning history")
    @app_commands.describe(member="Who")
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

    @app_commands.command(name="clearwarnings", description="Wipe someone's warnings clean")
    @app_commands.describe(member="Who")
    @has_mod_access(manage_guild=True)
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member):
        if not await self._check_hierarchy(interaction, member):
            return
        count = db.clear_warnings(interaction.guild_id, member.id)
        embed = mod_embed("🧹 Warnings Cleared", f"Cleared {count} warning(s) for {member.mention}.")
        await self._log_and_confirm(interaction, embed)

    @app_commands.command(name="removewarning", description="Delete one specific warning")
    @app_commands.describe(warning_id="ID from /warnings")
    @has_mod_access(manage_guild=True)
    async def removewarning(self, interaction: discord.Interaction, warning_id: int):
        removed = db.remove_warning(interaction.guild_id, warning_id)
        if removed:
            embed = mod_embed("🧹 Warning Removed", f"Removed warning #{warning_id}.")
            await self._log_and_confirm(interaction, embed)
        else:
            await interaction.response.send_message(f"No warning with ID #{warning_id} found.", ephemeral=True)

    # ---------------- Kick / Ban ----------------

    @app_commands.command(name="kick", description="Kick someone out of the server")
    @app_commands.describe(member="Who", reason="Why")
    @has_mod_access(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if not await self._check_hierarchy(interaction, member):
            return
        try:
            await member.send(f"You were kicked from **{interaction.guild.name}**.\nReason: {reason}")
        except discord.Forbidden:
            pass
        await member.kick(reason=f"{reason} — by {interaction.user}")
        embed = mod_embed("👢 Member Kicked", f"{member.mention} was kicked.\n**Reason:** {reason}")
        await self._log_and_confirm(interaction, embed)

    @app_commands.command(name="ban", description="Ban someone")
    @app_commands.describe(member="Who", reason="Why", delete_days="Wipe their last N days of messages (0-7)")
    @has_mod_access(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided", delete_days: app_commands.Range[int, 0, 7] = 0):
        if not await self._check_hierarchy(interaction, member):
            return
        try:
            await member.send(f"You were banned from **{interaction.guild.name}**.\nReason: {reason}")
        except discord.Forbidden:
            pass
        await member.ban(reason=f"{reason} — by {interaction.user}", delete_message_seconds=delete_days * 86400)
        embed = mod_embed("🔨 Member Banned", f"{member.mention} was banned.\n**Reason:** {reason}", discord.Color.red())
        await self._log_and_confirm(interaction, embed)

    @app_commands.command(name="unban", description="Lift a ban using their user ID")
    @app_commands.describe(user_id="Their Discord user ID")
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

    @app_commands.command(name="timeout", description="Mute someone for a bit")
    @app_commands.describe(member="Who", minutes="How long, in minutes (up to 40320 = 28 days)", reason="Why")
    @has_mod_access(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason provided"):
        if not await self._check_hierarchy(interaction, member):
            return
        until = discord.utils.utcnow() + timedelta(minutes=minutes)
        await member.timeout(until, reason=f"{reason} — by {interaction.user}")
        try:
            await member.send(f"You're timed out in **{interaction.guild.name}** for {minutes} minute(s).\nReason: {reason}")
        except discord.Forbidden:
            pass
        embed = mod_embed("🔇 Member Timed Out", f"{member.mention} is timed out for {minutes} minute(s).\n**Reason:** {reason}")
        await self._log_and_confirm(interaction, embed)

    @app_commands.command(name="untimeout", description="Lift someone's timeout early")
    @app_commands.describe(member="Who")
    @has_mod_access(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        if not await self._check_hierarchy(interaction, member):
            return
        await member.timeout(None, reason=f"Timeout removed by {interaction.user}")
        embed = mod_embed("🔊 Timeout Removed", f"Removed timeout for {member.mention}.")
        await self._log_and_confirm(interaction, embed)

    # ---------------- Channel management ----------------

    @app_commands.command(name="purge", description="Mass-delete messages in this channel")
    @app_commands.describe(amount="How many (up to 1000)")
    @has_mod_access(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1000]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        embed = mod_embed("🧹 Messages Purged", f"Deleted {len(deleted)} message(s) in {interaction.channel.mention}.")
        embed.set_footer(text=f"By {interaction.user} ({interaction.user.id})")
        await send_log(self.bot, embed)
        await interaction.followup.send(f"Deleted {len(deleted)} message(s).", ephemeral=True)

    @app_commands.command(name="slowmode", description="Turn slowmode on/off for this channel")
    @app_commands.describe(seconds="Seconds between messages (0 turns it off, max 21600)")
    @has_mod_access(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=seconds)
        desc = "Slowmode disabled." if seconds == 0 else f"Slowmode set to {seconds} second(s)."
        embed = mod_embed("🐢 Slowmode Updated", f"{desc} ({interaction.channel.mention})")
        await self._log_and_confirm(interaction, embed)

    # ---------------- Text/prefix command versions (e.g. "?ban @user spam") ----------------
    # Same hierarchy rules and logging as the slash commands above -- these
    # just let mods type them as plain messages instead. Whatever prefix is
    # set via COMMAND_PREFIX (default "!") is what triggers them.

    def _has_access(self, member: discord.Member, **required_perms: bool) -> bool:
        if get_tier(member) > 0:
            return True
        perms = member.guild_permissions
        return all(getattr(perms, perm_name, False) == expected for perm_name, expected in required_perms.items())

    async def _deny(self, ctx: commands.Context, msg: str):
        try:
            await ctx.message.add_reaction("❌")
        except discord.HTTPException:
            pass
        await ctx.reply(msg, mention_author=False, delete_after=8)

    async def _hierarchy_ok(self, ctx: commands.Context, target: discord.Member) -> bool:
        if can_moderate(ctx.author, target):
            return True
        msg = "You can't use that on yourself." if ctx.author.id == target.id else "You can't moderate someone at your rank or above."
        await self._deny(ctx, msg)
        return False

    async def _reply_and_log(self, ctx: commands.Context, embed: discord.Embed):
        embed.set_footer(text=f"By {ctx.author} ({ctx.author.id})")
        await send_log(self.bot, embed)
        try:
            await ctx.message.add_reaction("✅")
        except discord.HTTPException:
            pass

    @commands.command(name="kick")
    async def kick_text(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not self._has_access(ctx.author, kick_members=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        if not await self._hierarchy_ok(ctx, member):
            return
        try:
            await member.send(f"You were kicked from **{ctx.guild.name}**.\nReason: {reason}")
        except discord.Forbidden:
            pass
        await member.kick(reason=f"{reason} — by {ctx.author}")
        await self._reply_and_log(ctx, mod_embed("👢 Member Kicked", f"{member.mention} was kicked.\n**Reason:** {reason}"))

    @commands.command(name="ban")
    async def ban_text(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not self._has_access(ctx.author, ban_members=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        if not await self._hierarchy_ok(ctx, member):
            return
        try:
            await member.send(f"You were banned from **{ctx.guild.name}**.\nReason: {reason}")
        except discord.Forbidden:
            pass
        await member.ban(reason=f"{reason} — by {ctx.author}")
        await self._reply_and_log(ctx, mod_embed("🔨 Member Banned", f"{member.mention} was banned.\n**Reason:** {reason}", discord.Color.red()))

    @commands.command(name="unban")
    async def unban_text(self, ctx: commands.Context, user_id: int):
        if not self._has_access(ctx.author, ban_members=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user)
        except (discord.NotFound, discord.HTTPException):
            await self._deny(ctx, "That user ID isn't banned or doesn't exist.")
            return
        await self._reply_and_log(ctx, mod_embed("🔓 Member Unbanned", f"Unbanned **{user}**."))

    @commands.command(name="timeout")
    async def timeout_text(self, ctx: commands.Context, member: discord.Member, minutes: int, *, reason: str = "No reason provided"):
        if not self._has_access(ctx.author, moderate_members=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        if not await self._hierarchy_ok(ctx, member):
            return
        if not 1 <= minutes <= 40320:
            await self._deny(ctx, "Minutes must be between 1 and 40320 (28 days).")
            return
        until = discord.utils.utcnow() + timedelta(minutes=minutes)
        await member.timeout(until, reason=f"{reason} — by {ctx.author}")
        try:
            await member.send(f"You're timed out in **{ctx.guild.name}** for {minutes} minute(s).\nReason: {reason}")
        except discord.Forbidden:
            pass
        await self._reply_and_log(ctx, mod_embed("🔇 Member Timed Out", f"{member.mention} is timed out for {minutes} minute(s).\n**Reason:** {reason}"))

    @commands.command(name="untimeout")
    async def untimeout_text(self, ctx: commands.Context, member: discord.Member):
        if not self._has_access(ctx.author, moderate_members=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        if not await self._hierarchy_ok(ctx, member):
            return
        await member.timeout(None, reason=f"Timeout removed by {ctx.author}")
        await self._reply_and_log(ctx, mod_embed("🔊 Timeout Removed", f"Removed timeout for {member.mention}."))

    @commands.command(name="warn")
    async def warn_text(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not self._has_access(ctx.author, moderate_members=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        if not await self._hierarchy_ok(ctx, member):
            return
        warning_id = db.add_warning(ctx.guild.id, member.id, ctx.author.id, reason)
        count = len(db.get_warnings(ctx.guild.id, member.id))
        await self._reply_and_log(ctx, mod_embed("⚠️ Member Warned", f"{member.mention} was warned (#{warning_id}).\n**Reason:** {reason}\n**Total warnings:** {count}"))
        try:
            await member.send(f"You were warned in **{ctx.guild.name}**.\nReason: {reason}")
        except discord.Forbidden:
            pass

    @commands.command(name="purge")
    async def purge_text(self, ctx: commands.Context, amount: int):
        if not self._has_access(ctx.author, manage_messages=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        if not 1 <= amount <= 1000:
            await self._deny(ctx, "Amount must be between 1 and 1000.")
            return
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        deleted = await ctx.channel.purge(limit=amount)
        embed = mod_embed("🧹 Messages Purged", f"Deleted {len(deleted)} message(s) in {ctx.channel.mention}.")
        embed.set_footer(text=f"By {ctx.author} ({ctx.author.id})")
        await send_log(self.bot, embed)
        await ctx.send(f"Deleted {len(deleted)} message(s).", delete_after=5)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MemberNotFound):
            await self._deny(ctx, "Couldn't find that member.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await self._deny(ctx, f"Missing argument: `{error.param.name}`.")
        elif isinstance(error, commands.BadArgument):
            await self._deny(ctx, "Check your arguments -- e.g. minutes/amount need to be plain numbers.")
        else:
            original = getattr(error, "original", error)
            if isinstance(original, discord.Forbidden):
                await self._deny(ctx, "Discord won't let me do that to them (role position, or they have Administrator).")
            else:
                print(f"Prefix mod command error: {error}")

    # ---------------- Error handling for slash commands in this cog ----------------

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        # app_commands wraps exceptions raised inside a command as
        # CommandInvokeError, with the real exception in .original --
        # unwrap it or isinstance checks below never match.
        original = getattr(error, "original", error)

        if isinstance(error, (app_commands.MissingPermissions, app_commands.CheckFailure)):
            await interaction.response.send_message(
                "You don't have permission to use this command (need the Owner/Administrator/Moderator role or the relevant Discord permission).",
                ephemeral=True,
            )
        elif isinstance(original, discord.Forbidden):
            await interaction.response.send_message(
                "Discord won't let me do that to them. Two likely reasons: my role needs to be "
                "moved above theirs in Server Settings → Roles, or -- if this was a timeout -- "
                "Discord never allows timing out anyone with the Administrator permission, no "
                "matter who's asking or how the roles are ordered.",
                ephemeral=True,
            )
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {original}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))

from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from logsutil import send_log
from permissions import SUPER_USER_ID

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
    1540124311522779327: TIER_OWNER,
    1540124807000105040: TIER_ADMIN,
    1540150015182372956: TIER_MOD,
}


def get_tier(member: discord.Member) -> int:
    if member.id == SUPER_USER_ID:
        return TIER_OWNER + 1  # always outranks everyone -- can moderate anyone, including other Owners
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


def warning_dm_embed(guild: discord.Guild, moderator, reason: str, warning_id: int) -> discord.Embed:
    """The card DMed to the warned member -- matches the reference bot's Warning card layout."""
    embed = discord.Embed(
        title=f"Warning · {guild.name}",
        description=(
            f"**Reason**\n{reason}\n\n"
            f"**Issued by**\n{moderator} · {moderator.mention}\n\n"
            f"*Warn ID · `{warning_id}`*\n\n"
            "If you think this is a mistake, open a ticket or contact staff."
        ),
        color=discord.Color.orange(),
    )
    embed.add_field(name="\u200b", value="*Made by **Mercyy** for **Friends***", inline=False)
    return embed


# ---------------------------------------------------------------------------
# Dyno-style "you used this command wrong" help cards. Shown automatically
# whenever someone's missing a required argument (or gives a bad one) on a
# prefix mod command -- e.g. typing "?ban" with no member. Add an entry here
# for any other prefix command that should get this treatment.
# ---------------------------------------------------------------------------
COMMAND_USAGE = {
    "ban": {
        "description": "Ban a member from the server.",
        "usage": ["?ban [member] [reason]"],
        "example": ["?ban @bean making bugs"],
    },
    "kick": {
        "description": "Kick a member out of the server.",
        "usage": ["?kick [member] [reason]"],
        "example": ["?kick @bean stop that"],
    },
    "unban": {
        "description": "Lift a ban using their user ID.",
        "usage": ["?unban [user_id]"],
        "example": ["?unban 123456789012345678"],
    },
    "timeout": {
        "description": "Mute a member for a set number of minutes.",
        "usage": ["?timeout [member] [minutes] [reason]"],
        "example": ["?timeout @bean 30 cool off"],
    },
    "untimeout": {
        "description": "Lift a member's timeout early.",
        "usage": ["?untimeout [member]"],
        "example": ["?untimeout @bean"],
    },
    "warn": {
        "description": "Give a member a warning.",
        "usage": ["?warn [member] [reason]"],
        "example": ["?warn @bean spamming links"],
    },
    "purge": {
        "description": "Mass-delete messages in this channel.",
        "usage": ["?purge [amount]"],
        "example": ["?purge 50"],
    },
    "warnings": {
        "description": "Pull up someone's warning history.",
        "usage": ["?warnings [member]"],
        "example": ["?warnings @bean"],
    },
    "clearwarnings": {
        "description": "Wipe someone's warnings clean.",
        "usage": ["?clearwarnings [member]"],
        "example": ["?clearwarnings @bean"],
    },
    "removewarning": {
        "description": "Delete one specific warning by its ID.",
        "usage": ["?removewarning [warning_id]"],
        "example": ["?removewarning 42"],
    },
    "slowmode": {
        "description": "Turn slowmode on/off for this channel.",
        "usage": ["?slowmode [seconds]"],
        "example": ["?slowmode 10"],
    },
}


def usage_embed(command_name: str) -> discord.Embed | None:
    info = COMMAND_USAGE.get(command_name)
    if info is None:
        return None
    lines = [f"**Description:** {info['description']}", "**Usage:**"]
    lines.extend(info["usage"])
    lines.append("**Example:**")
    lines.extend(info["example"])
    embed = discord.Embed(
        title=f"Command: ?{command_name}",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    return embed


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
            await member.send(embed=warning_dm_embed(interaction.guild, interaction.user, reason, warning_id))
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
    # set via COMMAND_PREFIX (default "?") is what triggers them.

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
        # Public confirmation in the channel the command was run in (Dyno-style),
        # separate from the full embed that goes to the mod-log channel above.
        try:
            await ctx.send(embed=embed)
        except discord.Forbidden:
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
            await member.send(embed=warning_dm_embed(ctx.guild, ctx.author, reason, warning_id))
        except discord.Forbidden:
            pass

    @commands.command(name="warnings")
    async def warnings_text(self, ctx: commands.Context, member: discord.Member):
        if not self._has_access(ctx.author, moderate_members=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        rows = db.get_warnings(ctx.guild.id, member.id)
        if not rows:
            await ctx.reply(f"{member.mention} has no warnings.", mention_author=False)
            return
        lines = [f"**#{r['id']}** — {r['reason']} (by <@{r['moderator_id']}>, {r['created_at']})" for r in rows]
        await ctx.reply(embed=mod_embed(f"Warnings for {member.display_name}", "\n".join(lines)), mention_author=False)

    @commands.command(name="clearwarnings")
    async def clearwarnings_text(self, ctx: commands.Context, member: discord.Member):
        if not self._has_access(ctx.author, manage_guild=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        if not await self._hierarchy_ok(ctx, member):
            return
        count = db.clear_warnings(ctx.guild.id, member.id)
        await self._reply_and_log(ctx, mod_embed("🧹 Warnings Cleared", f"Cleared {count} warning(s) for {member.mention}."))

    @commands.command(name="removewarning")
    async def removewarning_text(self, ctx: commands.Context, warning_id: int):
        if not self._has_access(ctx.author, manage_guild=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        removed = db.remove_warning(ctx.guild.id, warning_id)
        if removed:
            await self._reply_and_log(ctx, mod_embed("🧹 Warning Removed", f"Removed warning #{warning_id}."))
        else:
            await ctx.reply(f"No warning with ID #{warning_id} found.", mention_author=False)

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

    @commands.command(name="slowmode")
    async def slowmode_text(self, ctx: commands.Context, seconds: int):
        if not self._has_access(ctx.author, manage_channels=True):
            await self._deny(ctx, "You don't have permission to do that.")
            return
        if not 0 <= seconds <= 21600:
            await self._deny(ctx, "Seconds must be between 0 and 21600.")
            return
        await ctx.channel.edit(slowmode_delay=seconds)
        desc = "Slowmode disabled." if seconds == 0 else f"Slowmode set to {seconds} second(s)."
        await self._reply_and_log(ctx, mod_embed("🐢 Slowmode Updated", f"{desc} ({ctx.channel.mention})"))

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        command_name = ctx.command.name if ctx.command else None
        usage = usage_embed(command_name) if command_name else None

        if isinstance(error, (commands.MemberNotFound, commands.MissingRequiredArgument, commands.BadArgument)):
            if usage is not None:
                try:
                    await ctx.message.add_reaction("❌")
                except discord.HTTPException:
                    pass
                await ctx.reply(embed=usage, mention_author=False)
            elif isinstance(error, commands.MemberNotFound):
                await self._deny(ctx, "Couldn't find that member.")
            elif isinstance(error, commands.MissingRequiredArgument):
                await self._deny(ctx, f"Missing argument: `{error.param.name}`.")
            else:
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

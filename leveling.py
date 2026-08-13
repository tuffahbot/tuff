import random
import time

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from logsutil import send_log

# ---------------------------------------------------------------------------
# Level -> role name. Create these roles with /setuplevelroles (or manually,
# exact name match, case-sensitive). /setuplevelroles grants each a small
# set of cosmetic permissions by default (see LEVEL_PERMISSIONS below) -- feel
# free to add/remove perks yourself in Server Settings -> Roles afterward.
# ---------------------------------------------------------------------------
LEVEL_ROLES = {
    5: "Level 5",
    10: "Level 10",
    25: "Level 25",
    50: "Level 50",
}

LEVEL_PERMISSIONS = {
    5: discord.Permissions(use_external_emojis=True, use_external_stickers=True),
    10: discord.Permissions(use_external_emojis=True, use_external_stickers=True, attach_files=True, embed_links=True),
    25: discord.Permissions(use_external_emojis=True, use_external_stickers=True, attach_files=True, embed_links=True, change_nickname=True, priority_speaker=True),
    50: discord.Permissions(use_external_emojis=True, use_external_stickers=True, attach_files=True, embed_links=True, change_nickname=True, priority_speaker=True, stream=True),
}

LEVEL_PERK_DESCRIPTIONS = {
    5: "External emojis & stickers",
    10: "+ Attach files & embed links",
    25: "+ Change nickname & voice priority speaker",
    50: "+ Go live / stream in voice",
}

LEVEL_COLORS = {
    5: discord.Color.light_gray(),
    10: discord.Color.green(),
    25: discord.Color.blue(),
    50: discord.Color.gold(),
}

XP_MIN, XP_MAX = 8, 15        # xp awarded per eligible message (was 15-25)
XP_COOLDOWN_SECONDS = 90      # per-user cooldown to prevent spam-leveling (was 60)


def xp_for_level(level: int) -> int:
    """Total cumulative XP required to reach `level`. Roughly 2x the old
    MEE6-style curve, on top of the lower per-message XP above -- leveling
    is noticeably slower end-to-end now."""
    return 10 * (level ** 2) + 100 * level + 200


def level_from_xp(xp: int) -> int:
    level = 0
    while xp >= xp_for_level(level + 1):
        level += 1
    return level


class ConfirmResetView(discord.ui.View):
    def __init__(self, author_id: int, guild_id: int):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who ran this command can confirm.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        count = db.reset_server_xp(self.guild_id)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"✅ Reset XP for **{count}** member(s).", view=self)

        log_embed = discord.Embed(
            title="⚠️ Server XP Reset",
            description=f"{interaction.user.mention} wiped XP/levels for **{count}** member(s) in **{interaction.guild.name}**.",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        log_embed.set_footer(text=f"By {interaction.user} ({interaction.user.id})")
        await send_log(interaction.client, log_embed)

        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Cancelled.", view=self)
        self.stop()


class Leveling(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        key = (message.guild.id, message.author.id)
        now = time.time()
        last = self._cooldowns.get(key, 0)
        if now - last < XP_COOLDOWN_SECONDS:
            return
        self._cooldowns[key] = now

        xp, level = db.get_user_xp(message.guild.id, message.author.id)
        xp += random.randint(XP_MIN, XP_MAX)
        new_level = level_from_xp(xp)
        db.set_user_xp(message.guild.id, message.author.id, xp, new_level)

        if new_level > level:
            await self._handle_level_up(message, new_level)

    async def _handle_level_up(self, message: discord.Message, new_level: int):
        role_unlocked = None
        role_name = LEVEL_ROLES.get(new_level)
        if role_name:
            role = discord.utils.get(message.guild.roles, name=role_name)
            me = message.guild.me
            if role and me.guild_permissions.manage_roles and role < me.top_role:
                try:
                    await message.author.add_roles(role, reason=f"Reached level {new_level}")
                    role_unlocked = role
                except discord.Forbidden:
                    pass

        xp, _ = db.get_user_xp(message.guild.id, message.author.id)
        current_floor = xp_for_level(new_level)
        next_floor = xp_for_level(new_level + 1)
        progress = xp - current_floor
        needed = next_floor - current_floor
        bar_filled = int((progress / needed) * 12) if needed else 0
        bar = "🟩" * bar_filled + "⬛" * (12 - bar_filled)

        embed = discord.Embed(
            title="Level up",
            description=(
                f"You reached **level {new_level}** on **{message.guild.name}**!\n"
                f"**Before** · {new_level - 1}\n"
                f"**Server** · {message.guild.name} (`{message.guild.id}`)"
            ),
            color=LEVEL_COLORS.get(new_level, discord.Color.blurple()),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=message.author.display_avatar.url)
        embed.add_field(name="Total XP", value=f"{xp:,}", inline=True)
        embed.add_field(name="Progress to Next Level", value=f"{progress:,} / {needed:,}", inline=True)
        embed.add_field(name="\u200b", value=bar, inline=False)
        if role_unlocked:
            perks = LEVEL_PERK_DESCRIPTIONS.get(new_level)
            embed.add_field(
                name=f"🔓 Unlocked: {role_unlocked.name}",
                value=perks or "New role!",
                inline=False,
            )
        embed.add_field(name="\u200b", value="*Made by **Mercyy** for **Friends***", inline=False)

        try:
            await message.author.send(embed=embed)
        except discord.Forbidden:
            # DMs closed -- fall back to a quiet channel ping so it isn't lost
            try:
                await message.channel.send(
                    f"🎉 {message.author.mention} leveled up to **level {new_level}**! "
                    f"(Open your DMs to get this privately next time.)"
                )
            except discord.Forbidden:
                pass

    def _rank_embed(self, guild: discord.Guild, target: discord.Member) -> discord.Embed:
        xp, level = db.get_user_xp(guild.id, target.id)
        position = db.get_rank(guild.id, target.id)

        current_floor = xp_for_level(level)
        next_floor = xp_for_level(level + 1)
        progress = xp - current_floor
        needed = next_floor - current_floor
        bar_filled = int((progress / needed) * 20) if needed else 0
        bar = "▰" * bar_filled + "▱" * (20 - bar_filled)
        percent = int((progress / needed) * 100) if needed else 0

        embed = discord.Embed(
            title=target.display_name,
            description=(
                f"Level {level} · Rank {f'#{position}' if position else '—'}\n"
                f"{bar}\n"
                f"┃ {percent}%\n"
                f"XP {xp:,} · {progress:,}/{needed:,} to level {level + 1}"
            ),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text="Made by Mercyy")
        return embed

    def _leaderboard_embed(self, guild: discord.Guild) -> discord.Embed | None:
        rows = db.get_leaderboard(guild.id, limit=10)
        if not rows:
            return None
        lines = []
        for i, row in enumerate(rows, start=1):
            member = guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            lines.append(f"{i}. {name} - lvl {row['level']} ({row['xp']} XP)")

        embed = discord.Embed(
            title="XP leaderboard",
            description=(
                f"{guild.name} · Top 10\n\n"
                + "\n".join(lines)
                + "\n\n*May change with each XP message.*"
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Made by Mercyy")
        return embed

    async def _setup_level_roles(self, guild: discord.Guild, requester) -> discord.Embed:
        created, existing = [], []
        for level, role_name in LEVEL_ROLES.items():
            role = discord.utils.get(guild.roles, name=role_name)
            if role is not None:
                existing.append(role_name)
                continue
            role = await guild.create_role(
                name=role_name,
                color=LEVEL_COLORS.get(level, discord.Color.default()),
                permissions=LEVEL_PERMISSIONS.get(level, discord.Permissions.none()),
                hoist=False,
                mentionable=False,
                reason=f"Level role setup requested by {requester}",
            )
            created.append(role_name)

        embed = discord.Embed(title="Level Roles Setup", color=discord.Color.blurple())
        if created:
            embed.add_field(name="✅ Created", value=", ".join(created), inline=False)
        if existing:
            embed.add_field(name="ℹ️ Already Existed", value=", ".join(existing), inline=False)
        embed.add_field(
            name="Note",
            value=(
                "New roles start with a small set of cosmetic perks (emojis, attach files, etc. — "
                "scaling up by level) and are created at the bottom of the role list. "
                "Drag them above where you want and tweak permissions in Server Settings → Roles anytime."
            ),
            inline=False,
        )

        log_embed = discord.Embed(
            title="⚙️ Level Roles Set Up",
            description=f"{requester.mention} ran level role setup. Created: {', '.join(created) or 'none'}. Already existed: {', '.join(existing) or 'none'}.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        log_embed.set_footer(text=f"By {requester} ({requester.id})")
        await send_log(self.bot, log_embed)

        return embed

    async def _apply_xp_change(self, guild: discord.Guild, member: discord.Member, amount: int, requester) -> tuple[discord.Embed, int, int]:
        """Returns (embed, old_level, new_level). amount can be negative."""
        xp, level = db.get_user_xp(guild.id, member.id)
        xp = max(0, xp + amount)
        new_level = level_from_xp(xp)
        db.set_user_xp(guild.id, member.id, xp, new_level)
        verb = "Gave" if amount >= 0 else "Removed"
        color = discord.Color.green() if amount >= 0 else discord.Color.orange()
        embed = discord.Embed(
            description=f"{verb} **{abs(amount)} XP** {'to' if amount >= 0 else 'from'} {member.mention}. Now at **{xp} XP** (level {new_level}).",
            color=color,
        )

        log_embed = discord.Embed(
            title=f"⚙️ XP {verb}",
            description=f"{requester.mention} {verb.lower()} **{abs(amount)} XP** {'to' if amount >= 0 else 'from'} {member.mention}. Now at **{xp} XP** (level {new_level}).",
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        log_embed.set_footer(text=f"By {requester} ({requester.id})")
        await send_log(self.bot, log_embed)

        return embed, level, new_level

    async def _announce_role_unlock(self, guild: discord.Guild, member: discord.Member, new_level: int, channel: discord.abc.Messageable):
        role_name = LEVEL_ROLES.get(new_level)
        if not role_name:
            return
        role = discord.utils.get(guild.roles, name=role_name)
        me = guild.me
        if role and me.guild_permissions.manage_roles and role < me.top_role:
            try:
                await member.add_roles(role, reason=f"Reached level {new_level}")
                await channel.send(f"🔓 {member.mention} unlocked the **{role.name}** role!")
            except discord.Forbidden:
                pass

    @app_commands.command(name="rank", description="Check your rank, or someone else's")
    @app_commands.describe(member="Leave blank for yourself")
    async def rank(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        is_self = target.id == interaction.user.id
        await interaction.response.send_message(embed=self._rank_embed(interaction.guild, target), ephemeral=is_self)

    @commands.command(name="rank")
    async def rank_text(self, ctx: commands.Context, member: discord.Member = None):
        target = member or ctx.author
        await ctx.reply(embed=self._rank_embed(ctx.guild, target), mention_author=False)

    @app_commands.command(name="leaderboard", description="Who's at the top of the server")
    async def leaderboard(self, interaction: discord.Interaction):
        embed = self._leaderboard_embed(interaction.guild)
        if embed is None:
            await interaction.response.send_message("No one has earned XP yet.")
            return
        await interaction.response.send_message(embed=embed)

    @commands.command(name="leaderboard")
    async def leaderboard_text(self, ctx: commands.Context):
        embed = self._leaderboard_embed(ctx.guild)
        if embed is None:
            await ctx.reply("No one has earned XP yet.", mention_author=False)
            return
        await ctx.reply(embed=embed, mention_author=False)

    @app_commands.command(name="setuplevelroles", description="[Admin] Set up the level roles if they're missing")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def setuplevelroles(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed = await self._setup_level_roles(interaction.guild, interaction.user)
        await interaction.followup.send(embed=embed)

    @commands.command(name="setuplevelroles")
    @commands.has_permissions(manage_roles=True)
    async def setuplevelroles_text(self, ctx: commands.Context):
        embed = await self._setup_level_roles(ctx.guild, ctx.author)
        await ctx.reply(embed=embed, mention_author=False)

    @app_commands.command(name="give_xp", description="[Admin] Hand someone some XP")
    @app_commands.describe(member="Who", amount="How much")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def give_xp(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000]):
        embed, level, new_level = await self._apply_xp_change(interaction.guild, member, amount, interaction.user)
        await interaction.response.send_message(embed=embed)
        if new_level > level:
            await self._announce_role_unlock(interaction.guild, member, new_level, interaction.channel)

    @commands.command(name="give_xp")
    @commands.has_permissions(manage_guild=True)
    async def give_xp_text(self, ctx: commands.Context, member: discord.Member, amount: int):
        if not 1 <= amount <= 1_000_000:
            await ctx.reply("Amount must be between 1 and 1,000,000.", mention_author=False)
            return
        embed, level, new_level = await self._apply_xp_change(ctx.guild, member, amount, ctx.author)
        await ctx.reply(embed=embed, mention_author=False)
        if new_level > level:
            await self._announce_role_unlock(ctx.guild, member, new_level, ctx.channel)

    @app_commands.command(name="remove_xp", description="[Admin] Take XP away from someone")
    @app_commands.describe(member="Who", amount="How much")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_xp(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000]):
        embed, _, _ = await self._apply_xp_change(interaction.guild, member, -amount, interaction.user)
        await interaction.response.send_message(embed=embed)

    @commands.command(name="remove_xp")
    @commands.has_permissions(manage_guild=True)
    async def remove_xp_text(self, ctx: commands.Context, member: discord.Member, amount: int):
        if not 1 <= amount <= 1_000_000:
            await ctx.reply("Amount must be between 1 and 1,000,000.", mention_author=False)
            return
        embed, _, _ = await self._apply_xp_change(ctx.guild, member, -amount, ctx.author)
        await ctx.reply(embed=embed, mention_author=False)

    @app_commands.command(name="resetxp", description="[Admin] Wipe every member's XP — careful with this one")
    @app_commands.checks.has_permissions(administrator=True)
    async def resetxp(self, interaction: discord.Interaction):
        view = ConfirmResetView(interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(
            "⚠️ This will wipe **all** XP and levels for **every member** in this server. This can't be undone.",
            view=view,
            ephemeral=True,
        )

    @commands.command(name="resetxp")
    @commands.has_permissions(administrator=True)
    async def resetxp_text(self, ctx: commands.Context):
        view = ConfirmResetView(ctx.author.id, ctx.guild.id)
        await ctx.reply(
            "⚠️ This will wipe **all** XP and levels for **every member** in this server. This can't be undone.",
            view=view,
            mention_author=False,
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You don't have permission to use this command.", mention_author=False)
        elif isinstance(error, commands.MemberNotFound):
            await ctx.reply("Couldn't find that member.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Missing argument: `{error.param.name}`.", mention_author=False)
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("Check your arguments -- amount needs to be a plain number.", mention_author=False)
        else:
            print(f"Leveling prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))

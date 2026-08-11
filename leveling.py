import random
import time

import discord
from discord import app_commands
from discord.ext import commands

import database as db

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
XP_COOLDOWN_SECONDS = 75      # per-user cooldown to prevent spam-leveling (was 90)


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
            title=f"🎉 Level {new_level}!",
            description=f"You leveled up in **{message.guild.name}**!",
            color=LEVEL_COLORS.get(new_level, discord.Color.gold()),
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
        if message.guild.icon:
            embed.set_footer(text=message.guild.name, icon_url=message.guild.icon.url)
        else:
            embed.set_footer(text=message.guild.name)

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

    @app_commands.command(name="rank", description="Show your (or someone else's) level and XP")
    @app_commands.describe(member="Whose rank to check (defaults to you)")
    async def rank(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        is_self = target.id == interaction.user.id

        xp, level = db.get_user_xp(interaction.guild_id, target.id)
        position = db.get_rank(interaction.guild_id, target.id)

        current_floor = xp_for_level(level)
        next_floor = xp_for_level(level + 1)
        progress = xp - current_floor
        needed = next_floor - current_floor
        bar_filled = int((progress / needed) * 10) if needed else 0
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        embed = discord.Embed(title=f"Rank — {target.display_name}", color=discord.Color.blurple())
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Server Rank", value=f"#{position}" if position else "Unranked", inline=True)
        embed.add_field(name="XP Progress", value=f"`{bar}` {progress}/{needed}", inline=False)
        embed.set_footer(text=f"Total XP: {xp}")

        await interaction.response.send_message(embed=embed, ephemeral=is_self)

    @app_commands.command(name="leaderboard", description="Show the server XP leaderboard")
    async def leaderboard(self, interaction: discord.Interaction):
        rows = db.get_leaderboard(interaction.guild_id, limit=10)
        if not rows:
            await interaction.response.send_message("No one has earned XP yet.")
            return

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            prefix = medals.get(i, f"**{i}.**")
            lines.append(f"{prefix} {name} — Level {row['level']} ({row['xp']} XP)")

        embed = discord.Embed(
            title=f"🏆 {interaction.guild.name} Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="setuplevelroles", description="[Admin] Create the Level 5/10/25/50 roles in this server if they don't exist")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def setuplevelroles(self, interaction: discord.Interaction):
        await interaction.response.defer()

        created, existing = [], []
        for level, role_name in LEVEL_ROLES.items():
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if role is not None:
                existing.append(role_name)
                continue
            role = await interaction.guild.create_role(
                name=role_name,
                color=LEVEL_COLORS.get(level, discord.Color.default()),
                permissions=LEVEL_PERMISSIONS.get(level, discord.Permissions.none()),
                hoist=False,
                mentionable=False,
                reason=f"Level role setup requested by {interaction.user}",
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
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="give_xp", description="[Admin] Manually add XP to a member")
    @app_commands.describe(member="Member to give XP to", amount="Amount of XP to add")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def give_xp(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000]):
        xp, level = db.get_user_xp(interaction.guild_id, member.id)
        xp = max(0, xp + amount)
        new_level = level_from_xp(xp)
        db.set_user_xp(interaction.guild_id, member.id, xp, new_level)
        embed = discord.Embed(
            description=f"Gave **{amount} XP** to {member.mention}. Now at **{xp} XP** (level {new_level}).",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

        if new_level > level:
            role_name = LEVEL_ROLES.get(new_level)
            if role_name:
                role = discord.utils.get(interaction.guild.roles, name=role_name)
                me = interaction.guild.me
                if role and me.guild_permissions.manage_roles and role < me.top_role:
                    try:
                        await member.add_roles(role, reason=f"Reached level {new_level}")
                        await interaction.channel.send(f"🔓 {member.mention} unlocked the **{role.name}** role!")
                    except discord.Forbidden:
                        pass

    @app_commands.command(name="remove_xp", description="[Admin] Remove XP from a member")
    @app_commands.describe(member="Member to remove XP from", amount="Amount of XP to remove")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_xp(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000]):
        xp, level = db.get_user_xp(interaction.guild_id, member.id)
        xp = max(0, xp - amount)
        new_level = level_from_xp(xp)
        db.set_user_xp(interaction.guild_id, member.id, xp, new_level)
        embed = discord.Embed(
            description=f"Removed **{amount} XP** from {member.mention}. Now at **{xp} XP** (level {new_level}).",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="resetxp", description="[Admin] Reset XP/levels for the ENTIRE server")
    @app_commands.checks.has_permissions(administrator=True)
    async def resetxp(self, interaction: discord.Interaction):
        view = ConfirmResetView(interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(
            "⚠️ This will wipe **all** XP and levels for **every member** in this server. This can't be undone.",
            view=view,
            ephemeral=True,
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))

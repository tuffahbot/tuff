import discord
from discord import app_commands
from discord.ext import commands

import database as db
from permissions import SUPER_USER_ID


class SuperAdmin(commands.Cog):
    """Manages the global superadmin list. Deliberately gated to exactly
    ONE Discord user ID (SUPER_USER_ID) -- not "anyone with X permission",
    not even the Owner/Co-Owner roles -- since this command hands out
    Owner-tier moderation standing (see moderation.py's get_tier()) to
    whoever it's pointed at, everywhere the bot is."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    superadmin = app_commands.Group(name="superadmin", description="Manage the global superadmin list")

    @superadmin.command(name="add", description="[Owner only] Give someone Owner-tier moderation access everywhere")
    @app_commands.describe(user="Who to add")
    async def superadmin_add(self, interaction: discord.Interaction, user: discord.User):
        if interaction.user.id != SUPER_USER_ID:
            await interaction.response.send_message("You can't use this command.", ephemeral=True)
            return
        added = db.add_superadmin(user.id, interaction.user.id)
        await interaction.response.send_message(
            f"✅ {user.mention} is now a superadmin." if added else f"{user.mention} is already a superadmin.",
            ephemeral=True,
        )

    @superadmin.command(name="remove", description="[Owner only] Remove someone from the superadmin list")
    @app_commands.describe(user="Who to remove")
    async def superadmin_remove(self, interaction: discord.Interaction, user: discord.User):
        if interaction.user.id != SUPER_USER_ID:
            await interaction.response.send_message("You can't use this command.", ephemeral=True)
            return
        removed = db.remove_superadmin(user.id)
        await interaction.response.send_message(
            f"🧹 Removed {user.mention} from the superadmin list." if removed else f"{user.mention} wasn't on the list.",
            ephemeral=True,
        )

    @superadmin.command(name="list", description="[Owner only] Show everyone on the superadmin list")
    async def superadmin_list(self, interaction: discord.Interaction):
        if interaction.user.id != SUPER_USER_ID:
            await interaction.response.send_message("You can't use this command.", ephemeral=True)
            return
        ids = db.get_superadmins()
        if not ids:
            await interaction.response.send_message("Nobody's on the superadmin list.", ephemeral=True)
            return
        await interaction.response.send_message("Superadmins: " + ", ".join(f"<@{uid}>" for uid in ids), ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    # ---------------- Prefix commands ----------------

    @commands.group(name="superadmin", invoke_without_command=True)
    async def superadmin_text(self, ctx: commands.Context):
        if ctx.author.id != SUPER_USER_ID:
            return  # stay quiet, same as say_text/sync_text
        await ctx.reply(
            f"Usage: `{ctx.prefix}superadmin add <user>`, `{ctx.prefix}superadmin remove <user>`, `{ctx.prefix}superadmin list`",
            mention_author=False,
        )

    @superadmin_text.command(name="add")
    async def superadmin_text_add(self, ctx: commands.Context, user: discord.User):
        if ctx.author.id != SUPER_USER_ID:
            return
        added = db.add_superadmin(user.id, ctx.author.id)
        await ctx.reply(
            f"✅ {user.mention} is now a superadmin." if added else f"{user.mention} is already a superadmin.",
            mention_author=False,
        )

    @superadmin_text.command(name="remove")
    async def superadmin_text_remove(self, ctx: commands.Context, user: discord.User):
        if ctx.author.id != SUPER_USER_ID:
            return
        removed = db.remove_superadmin(user.id)
        await ctx.reply(
            f"🧹 Removed {user.mention} from the superadmin list." if removed else f"{user.mention} wasn't on the list.",
            mention_author=False,
        )

    @superadmin_text.command(name="list")
    async def superadmin_text_list(self, ctx: commands.Context):
        if ctx.author.id != SUPER_USER_ID:
            return
        ids = db.get_superadmins()
        if not ids:
            await ctx.reply("Nobody's on the superadmin list.", mention_author=False)
            return
        await ctx.reply("Superadmins: " + ", ".join(f"<@{uid}>" for uid in ids), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(SuperAdmin(bot))

import discord
from discord import app_commands
from discord.ext import commands

import database as db


class AutoRole(commands.Cog):
    """Automatically assigns a role to every member when they join.

    Available as both a slash command group (/autorole set/remove/view)
    and a prefix command group (?autorole set/remove/view), sharing the
    same underlying logic below.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------- Shared logic ----------------

    def _check_assignable(self, guild: discord.Guild, role: discord.Role) -> str | None:
        """Returns an error message if `role` can't be used as the autorole, else None."""
        me = guild.me
        if role.is_default():
            return "You can't use @everyone as the autorole."
        if not me.guild_permissions.manage_roles:
            return "I need the Manage Roles permission first."
        if role >= me.top_role:
            return (
                f"I can't assign **{role.name}** because it's above (or equal to) my own top role. "
                "Move my role above it in Server Settings → Roles first."
            )
        return None

    def _do_set(self, guild_id: int, role: discord.Role):
        db.set_autorole(guild_id, role.id)

    def _do_remove(self, guild_id: int) -> bool:
        """Returns False if there was nothing set to remove."""
        if db.get_autorole(guild_id) is None:
            return False
        db.clear_autorole(guild_id)
        return True

    def _do_view(self, guild: discord.Guild) -> str:
        role_id = db.get_autorole(guild.id)
        if role_id is None:
            return "No autorole is set up for this server."
        role = guild.get_role(role_id)
        if role is None:
            return "An autorole is set, but the role no longer exists -- set a new one or remove it."
        return f"New members currently get {role.mention} automatically."

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        role_id = db.get_autorole(member.guild.id)
        if role_id is None:
            return
        role = member.guild.get_role(role_id)
        if role is None:
            return
        me = member.guild.me
        if not me.guild_permissions.manage_roles or role >= me.top_role:
            return
        try:
            await member.add_roles(role, reason="Autorole on join")
        except discord.Forbidden:
            pass

    # ---------------- Slash commands (/autorole ...) ----------------

    autorole = app_commands.Group(
        name="autorole",
        description="Manage the role new members get automatically",
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @autorole.command(name="set", description="[Admin] Set the role given to new members automatically")
    @app_commands.describe(role="The role to auto-assign")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def autorole_set(self, interaction: discord.Interaction, role: discord.Role):
        problem = self._check_assignable(interaction.guild, role)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        self._do_set(interaction.guild_id, role)
        embed = discord.Embed(
            title="✅ Autorole Set",
            description=f"New members will automatically get {role.mention}.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @autorole.command(name="remove", description="[Admin] Turn off autorole")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def autorole_remove(self, interaction: discord.Interaction):
        if not self._do_remove(interaction.guild_id):
            await interaction.response.send_message("Autorole isn't set up.", ephemeral=True)
            return
        await interaction.response.send_message("🧹 Autorole turned off. New members won't get a role automatically anymore.")

    @autorole.command(name="view", description="Show the current autorole")
    async def autorole_view(self, interaction: discord.Interaction):
        await interaction.response.send_message(self._do_view(interaction.guild), ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Roles permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    # ---------------- Prefix commands (?autorole ...) ----------------

    @commands.group(name="autorole", invoke_without_command=True)
    async def autorole_text(self, ctx: commands.Context):
        await ctx.reply(
            f"Usage: `{ctx.prefix}autorole set <role>`, `{ctx.prefix}autorole remove`, `{ctx.prefix}autorole view`",
            mention_author=False,
        )

    @autorole_text.command(name="set")
    @commands.has_permissions(manage_roles=True)
    async def autorole_text_set(self, ctx: commands.Context, *, role: discord.Role):
        problem = self._check_assignable(ctx.guild, role)
        if problem:
            await ctx.reply(problem, mention_author=False)
            return
        self._do_set(ctx.guild.id, role)
        embed = discord.Embed(
            title="✅ Autorole Set",
            description=f"New members will automatically get {role.mention}.",
            color=discord.Color.green(),
        )
        await ctx.reply(embed=embed, mention_author=False)

    @autorole_text.command(name="remove")
    @commands.has_permissions(manage_roles=True)
    async def autorole_text_remove(self, ctx: commands.Context):
        if not self._do_remove(ctx.guild.id):
            await ctx.reply("Autorole isn't set up.", mention_author=False)
            return
        await ctx.reply("🧹 Autorole turned off. New members won't get a role automatically anymore.", mention_author=False)

    @autorole_text.command(name="view")
    async def autorole_text_view(self, ctx: commands.Context):
        await ctx.reply(self._do_view(ctx.guild), mention_author=False)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if ctx.command not in (self.autorole_text, self.autorole_text_set, self.autorole_text_remove, self.autorole_text_view):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Roles permission to do that.", mention_author=False)
        elif isinstance(error, commands.RoleNotFound):
            await ctx.reply("Couldn't find that role -- try the exact name or mention it.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Usage: `{ctx.prefix}autorole set <role>`", mention_author=False)
        else:
            original = getattr(error, "original", error)
            if isinstance(original, discord.Forbidden):
                await ctx.reply("Discord won't let me do that -- check my role position and permissions.", mention_author=False)
            else:
                print(f"Autorole prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoRole(bot))

import discord
from discord import app_commands
from discord.ext import commands

from logsutil import send_log
from permissions import SUPER_USER_ID


class Roles(commands.Cog):
    """Manual role management -- /rolegive and /roleremove.

    Different from autorole.py, which handles the role auto-assigned on
    member join. This is for one-off "give this person this role" actions.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _check(self, guild: discord.Guild, invoker: discord.Member, role: discord.Role) -> str | None:
        """Returns an error message if the role can't be touched here, else None."""
        me = guild.me
        if role.is_default():
            return "You can't use @everyone here."
        if not me.guild_permissions.manage_roles:
            return "I need the Manage Roles permission first."
        if role >= me.top_role:
            return (
                f"I can't manage **{role.name}** because it's above (or equal to) my own top role. "
                "Move my role above it in Server Settings → Roles first."
            )
        if invoker.id != SUPER_USER_ID and not invoker.guild_permissions.administrator and role >= invoker.top_role:
            return f"You can't assign **{role.name}** since it's above (or equal to) your own top role."
        return None

    async def _give(self, guild: discord.Guild, invoker: discord.Member, member: discord.Member, role: discord.Role) -> tuple[str | None, str | None]:
        """Returns (success_message, error_message) -- exactly one is set."""
        problem = self._check(guild, invoker, role)
        if problem:
            return None, problem
        if role in member.roles:
            return None, f"{member.mention} already has {role.mention}."
        await member.add_roles(role, reason=f"Given by {invoker}")
        await self._log(invoker, member, role, "Given")
        return f"✅ Gave {role.mention} to {member.mention}.", None

    async def _remove(self, guild: discord.Guild, invoker: discord.Member, member: discord.Member, role: discord.Role) -> tuple[str | None, str | None]:
        problem = self._check(guild, invoker, role)
        if problem:
            return None, problem
        if role not in member.roles:
            return None, f"{member.mention} doesn't have {role.mention}."
        await member.remove_roles(role, reason=f"Removed by {invoker}")
        await self._log(invoker, member, role, "Removed")
        return f"🧹 Removed {role.mention} from {member.mention}.", None

    async def _log(self, invoker: discord.Member, member: discord.Member, role: discord.Role, verb: str):
        embed = discord.Embed(
            title=f"⚙️ Role {verb}",
            description=f"{invoker.mention} {verb.lower()} {role.mention} {'to' if verb == 'Given' else 'from'} {member.mention}.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"By {invoker} ({invoker.id})")
        await send_log(self.bot, embed)

    # ---------------- Slash commands ----------------

    @app_commands.command(name="rolegive", description="[Admin] Give a member a role")
    @app_commands.describe(member="Who", role="Which role")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def rolegive(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        result, error = await self._give(interaction.guild, interaction.user, member, role)
        await interaction.response.send_message(error or result, ephemeral=bool(error))

    @app_commands.command(name="roleremove", description="[Admin] Remove a role from a member")
    @app_commands.describe(member="Who", role="Which role")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def roleremove(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        result, error = await self._remove(interaction.guild, interaction.user, member, role)
        await interaction.response.send_message(error or result, ephemeral=bool(error))

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Roles permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    # ---------------- Prefix commands ----------------

    @commands.command(name="rolegive")
    @commands.has_permissions(manage_roles=True)
    async def rolegive_text(self, ctx: commands.Context, member: discord.Member, *, role: discord.Role):
        result, error = await self._give(ctx.guild, ctx.author, member, role)
        await ctx.reply(error or result, mention_author=False)

    @commands.command(name="roleremove")
    @commands.has_permissions(manage_roles=True)
    async def roleremove_text(self, ctx: commands.Context, member: discord.Member, *, role: discord.Role):
        result, error = await self._remove(ctx.guild, ctx.author, member, role)
        await ctx.reply(error or result, mention_author=False)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Roles permission to do that.", mention_author=False)
        elif isinstance(error, commands.MemberNotFound):
            await ctx.reply("Couldn't find that member.", mention_author=False)
        elif isinstance(error, commands.RoleNotFound):
            await ctx.reply("Couldn't find that role -- try the exact name or mention it.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Usage: `{ctx.prefix}rolegive <member> <role>` or `{ctx.prefix}roleremove <member> <role>`", mention_author=False)
        else:
            print(f"Roles prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))

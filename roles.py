import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from permissions import SUPER_USER_ID

ROLEALL_MAX_CONCURRENT = 5  # keep Discord's role-edit rate limit happy


class Roles(commands.Cog):
    """Manual role management -- /rolegive, /roleremove, /roleall, /rolecreate.

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
        return f"✅ Gave {role.mention} to {member.mention}.", None

    async def _remove(self, guild: discord.Guild, invoker: discord.Member, member: discord.Member, role: discord.Role) -> tuple[str | None, str | None]:
        problem = self._check(guild, invoker, role)
        if problem:
            return None, problem
        if role not in member.roles:
            return None, f"{member.mention} doesn't have {role.mention}."
        await member.remove_roles(role, reason=f"Removed by {invoker}")
        return f"🧹 Removed {role.mention} from {member.mention}.", None

    async def _give_all(self, guild: discord.Guild, invoker: discord.Member, role: discord.Role, include_bots: bool) -> tuple[int, int, int, str | None]:
        """Returns (given_count, already_had_count, failed_count, error)."""
        problem = self._check(guild, invoker, role)
        if problem:
            return 0, 0, 0, problem

        eligible = [m for m in guild.members if include_bots or not m.bot]
        already_had = sum(1 for m in eligible if role in m.roles)
        targets = [m for m in eligible if role not in m.roles]

        sem = asyncio.Semaphore(ROLEALL_MAX_CONCURRENT)
        given = 0
        failed = 0

        async def give_one(member: discord.Member):
            nonlocal given, failed
            async with sem:
                try:
                    await member.add_roles(role, reason=f"/roleall by {invoker}")
                    given += 1
                except discord.HTTPException:
                    failed += 1

        await asyncio.gather(*(give_one(m) for m in targets))
        return given, already_had, failed, None

    def _give_all_summary(self, role: discord.Role, given: int, already_had: int, failed: int) -> str:
        summary = f"✅ Gave {role.mention} to **{given}** member(s). {already_had} already had it."
        if failed:
            summary += f" **{failed}** failed (likely a permissions/hierarchy issue on that member specifically)."
        return summary

    def _parse_color(self, color_str: str) -> tuple[discord.Color | None, str | None]:
        """Returns (color, warning) -- color is None if it couldn't be parsed."""
        try:
            return discord.Color.from_str(color_str if color_str.startswith("#") else f"#{color_str}"), None
        except ValueError:
            return None, f"Couldn't parse `{color_str}` as a color -- try a hex code like `#ff0000`."

    async def _create_role(
        self, guild: discord.Guild, invoker: discord.Member, name: str, color_str: str | None, hoist: bool, mentionable: bool
    ) -> tuple[discord.Role | None, str | None, str | None]:
        """Returns (role, warning, error) -- role is None only if error is set."""
        me = guild.me
        if not me.guild_permissions.manage_roles:
            return None, None, "I need the Manage Roles permission to create roles."
        if len(guild.roles) >= 250:
            return None, None, "This server already has Discord's max of 250 roles -- delete one first."

        color = discord.Color.default()
        warning = None
        if color_str:
            parsed, warning = self._parse_color(color_str)
            if parsed is not None:
                color = parsed
            else:
                warning += " Created with no color instead."

        try:
            role = await guild.create_role(name=name, colour=color, hoist=hoist, mentionable=mentionable, reason=f"Created by {invoker}")
        except discord.Forbidden:
            return None, None, "Discord refused to create that role -- check my Manage Roles permission."
        except discord.HTTPException as e:
            return None, None, f"Something went wrong creating the role: {e}"
        return role, warning, None

    async def _edit_role(
        self, guild: discord.Guild, invoker: discord.Member, role: discord.Role,
        name: str | None, color_str: str | None, hoist: bool | None, mentionable: bool | None,
    ) -> tuple[discord.Role | None, str | None, str | None]:
        """Returns (role, warning, error) -- only the given fields are changed, everything else is left as-is."""
        problem = self._check(guild, invoker, role)
        if problem:
            return None, None, problem

        changes = {}
        warning = None
        if name:
            changes["name"] = name
        if color_str:
            color, warning = self._parse_color(color_str)
            if color is not None:
                changes["colour"] = color
        if hoist is not None:
            changes["hoist"] = hoist
        if mentionable is not None:
            changes["mentionable"] = mentionable

        if not changes:
            return None, None, "Give at least one thing to change: name, color, hoist, or mentionable."

        try:
            await role.edit(reason=f"Edited by {invoker}", **changes)
        except discord.Forbidden:
            return None, None, "Discord refused to edit that role -- check my Manage Roles permission."
        except discord.HTTPException as e:
            return None, None, f"Something went wrong editing the role: {e}"
        return role, warning, None

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

    @app_commands.command(name="roleall", description="[Admin] Give a role to every member in the server")
    @app_commands.describe(role="Which role to give everyone", include_bots="Also give it to bot accounts (default: no)")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def roleall(self, interaction: discord.Interaction, role: discord.Role, include_bots: bool = False):
        await interaction.response.defer(ephemeral=True, thinking=True)
        given, already_had, failed, error = await self._give_all(interaction.guild, interaction.user, role, include_bots)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(self._give_all_summary(role, given, already_had, failed), ephemeral=True)

    @app_commands.command(name="rolecreate", description="[Admin] Create a new role")
    @app_commands.describe(
        name="The role's name",
        color="Hex color code, e.g. #ff0000 (optional)",
        hoist="Show this role separately in the member list (default: no)",
        mentionable="Let anyone @mention this role (default: no)",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def rolecreate(self, interaction: discord.Interaction, name: str, color: str = None, hoist: bool = False, mentionable: bool = False):
        role, warning, error = await self._create_role(interaction.guild, interaction.user, name, color, hoist, mentionable)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        msg = f"✅ Created {role.mention}."
        if warning:
            msg += f"\n⚠️ {warning}"
        await interaction.response.send_message(msg)

    @app_commands.command(name="roleedit", description="[Admin] Edit an existing role's name, color, hoist, or mentionable")
    @app_commands.describe(
        role="Which role to edit",
        name="New name (leave blank to keep it)",
        color="New hex color code, e.g. #ff0000 (leave blank to keep it)",
        hoist="Show this role separately in the member list (leave blank to keep it)",
        mentionable="Let anyone @mention this role (leave blank to keep it)",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def roleedit(self, interaction: discord.Interaction, role: discord.Role, name: str = None, color: str = None, hoist: bool = None, mentionable: bool = None):
        result, warning, error = await self._edit_role(interaction.guild, interaction.user, role, name, color, hoist, mentionable)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        msg = f"✅ Updated {result.mention}."
        if warning:
            msg += f"\n⚠️ {warning}"
        await interaction.response.send_message(msg)

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

    @commands.command(name="roleall")
    @commands.has_permissions(manage_roles=True)
    async def roleall_text(self, ctx: commands.Context, role: discord.Role, include_bots: bool = False):
        status = await ctx.reply(f"⏳ Giving {role.mention} to everyone -- this can take a bit for a big server.", mention_author=False)
        given, already_had, failed, error = await self._give_all(ctx.guild, ctx.author, role, include_bots)
        if error:
            await status.edit(content=error)
            return
        await status.edit(content=self._give_all_summary(role, given, already_had, failed))

    @commands.command(name="rolecreate")
    @commands.has_permissions(manage_roles=True)
    async def rolecreate_text(self, ctx: commands.Context, *, name: str):
        role, warning, error = await self._create_role(ctx.guild, ctx.author, name, None, False, False)
        if error:
            await ctx.reply(error, mention_author=False)
            return
        msg = f"✅ Created {role.mention}."
        if warning:
            msg += f"\n⚠️ {warning}"
        await ctx.reply(msg, mention_author=False)

    @commands.command(name="roleedit")
    @commands.has_permissions(manage_roles=True)
    async def roleedit_text(self, ctx: commands.Context, role: discord.Role, field: str, *, value: str):
        field = field.lower()
        name = color = None
        hoist = mentionable = None
        if field == "name":
            name = value
        elif field == "color":
            color = value
        elif field == "hoist":
            hoist = value.lower() in ("true", "yes", "on", "1")
        elif field == "mentionable":
            mentionable = value.lower() in ("true", "yes", "on", "1")
        else:
            await ctx.reply(f"Usage: `{ctx.prefix}roleedit <role> <name|color|hoist|mentionable> <value>`", mention_author=False)
            return

        result, warning, error = await self._edit_role(ctx.guild, ctx.author, role, name, color, hoist, mentionable)
        if error:
            await ctx.reply(error, mention_author=False)
            return
        msg = f"✅ Updated {result.mention}."
        if warning:
            msg += f"\n⚠️ {warning}"
        await ctx.reply(msg, mention_author=False)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Roles permission to do that.", mention_author=False)
        elif isinstance(error, commands.MemberNotFound):
            await ctx.reply("Couldn't find that member.", mention_author=False)
        elif isinstance(error, commands.RoleNotFound):
            await ctx.reply("Couldn't find that role -- try the exact name or mention it.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                f"Usage: `{ctx.prefix}rolegive <member> <role>`, `{ctx.prefix}roleremove <member> <role>`, "
                f"`{ctx.prefix}roleall <role>`, `{ctx.prefix}rolecreate <name>`, or "
                f"`{ctx.prefix}roleedit <role> <name|color|hoist|mentionable> <value>`",
                mention_author=False,
            )
        else:
            print(f"Roles prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))

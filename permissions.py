"""
Shared "super user" bypass. SUPER_USER_ID (the same person as
AUTHORIZED_SAY_USER_ID in general.py) can use every command in the bot,
regardless of what Discord permissions or staff roles they hold in a given
server.

install_permission_bypass() patches the two decorators nearly every cog's
admin/mod commands are built with -- commands.has_permissions and
app_commands.checks.has_permissions -- so this works automatically for any
command that uses them, without having to edit every cog individually.

A handful of cogs gate access with their own custom logic instead of those
decorators (moderation.py's role-tier system, modapps.py's button handlers,
polls.py's "creator or mod" check, roles.py's role-hierarchy guard) --
those import SUPER_USER_ID directly and check it inline, since a decorator
patch can't reach hand-rolled logic like that.
"""
import discord
from discord import app_commands
from discord.ext import commands

SUPER_USER_ID = 1503282641221320815


def install_permission_bypass():
    """Must run BEFORE any cog is loaded. @commands.has_permissions(...) and
    @app_commands.checks.has_permissions(...) call these factory functions
    once, at import time (when the decorator line itself executes) -- so
    patching them after a cog's module has already been imported would be
    too late to affect that cog's commands.

    These reimplement the same permission-checking logic the real
    decorators use (rather than wrapping/unwrapping the originals) --
    app_commands.checks.has_permissions in particular doesn't expose its
    inner predicate the way commands.has_permissions does, so trying to
    delegate to "the original, but bypassed for one user" isn't reliable
    across versions. This is simple enough to just reimplement directly."""

    def patched_prefix_has_permissions(**perms):
        invalid = set(perms) - set(discord.Permissions.VALID_FLAGS)
        if invalid:
            raise TypeError(f"Invalid permission(s): {', '.join(invalid)}")

        def predicate(ctx: commands.Context) -> bool:
            if ctx.author.id == SUPER_USER_ID:
                return True
            permissions = ctx.permissions
            missing = [perm for perm, value in perms.items() if getattr(permissions, perm, None) != value]
            if not missing:
                return True
            raise commands.MissingPermissions(missing)

        return commands.check(predicate)

    def patched_app_has_permissions(**perms):
        invalid = set(perms) - set(discord.Permissions.VALID_FLAGS)
        if invalid:
            raise TypeError(f"Invalid permission(s): {', '.join(invalid)}")

        async def predicate(interaction: discord.Interaction) -> bool:
            if interaction.user.id == SUPER_USER_ID:
                return True
            permissions = interaction.permissions
            missing = [perm for perm, value in perms.items() if getattr(permissions, perm, None) != value]
            if not missing:
                return True
            raise app_commands.MissingPermissions(missing)

        return app_commands.check(predicate)

    commands.has_permissions = patched_prefix_has_permissions
    app_commands.checks.has_permissions = patched_app_has_permissions

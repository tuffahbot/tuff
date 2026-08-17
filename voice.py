import discord
from discord import app_commands
from discord.ext import commands

import database as db

# Joining this channel creates a fresh temp voice channel for that member and
# moves them into it. The temp channel is auto-deleted once everyone leaves.
TRIGGER_CHANNEL_ID = 1536207314074472528


def find_owner_id(channel: discord.VoiceChannel) -> int | None:
    """Best-effort owner lookup from the channel's own permission overwrites
    (the owner is the member we granted manage_channels to when it was
    created). Used as a fallback for when the in-memory owner map has been
    lost, e.g. after a bot restart."""
    for target, overwrite in channel.overwrites.items():
        if isinstance(target, discord.Member) and overwrite.manage_channels:
            return target.id
    return None


class RenameModal(discord.ui.Modal, title="Rename Voice Channel"):
    name = discord.ui.TextInput(label="New channel name", max_length=100)

    def __init__(self, channel: discord.VoiceChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await self.channel.edit(name=str(self.name), reason=f"Renamed by {interaction.user}")
        await interaction.response.send_message(f"✏️ Renamed to **{self.name}**.", ephemeral=True)


class LimitModal(discord.ui.Modal, title="Set User Limit"):
    limit = discord.ui.TextInput(label="User limit (0 = unlimited, max 99)", max_length=2)

    def __init__(self, channel: discord.VoiceChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(str(self.limit))
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if not 0 <= value <= 99:
            await interaction.response.send_message("Must be between 0 and 99.", ephemeral=True)
            return
        await self.channel.edit(user_limit=value, reason=f"Limit set by {interaction.user}")
        shown = "unlimited" if value == 0 else str(value)
        await interaction.response.send_message(f"👥 User limit set to **{shown}**.", ephemeral=True)


class MemberActionSelect(discord.ui.UserSelect):
    """One-shot member picker used by Kick/Permit/Block/Transfer -- shown in
    an ephemeral follow-up so it doesn't clutter the main control panel."""
    def __init__(self, channel: discord.VoiceChannel, action: str):
        placeholder = {
            "kick": "Select a member to disconnect...",
            "permit": "Select a member to permit...",
            "block": "Select a member to block...",
            "transfer": "Select the new owner...",
        }[action]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        self.channel = channel
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        target = interaction.guild.get_member(self.values[0].id)
        if target is None:
            await interaction.response.send_message("Couldn't find that member in the server.", ephemeral=True)
            return

        if self.action == "kick":
            if target.voice is None or target.voice.channel != self.channel:
                await interaction.response.send_message(f"{target.mention} isn't in this channel.", ephemeral=True)
                return
            await target.move_to(None, reason=f"Kicked from VC by {interaction.user}")
            await interaction.response.send_message(f"👢 Disconnected {target.mention}.", ephemeral=True)

        elif self.action == "permit":
            await self.channel.set_permissions(target, connect=True, reason=f"Permitted by {interaction.user}")
            await interaction.response.send_message(f"✅ {target.mention} can now join even while locked.", ephemeral=True)

        elif self.action == "block":
            await self.channel.set_permissions(target, connect=False, reason=f"Blocked by {interaction.user}")
            if target.voice and target.voice.channel == self.channel:
                await target.move_to(None, reason="Blocked from channel")
            await interaction.response.send_message(f"⛔ Blocked {target.mention} from this channel.", ephemeral=True)

        elif self.action == "transfer":
            if target.id == interaction.user.id:
                await interaction.response.send_message("You already own this channel.", ephemeral=True)
                return
            cog = interaction.client.get_cog("JoinToCreate")
            old_owner_id = cog.temp_channels.get(self.channel.id) if cog else find_owner_id(self.channel)
            if old_owner_id and old_owner_id != target.id:
                old_owner = interaction.guild.get_member(old_owner_id)
                if old_owner:
                    await self.channel.set_permissions(old_owner, overwrite=None, reason="Ownership transferred away")
            await self.channel.set_permissions(target, manage_channels=True, move_members=True, reason=f"Ownership transferred by {interaction.user}")
            if cog:
                cog.temp_channels[self.channel.id] = target.id
            await interaction.response.send_message(f"👑 Transferred ownership to {target.mention}.", ephemeral=True)


class MemberActionView(discord.ui.View):
    def __init__(self, channel: discord.VoiceChannel, action: str):
        super().__init__(timeout=60)
        self.add_item(MemberActionSelect(channel, action))


class VoiceControlView(discord.ui.View):
    """Persistent control panel posted in each temp channel."""
    def __init__(self):
        super().__init__(timeout=None)

    def _get_channel(self, interaction: discord.Interaction) -> discord.VoiceChannel | None:
        channel = interaction.channel
        return channel if isinstance(channel, discord.VoiceChannel) else None

    async def _require_owner(self, interaction: discord.Interaction, channel: discord.VoiceChannel) -> bool:
        cog = interaction.client.get_cog("JoinToCreate")
        owner_id = cog.temp_channels.get(channel.id) if cog else None
        if owner_id is None:
            owner_id = find_owner_id(channel)
        if owner_id != interaction.user.id:
            await interaction.response.send_message("Only this channel's owner can use that.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Rename", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="voice_rename", row=0)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = self._get_channel(interaction)
        if not channel or not await self._require_owner(interaction, channel):
            return
        await interaction.response.send_modal(RenameModal(channel))

    @discord.ui.button(label="Limit", emoji="👥", style=discord.ButtonStyle.secondary, custom_id="voice_limit", row=0)
    async def limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = self._get_channel(interaction)
        if not channel or not await self._require_owner(interaction, channel):
            return
        await interaction.response.send_modal(LimitModal(channel))

    @discord.ui.button(label="Lock", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="voice_lock", row=0)
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = self._get_channel(interaction)
        if not channel or not await self._require_owner(interaction, channel):
            return
        everyone = channel.guild.default_role
        await channel.set_permissions(everyone, connect=False, reason=f"Locked by {interaction.user}")

        mod_role_id = db.get_mod_role(channel.guild.id)
        mod_role = channel.guild.get_role(mod_role_id) if mod_role_id else None
        if mod_role:
            await channel.set_permissions(mod_role, connect=True, reason=f"Staff bypass -- locked by {interaction.user}")

        await interaction.response.send_message("🔒 Locked." + (f" ({mod_role.name} can still join.)" if mod_role else ""), ephemeral=True)

    @discord.ui.button(label="Unlock", emoji="🔓", style=discord.ButtonStyle.success, custom_id="voice_unlock", row=0)
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = self._get_channel(interaction)
        if not channel or not await self._require_owner(interaction, channel):
            return
        everyone = channel.guild.default_role
        await channel.set_permissions(everyone, connect=None, reason=f"Unlocked by {interaction.user}")

        mod_role_id = db.get_mod_role(channel.guild.id)
        mod_role = channel.guild.get_role(mod_role_id) if mod_role_id else None
        if mod_role:
            await channel.set_permissions(mod_role, overwrite=None, reason=f"Unlocked by {interaction.user}")

        await interaction.response.send_message("🔓 Unlocked.", ephemeral=True)

    @discord.ui.button(label="Kick", emoji="👢", style=discord.ButtonStyle.secondary, custom_id="voice_kick", row=1)
    async def kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = self._get_channel(interaction)
        if not channel or not await self._require_owner(interaction, channel):
            return
        await interaction.response.send_message(view=MemberActionView(channel, "kick"), ephemeral=True)

    @discord.ui.button(label="Permit", emoji="✅", style=discord.ButtonStyle.success, custom_id="voice_permit", row=1)
    async def permit(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = self._get_channel(interaction)
        if not channel or not await self._require_owner(interaction, channel):
            return
        await interaction.response.send_message(view=MemberActionView(channel, "permit"), ephemeral=True)

    @discord.ui.button(label="Block", emoji="⛔", style=discord.ButtonStyle.danger, custom_id="voice_block", row=1)
    async def block(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = self._get_channel(interaction)
        if not channel or not await self._require_owner(interaction, channel):
            return
        await interaction.response.send_message(view=MemberActionView(channel, "block"), ephemeral=True)

    @discord.ui.button(label="Transfer", emoji="👑", style=discord.ButtonStyle.secondary, custom_id="voice_transfer", row=1)
    async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = self._get_channel(interaction)
        if not channel or not await self._require_owner(interaction, channel):
            return
        await interaction.response.send_message(view=MemberActionView(channel, "transfer"), ephemeral=True)

    @discord.ui.button(label="Claim", emoji="🛡️", style=discord.ButtonStyle.primary, custom_id="voice_claim", row=2)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = self._get_channel(interaction)
        if not channel:
            return
        cog = interaction.client.get_cog("JoinToCreate")
        owner_id = cog.temp_channels.get(channel.id) if cog else None
        if owner_id is None:
            owner_id = find_owner_id(channel)

        owner_present = owner_id is not None and any(m.id == owner_id for m in channel.members)
        if owner_present:
            await interaction.response.send_message("The owner is still in this channel.", ephemeral=True)
            return

        if owner_id:
            old_owner = channel.guild.get_member(owner_id)
            if old_owner:
                await channel.set_permissions(old_owner, overwrite=None, reason="Ownership claimed")
        await channel.set_permissions(interaction.user, manage_channels=True, move_members=True, reason=f"Claimed by {interaction.user}")
        if cog:
            cog.temp_channels[channel.id] = interaction.user.id
        await interaction.response.send_message("🛡️ You're now the owner of this channel.", ephemeral=True)


def control_panel_text(owner: discord.Member) -> str:
    return (
        f"**Voice controls** · owner 🎙️ {owner.mention}\n"
        "*Rename · Limit · Lock/Unlock · Kick (disconnect) · Permit (allow into a locked channel) · "
        "Block (ban from the channel) · Transfer · Claim (if owner is gone)*"
    )


class JoinToCreate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.temp_channels: dict[int, int] = {}  # temp_channel_id -> owner_id
        # NOTE: this map lives in memory only, so if the bot restarts mid-way,
        # any temp channels that existed before the restart won't be tracked
        # for auto-cleanup anymore. Ownership itself is recoverable though --
        # see find_owner_id(), which reads it back from the channel's own
        # permission overwrites.
        self.bot.add_view(VoiceControlView())

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # Joined the trigger channel -> create a personal channel and move them in
        if after.channel and after.channel.id == TRIGGER_CHANNEL_ID:
            guild = member.guild
            try:
                new_channel = await guild.create_voice_channel(
                    name=f"{member.display_name}'s Channel",
                    category=after.channel.category,
                    reason=f"Join-to-create requested by {member}",
                )
                await new_channel.set_permissions(
                    member, manage_channels=True, move_members=True, reason="Channel owner"
                )
                await member.move_to(new_channel, reason="Join-to-create")
                self.temp_channels[new_channel.id] = member.id
                try:
                    await new_channel.send(content=control_panel_text(member), view=VoiceControlView())
                except discord.Forbidden:
                    pass
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                pass

        # Left a temp channel and it's now empty -> delete it
        if before.channel and before.channel.id in self.temp_channels:
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="Join-to-create channel empty")
                except (discord.NotFound, discord.Forbidden):
                    pass
                finally:
                    self.temp_channels.pop(before.channel.id, None)

    # ---------------- Staff bypass role config ----------------
    # Whoever holds this role can still join a temp VC even after the owner
    # locks it (see VoiceControlView.lock/unlock above).

    modrole = app_commands.Group(name="modrole", description="Set which role can join locked voice channels", default_permissions=discord.Permissions(manage_guild=True))

    @modrole.command(name="set", description="[Admin] Set the role that can bypass locked voice channels")
    @app_commands.describe(role="The staff/mod role")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def modrole_set(self, interaction: discord.Interaction, role: discord.Role):
        db.set_mod_role(interaction.guild_id, role.id)
        await interaction.response.send_message(f"✅ {role.mention} can now join locked voice channels.", ephemeral=True)

    @modrole.command(name="clear", description="[Admin] Remove the locked-VC bypass role")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def modrole_clear(self, interaction: discord.Interaction):
        db.clear_mod_role(interaction.guild_id)
        await interaction.response.send_message("Cleared -- no role bypasses locked voice channels now.", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need the Manage Server permission to do that.", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)

    @commands.group(name="modrole", invoke_without_command=True)
    async def modrole_text(self, ctx: commands.Context):
        await ctx.reply(f"Usage: `{ctx.prefix}modrole set <role>`, `{ctx.prefix}modrole clear`", mention_author=False)

    @modrole_text.command(name="set")
    @commands.has_permissions(manage_guild=True)
    async def modrole_text_set(self, ctx: commands.Context, *, role: discord.Role):
        db.set_mod_role(ctx.guild.id, role.id)
        await ctx.reply(f"✅ {role.mention} can now join locked voice channels.", mention_author=False)

    @modrole_text.command(name="clear")
    @commands.has_permissions(manage_guild=True)
    async def modrole_text_clear(self, ctx: commands.Context):
        db.clear_mod_role(ctx.guild.id)
        await ctx.reply("Cleared -- no role bypasses locked voice channels now.", mention_author=False)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("You need the Manage Server permission to do that.", mention_author=False)
        elif isinstance(error, commands.RoleNotFound):
            await ctx.reply("Couldn't find that role.", mention_author=False)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Usage: `{ctx.prefix}modrole set <role>`", mention_author=False)
        else:
            print(f"Voice prefix command error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(JoinToCreate(bot))

import discord
from discord.ext import commands

# Joining this channel creates a fresh temp voice channel for that member and
# moves them into it. The temp channel is auto-deleted once everyone leaves.
TRIGGER_CHANNEL_ID = 1536207314074472528


class JoinToCreate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.temp_channels: dict[int, int] = {}  # temp_channel_id -> owner_id
        # NOTE: this map lives in memory only, so if the bot restarts mid-way,
        # any temp channels that existed before the restart won't be tracked
        # for auto-cleanup anymore (they just won't get deleted automatically).

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


async def setup(bot: commands.Bot):
    await bot.add_cog(JoinToCreate(bot))

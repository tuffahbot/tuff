import asyncio
import functools
import os
import re

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
except ImportError:
    spotipy = None

YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    # Spoofing mobile app clients sometimes avoids YouTube's "sign in to
    # confirm you're not a bot" check without needing cookies. This is
    # inconsistent -- YouTube tweaks this over time -- so it's a free first
    # line of defense, not a guarantee. Cookies (see below) are the reliable fix.
    "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}},
}

# --- YouTube cookies (optional but usually required on cloud hosts) ---------
# YouTube blocks/flags requests from datacenter IPs (like Railway's) with a
# "Sign in to confirm you're not a bot" error. The reliable fix is passing
# cookies from a real, logged-in YouTube session. Set the RAILWAY VARIABLE
# `YOUTUBE_COOKIES` to the full contents of a cookies.txt file (Netscape
# format) exported from your browser, and this will pick it up automatically.
# See the README for step-by-step export instructions.
_cookies_content = os.getenv("YOUTUBE_COOKIES")
if _cookies_content:
    _cookies_path = "/tmp/youtube_cookies.txt"
    with open(_cookies_path, "w") as _f:
        _f.write(_cookies_content)
    YTDL_OPTS["cookiefile"] = _cookies_path

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

# --- Spotify link support -----------------------------------------------
# Spotify's API never gives out actual audio (it's DRM-protected), so this
# only reads the track names off a Spotify link via Spotify's API, then
# plays the matching audio for each one through YouTube -- same as any
# other Spotify-aware Discord music bot does it. Needs SPOTIFY_CLIENT_ID
# and SPOTIFY_CLIENT_SECRET set (see README); without them, Spotify links
# just get told to the person as unsupported.
SPOTIFY_URL_RE = re.compile(r"open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)")
MAX_SPOTIFY_TRACKS = 50

_spotify_client = None
_spotify_checked = False


def get_spotify_client():
    global _spotify_client, _spotify_checked
    if _spotify_checked:
        return _spotify_client
    _spotify_checked = True
    if spotipy is None:
        return None
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    _spotify_client = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
    )
    return _spotify_client


async def resolve_spotify_queries(url: str, loop: asyncio.AbstractEventLoop) -> list[str] | None:
    """Returns a list of 'track - artist' search strings for a Spotify
    track/album/playlist link, or None if Spotify isn't configured."""
    sp = get_spotify_client()
    if sp is None:
        return None

    match = SPOTIFY_URL_RE.search(url)
    if not match:
        return None
    kind, spotify_id = match.groups()

    def _fetch() -> list[str]:
        if kind == "track":
            t = sp.track(spotify_id)
            return [f"{t['name']} {t['artists'][0]['name']}"]

        if kind == "album":
            items = sp.album_tracks(spotify_id, limit=MAX_SPOTIFY_TRACKS)["items"]
            return [f"{t['name']} {t['artists'][0]['name']}" for t in items]

        # playlist
        items = sp.playlist_items(
            spotify_id, limit=MAX_SPOTIFY_TRACKS, fields="items.track.name,items.track.artists"
        )["items"]
        queries = []
        for item in items:
            track = item.get("track")
            if track:
                queries.append(f"{track['name']} {track['artists'][0]['name']}")
        return queries

    return await loop.run_in_executor(None, _fetch)

ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)


class Song:
    def __init__(self, source_url: str, title: str, webpage_url: str, duration: int, requester: discord.Member, source: str = "youtube", http_headers: dict | None = None):
        self.source_url = source_url
        self.title = title
        self.webpage_url = webpage_url
        self.duration = duration
        self.requester = requester
        self.source = source
        self.http_headers = http_headers or {}

    @staticmethod
    def _is_url(query: str) -> bool:
        return query.startswith("http://") or query.startswith("https://")

    @classmethod
    async def from_query(cls, query: str, requester: discord.Member, loop: asyncio.AbstractEventLoop):
        try:
            data = await loop.run_in_executor(None, functools.partial(ytdl.extract_info, query, download=False))
            source = "youtube"
        except yt_dlp.utils.DownloadError as e:
            # If it's a plain text search (not a pasted link) and YouTube blocked
            # us as a bot, retry the same search on SoundCloud instead -- it
            # isn't subject to this bot-detection issue at all.
            if not cls._is_url(query) and "Sign in to confirm" in str(e):
                data = await loop.run_in_executor(
                    None, functools.partial(ytdl.extract_info, f"scsearch:{query}", download=False)
                )
                source = "soundcloud"
            else:
                raise

        if "entries" in data:
            data = data["entries"][0]
        return cls(
            source_url=data["url"],
            title=data.get("title", "Unknown title"),
            webpage_url=data.get("webpage_url", query),
            duration=data.get("duration", 0),
            requester=requester,
            source=source,
            # The direct media URL yt-dlp extracts is tied to the headers of the
            # player client that fetched it (see player_client spoofing above).
            # ffmpeg needs those SAME headers when it requests the URL itself,
            # or YouTube's CDN will often 403/throttle it -- which fails
            # completely silently from Discord's side (see play_next below).
            http_headers=data.get("http_headers"),
        )

    def format_duration(self) -> str:
        if not self.duration:
            return "Live/Unknown"
        m, s = divmod(int(self.duration), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _ffmpeg_opts_for(song: Song) -> dict:
    opts = dict(FFMPEG_OPTS)
    if song.http_headers:
        # Pass the SAME headers yt-dlp used to obtain this URL, or the CDN
        # will often silently 403/throttle ffmpeg's request for it.
        header_block = "".join(f"{k}: {v}\r\n" for k, v in song.http_headers.items())
        opts["before_options"] = f'-headers "{header_block}" ' + opts["before_options"]
    return opts


class GuildMusicState:
    def __init__(self, bot: commands.Bot, guild: discord.Guild):
        self.bot = bot
        self.guild = guild
        self.queue: list[Song] = []
        self.voice_client: discord.VoiceClient | None = None
        self.current: Song | None = None
        self.volume = 0.5
        self.loop_song = False
        self.text_channel: discord.abc.Messageable | None = None  # set by /play, used to report errors

    def play_next(self, error=None):
        if error:
            print(f"Player error: {error}")
            if self.text_channel:
                # 'after' callbacks run on a background thread, not the event
                # loop, so sending a message needs run_coroutine_threadsafe.
                asyncio.run_coroutine_threadsafe(
                    self.text_channel.send(f"⚠️ Playback stopped unexpectedly: `{error}`. Skipping."),
                    self.bot.loop,
                )

        if self.loop_song and self.current:
            self.queue.insert(0, self.current)

        if not self.queue:
            self.current = None
            return

        self.current = self.queue.pop(0)
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(self.current.source_url, **_ffmpeg_opts_for(self.current)), volume=self.volume
        )
        self.voice_client.play(source, after=self.play_next)


def music_embed(text: str, color=discord.Color.blurple()) -> discord.Embed:
    return discord.Embed(description=text, color=color)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states: dict[int, GuildMusicState] = {}

    def get_state(self, guild: discord.Guild) -> GuildMusicState:
        if guild.id not in self.states:
            self.states[guild.id] = GuildMusicState(self.bot, guild)
        return self.states[guild.id]

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # The bot itself got disconnected/kicked from voice -- clear stale
        # state so the next /play reconnects cleanly instead of erroring.
        if member.id == self.bot.user.id and before.channel and not after.channel:
            state = self.states.get(member.guild.id)
            if state:
                state.voice_client = None
                state.queue.clear()
                state.current = None
            return

        # Auto-leave if everyone else has left the bot's voice channel.
        if before.channel and member.id != self.bot.user.id:
            state = self.states.get(member.guild.id)
            if state and state.voice_client and state.voice_client.channel == before.channel:
                if all(m.bot for m in before.channel.members):
                    await asyncio.sleep(60)
                    if state.voice_client and state.voice_client.channel and all(m.bot for m in state.voice_client.channel.members):
                        await state.voice_client.disconnect()
                        state.voice_client = None
                        state.queue.clear()
                        state.current = None

    async def ensure_voice(self, interaction: discord.Interaction) -> GuildMusicState | None:
        """
        NOTE: this must only be called AFTER interaction.response.defer() has
        already run (currently only /play does this) -- it uses followup.send,
        not response.send_message, since the initial response is already used.
        """
        state = self.get_state(interaction.guild)
        if interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.followup.send("Join a voice channel first.", ephemeral=True)
            return None

        try:
            if state.voice_client is None or not state.voice_client.is_connected():
                state.voice_client = await interaction.user.voice.channel.connect()
            elif state.voice_client.channel != interaction.user.voice.channel:
                await state.voice_client.move_to(interaction.user.voice.channel)
        except discord.ClientException as e:
            await interaction.followup.send(f"Couldn't join your voice channel: `{e}`", ephemeral=True)
            return None
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to join/speak in that voice channel.", ephemeral=True)
            return None
        except asyncio.TimeoutError:
            await interaction.followup.send("Timed out connecting to voice. Try again.", ephemeral=True)
            return None

        return state

    @app_commands.command(name="play", description="Queue up a song — search by name or drop a link")
    @app_commands.describe(query="Name it or paste a link (YouTube, SoundCloud, or Spotify track/album/playlist)")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        state = await self.ensure_voice(interaction)
        if state is None:
            return

        if "open.spotify.com" in query:
            await self._play_spotify(interaction, state, query)
            return

        try:
            song = await Song.from_query(query, interaction.user, self.bot.loop)
        except Exception as e:
            if "Sign in to confirm" in str(e):
                await interaction.followup.send(
                    "YouTube is blocking this server as a bot and I couldn't find it on SoundCloud either. "
                    "Try searching by song name instead of pasting a link, or set up YouTube cookies "
                    "(see the README) for reliable playback."
                )
            else:
                await interaction.followup.send(f"Couldn't find/play that: `{e}`")
            return

        state.text_channel = interaction.channel
        state.queue.append(song)

        source_note = " (via SoundCloud, YouTube blocked the request)" if song.source == "soundcloud" else ""

        if state.voice_client.is_playing() or state.voice_client.is_paused():
            await interaction.followup.send(embed=music_embed(f"➕ Queued **{song.title}** ({song.format_duration()}){source_note}"))
        else:
            state.play_next()
            await interaction.followup.send(embed=music_embed(f"▶️ Now playing **{song.title}** ({song.format_duration()}){source_note}", discord.Color.green()))

    async def _play_spotify(self, interaction: discord.Interaction, state: "GuildMusicState", query: str):
        queries = await resolve_spotify_queries(query, self.bot.loop)
        if queries is None:
            await interaction.followup.send(
                "Spotify links aren't set up on this bot yet -- an admin needs to add "
                "`SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` (see the README). "
                "In the meantime, try searching by song name instead."
            )
            return
        if not queries:
            await interaction.followup.send("Couldn't find any tracks in that Spotify link.")
            return

        state.text_channel = interaction.channel
        started_playing = state.voice_client.is_playing() or state.voice_client.is_paused()
        added = 0

        for search_text in queries:
            try:
                song = await Song.from_query(search_text, interaction.user, self.bot.loop)
            except Exception:
                continue  # skip tracks with no decent YouTube/SoundCloud match
            state.queue.append(song)
            added += 1
            if not started_playing:
                state.play_next()
                started_playing = True

        note = " (matched via YouTube -- actual Spotify audio isn't accessible to bots)"
        if added == 0:
            await interaction.followup.send("Couldn't find playable matches for any track in that Spotify link.")
        elif len(queries) > added:
            await interaction.followup.send(embed=music_embed(f"🎧 Added {added}/{len(queries)} track(s) from Spotify{note}"))
        else:
            await interaction.followup.send(embed=music_embed(f"🎧 Added {added} track(s) from Spotify{note}"))

    @app_commands.command(name="pause", description="Pause whatever's playing")
    async def pause(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild)
        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.pause()
            await interaction.response.send_message(embed=music_embed("⏸️ Paused."))
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @app_commands.command(name="resume", description="Unpause it")
    async def resume(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild)
        if state.voice_client and state.voice_client.is_paused():
            state.voice_client.resume()
            await interaction.response.send_message(embed=music_embed("▶️ Resumed."))
        else:
            await interaction.response.send_message("Nothing is paused.", ephemeral=True)

    @app_commands.command(name="skip", description="Skip to the next one")
    async def skip(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild)
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
            state.voice_client.stop()  # triggers play_next via the 'after' callback
            await interaction.response.send_message(embed=music_embed("⏭️ Skipped."))
        else:
            await interaction.response.send_message("Nothing to skip.", ephemeral=True)

    @app_commands.command(name="stop", description="Kill the music and wipe the queue")
    async def stop(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild)
        state.queue.clear()
        state.loop_song = False
        if state.voice_client:
            state.voice_client.stop()
        await interaction.response.send_message(embed=music_embed("⏹️ Stopped and cleared the queue."))

    @app_commands.command(name="leave", description="Kick the bot out of voice")
    async def leave(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild)
        if state.voice_client:
            await state.voice_client.disconnect()
            state.voice_client = None
            state.queue.clear()
            state.current = None
        await interaction.response.send_message(embed=music_embed("👋 Disconnected."))

    @app_commands.command(name="queue", description="What's queued up")
    async def queue_(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild)
        if not state.current and not state.queue:
            await interaction.response.send_message("The queue is empty.")
            return

        embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.green())
        if state.current:
            embed.add_field(
                name="Now Playing",
                value=f"**{state.current.title}** ({state.current.format_duration()}) — requested by {state.current.requester.mention}",
                inline=False,
            )
        if state.queue:
            lines = [
                f"{i}. {s.title} ({s.format_duration()}) — {s.requester.mention}"
                for i, s in enumerate(state.queue[:10], start=1)
            ]
            embed.add_field(name="Up Next", value="\n".join(lines), inline=False)
            if len(state.queue) > 10:
                embed.set_footer(text=f"...and {len(state.queue) - 10} more")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nowplaying", description="What's playing right now")
    async def nowplaying(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild)
        if not state.current:
            await interaction.response.send_message("Nothing is playing.")
            return
        embed = discord.Embed(title="Now Playing", description=f"[{state.current.title}]({state.current.webpage_url})", color=discord.Color.green())
        embed.add_field(name="Duration", value=state.current.format_duration())
        embed.add_field(name="Requested by", value=state.current.requester.mention)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="volume", description="Adjust the volume, 0-100")
    @app_commands.describe(percent="0-100")
    async def volume(self, interaction: discord.Interaction, percent: app_commands.Range[int, 0, 100]):
        state = self.get_state(interaction.guild)
        state.volume = percent / 100
        if state.voice_client and state.voice_client.source:
            state.voice_client.source.volume = state.volume
        await interaction.response.send_message(embed=music_embed(f"🔊 Volume set to {percent}%."))

    @app_commands.command(name="loop", description="Repeat the current song on/off")
    async def loop(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild)
        state.loop_song = not state.loop_song
        await interaction.response.send_message(embed=music_embed(f"🔁 Looping is now **{'on' if state.loop_song else 'off'}**."))


    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        message = f"Something went wrong: `{error}`"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))

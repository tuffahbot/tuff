# Discord Music + Levels + Moderation Bot

A single bot organized as cogs:

- **music.py** — queue-based music playback (`/play`, `/skip`, `/queue`, etc.)
- **leveling.py** — XP-per-message leveling, DMs you on level-up, role rewards + starter perks at levels 5/10/25/50
- **moderation.py** — `/warn`, `/kick`, `/ban`, `/timeout`, `/purge`, etc. — actions post to the logs channel, not the regular chat
- **voice.py** — join-to-create temporary voice channels
- **eventlogs.py** — deleted/edited message + member join/leave logging, plus `/snipe`
- **giveaways.py** — `/giveaway start/end/reroll`

All commands are slash commands (`/command`). Prefix commands (`!help`) also work for anything not overridden.

## Hardcoded server-specific IDs

Two channel IDs are set directly in the code (not env vars, since they're specific to one server):

| What | File | Constant | Current value |
|---|---|---|---|
| Join-to-create trigger channel | `voice.py` | `TRIGGER_CHANNEL_ID` | `1536207314074472528` |
| Mod-log / event-log channel | `logsutil.py` | `LOGS_CHANNEL_ID` | `1536213945113780285` |

If you ever move servers or want to change either channel, just edit the constant and redeploy. The mod role IDs in `moderation.py` (`MOD_ROLE_IDS`) work the same way.

## 1. Create the bot on Discord

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. Under **Bot**, click **Reset Token** and copy it — this is your `DISCORD_TOKEN`.
3. Still under **Bot**, enable these **Privileged Gateway Intents**:
   - Server Members Intent
   - Message Content Intent
4. Under **OAuth2 → URL Generator**, check scopes `bot` and `applications.commands`, and these bot permissions (or just `Administrator` if this is your own server): Manage Roles, Kick Members, Ban Members, Moderate Members, Manage Messages, Manage Channels, Move Members, Connect, Speak, Send Messages, Embed Links.
5. Open the generated URL and invite the bot to your server.

## 2. Set up level roles (optional but recommended)

Run `/setuplevelroles` (needs Manage Roles) once the bot is in your server — it creates:

```
Level 5
Level 10
Level 25
Level 50
```

Each one starts with a small set of cosmetic perks that scale up by level (external emojis/stickers → +attach files/embed links → +nickname change/priority speaker → +go live). These are just sensible starting defaults — nothing moderation-related is auto-granted. Tweak or add to them anytime in **Server Settings → Roles**; the bot only assigns the role, permissions live on the role itself.

Make sure the bot's own role sits **above** all four of these roles in the role list, or it won't be able to assign them.

To change which levels get roles or their default perks, edit `LEVEL_ROLES` / `LEVEL_PERMISSIONS` at the top of `leveling.py`.

## 3. Run locally (optional, for testing)

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then edit .env and paste your token
```

You'll also need `ffmpeg` installed locally for music to work (`apt install ffmpeg` / `brew install ffmpeg`).

```bash
python main.py
```

## 4. Deploy to Railway

1. Push this project to a GitHub repo.
2. In Railway: **New Project → Deploy from GitHub repo**, pick the repo.
3. Railway will detect Python via Nixpacks. `nixpacks.toml` is already set up to install `ffmpeg` (required for music playback) alongside Python.
4. Go to your service's **Variables** tab and add:
   - `DISCORD_TOKEN` = your bot token
   - `COMMAND_PREFIX` = `!` (optional, defaults to `!`)
   - `DB_PATH` = `/data/bot.db` (only if you attach a volume — see below)
5. Under **Settings**, make sure the start command matches the `Procfile` (`python main.py`). Railway reads the `Procfile` automatically.
6. Deploy. Check the **Deployments → Logs** tab — you should see `Logged in as YourBot#1234` and `Synced N slash command(s)`.

### Persisting XP/warnings data (important)

Railway's default filesystem is **ephemeral** — every redeploy wipes `bot.db`, so levels and warnings will reset. To persist data:

1. In your Railway service, go to **Settings → Volumes → New Volume**, mount it at `/data`.
2. Set the `DB_PATH` variable to `/data/bot.db`.
3. Redeploy.

Alternatively, swap SQLite for a Railway-hosted Postgres/MySQL plugin if you want proper concurrent-write safety — the `database.py` module is the only file you'd need to touch.

## 5. Fix "Sign in to confirm you're not a bot" (YouTube blocking music playback)

YouTube blocks/flags requests coming from datacenter servers like Railway's — this isn't a bug in the bot, it's YouTube's bot detection kicking in on the server's IP. The reliable fix is giving `yt-dlp` real browser cookies to prove the request is coming from a logged-in session.

**Step 1 — Export a cookies.txt file** (needs a normal desktop/laptop browser, one-time):
1. Open Chrome or Firefox, log into youtube.com with any Google account (a throwaway account is fine — don't use your main one for this if you're cautious).
2. Install the **"Get cookies.txt LOCALLY"** extension (Chrome Web Store / Firefox Add-ons).
3. While on youtube.com, click the extension icon and export/download `cookies.txt`.

If you're mobile-only with no access to a desktop browser at all, ask a friend to do this 5-minute step once, or use a cloud desktop/browser service temporarily — there's currently no reliable mobile-only way to export cookies in the right format.

**Step 2 — Add it to Railway:**
1. Open the downloaded `cookies.txt` file, select all, copy the entire contents.
2. In Railway → your service → **Variables**, add a new variable named `YOUTUBE_COOKIES`, and paste the whole file content as its value.
3. Redeploy. `music.py` automatically picks this up and writes it to a temp cookies file for `yt-dlp` on startup.

**Notes:**
- Cookies expire eventually (often after weeks/months) — if `/play` starts failing with the same "sign in" error again later, just repeat the export and update the `YOUTUBE_COOKIES` variable.
- Keep `yt-dlp` current — the requirements file doesn't pin an upper version so you always get the latest on redeploy, but if playback breaks, updating dependencies first is always worth trying.

## 6. Command reference

**Music:** `/play`, `/pause`, `/resume`, `/skip`, `/stop`, `/leave`, `/queue`, `/nowplaying`, `/volume`, `/loop`

**Leveling:** `/rank [member]` (private if checking yourself, public for others), `/leaderboard`, `/setuplevelroles` (admin), `/give_xp` (admin), `/remove_xp` (admin), `/resetxp` (admin, whole-server wipe, asks for confirmation)

**Giveaways:** `/giveaway start <prize> <duration> <winners>` (admin), `/giveaway end <message_id>` (admin), `/giveaway reroll <message_id>` (admin)

**Voice:** join the configured "join to create" channel to get your own temporary VC — it's deleted once everyone leaves

**Utility:** `/snipe` — shows the last deleted message in the current channel

**Moderation:** `/warn`, `/warnings`, `/clearwarnings`, `/removewarning`, `/kick`, `/ban`, `/unban`, `/timeout`, `/untimeout`, `/purge` (max 1000), `/slowmode` — the moderator gets a private confirmation, and the full action gets logged to the logs channel instead of posting in the regular chat

## Notes & known limitations

- **YouTube playback**: see section 5 above if you hit "Sign in to confirm you're not a bot" — this is expected on cloud hosts and fixed with cookies, not a code bug.
- **Deleted/edited message logging & `/snipe`**: Discord only sends content for a deleted/edited message if the bot already had it cached (i.e. it was online and saw the message get sent). If the bot hadn't seen a message before it was deleted, Discord doesn't give us its content at all — logs/snipe will show "no text content" in that case, not a bug.
- **Join-to-create channels**: tracked in memory. If the bot restarts while a temp channel is in use, that one channel won't be auto-deleted when it empties out afterward (you'd need to remove it manually) — new ones created after the restart work normally.
- **Auto-leave**: the bot leaves voice automatically ~60 seconds after everyone else leaves its channel.
- Music state is stored in memory per guild, so it resets if the bot restarts (the queue, not levels/warnings — those are in the DB).
- The leveling XP curve and cooldown are defined at the top of `leveling.py` if you want to tune them.

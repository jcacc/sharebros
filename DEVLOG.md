# sharebro devlog

## 2026-02-23 — Add discoverydate command to fm cog

### fm cog — discoverydate
Added `.discoverydate` (aliases: `.dd`, `.firstlisten`) — shows when a user first listened to an artist, which track it was, and total all-time plays.

Uses `user.getArtistTracks` with `limit=1, page=1` to get `total` and `totalPages`, then fetches `page=totalPages` to retrieve the oldest scrobble's timestamp. Displays date, human-readable "X years/months/days ago", first track scrobbled, and total play count.

Works with no argument (uses current track's artist), a named artist, or `[@user] [artist]` to look up another server member.

## 2026-02-23 — Add crowns system to fm cog

### fm cog — crowns system
Added persistent crown tracking to `cogs/fm.py`. The #1 listener per artist per server earns the crown, which can be stolen and is announced when it changes hands.

**DB:** New `crowns` table in `fm.db` — `(guild_id, artist_name)` primary key, stores `artist_display`, `discord_id`, `play_count`. Helpers: `_get_crown`, `_set_crown`, `_guild_crowns`.

**`whoknows` changes:** `fetch_plays` now returns full member objects. After resolving the leaderboard, if #1 has ≥30 plays the crown is awarded/updated silently, or a steal is announced: `👑 **name** stole the crown for **Artist** from oldname with X,XXX plays!`. Crown holder gets 👑 prefix instead of a position number in the embed.

**New commands:** `crown <artist>`, `crowns [@user]` (cap 20), `servercrowns` (cap 15, sorted by play count), `topcrowns` (members ranked by crown count).

## 2026-02-23 — Add imdb cog; switch to TMDb Bearer auth

### imdb cog
Added `cogs/imdb.py` — `.imdb <title>` searches TMDb for movies and TV shows, returns a Discord embed with title, year, rating, genre, director/creator, top cast, plot, and poster thumbnail. Links to TMDb page.

Auth uses the TMDb Read Access Token (Bearer header) via `config.yaml` key `tmdb.read_access_token`. The v3 API key is also stored but not used for requests.

## 2026-02-21 — Expand fm cog; add sysinfo + youtube; slash commands; prefix change

### fm cog — full fmbot-style rewrite
Rewrote `cogs/fm.py` with SQLite storage and a full command set matching nugbot's fm cog.

**Storage:** Migrated from flat `fm_users.json` to SQLite (`cogs/fm.db`). Auto-migrates on first load.

**Commands:** `setfm`, `fm`, `recent`, `plays` (p), `toptracks` (tt), `topalbums` (tab), `topartists` (ta), `track` (tr), `trackplays` (tp), `artist` (a), `artistplays` (ap), `album` (ab), `albumplays` (abp), `whoknows` (wk), `whoknowstrack` (wktr/wt), `whoknowsalbum` (wkab/wa), `taste`, `overview` (o), `streak` (str), `serverartists`, `serveralbums`, `servertracks`

**Bug fixes:**
- `whoknows` family: `guild.get_member()` returns None without Members intent — `_guild_registered()` helper falls back to `guild.fetch_member()`
- `taste`: artist fetch limit raised from 50 → 1000

### sysinfo cog
Added `cogs/sysinfo.py` — `.sysinfo` / `.top` posts system snapshot embed: CPU, memory, disk, uptime, top 5 processes. Uses `psutil`.

### youtube cog
Added `cogs/youtube.py` — `.youtube` / `.yt <query>` searches YouTube via `yt-dlp`, returns top 5 results embed.

### Slash commands
All cog commands converted to `@commands.hybrid_command()` — work as prefix (`.`) and slash (`/`). Period params use `Literal` type for dropdown. `tree.sync()` in `setup_hook`.

### Other
- Command prefix changed from `!` to `.`
- `deadwood`: filter blank lines before random pick to prevent empty message 400 error
- lampPost deploy: scp files to `~/dev/python/sharebro/`, restart `sharebro.service`

# sharebro devlog

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

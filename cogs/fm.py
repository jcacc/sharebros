import asyncio
import datetime
import sqlite3
import time
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, Literal
import aiohttp
import json
import os
import yaml

LASTFM_API = 'https://ws.audioscrobbler.com/2.0/'
USERS_FILE = '/home/jca/dev/python/sharebro/cogs/fm_users.json'
DB_PATH    = '/home/jca/dev/python/sharebro/cogs/fm.db'

PERIODS = {
    'week':   '7day',
    'month':  '1month',
    '3month': '3month',
    '6month': '6month',
    'year':   '12month',
    'all':    'overall'
}

Period = Literal['week', 'month', '3month', '6month', 'year', 'all']


def load_config(config_file='/home/jca/dev/python/sharebro/config.yaml'):
    with open(config_file) as f:
        return yaml.safe_load(f)


def _init_db(db_path):
    """Create users table and migrate from JSON if needed."""
    con = sqlite3.connect(db_path)
    con.execute(
        'CREATE TABLE IF NOT EXISTS users '
        '(discord_id TEXT PRIMARY KEY, lastfm_username TEXT NOT NULL)'
    )
    con.execute(
        'CREATE TABLE IF NOT EXISTS crowns '
        '(guild_id TEXT NOT NULL, artist_name TEXT NOT NULL, '
        'artist_display TEXT NOT NULL, discord_id TEXT NOT NULL, '
        'play_count INTEGER NOT NULL, PRIMARY KEY (guild_id, artist_name))'
    )
    con.execute(
        'CREATE TABLE IF NOT EXISTS scrobbles ('
        '  lfm_username  TEXT    NOT NULL,'
        '  artist        TEXT    NOT NULL,'
        '  track         TEXT    NOT NULL,'
        '  album         TEXT    NOT NULL DEFAULT "",'
        '  scrobbled_at  INTEGER NOT NULL,'
        '  PRIMARY KEY (lfm_username, scrobbled_at, artist, track)'
        ')'
    )
    con.execute(
        'CREATE INDEX IF NOT EXISTS idx_scrobbles_user_artist '
        'ON scrobbles (lfm_username, LOWER(artist))'
    )
    con.execute(
        'CREATE TABLE IF NOT EXISTS scrobble_sync ('
        '  lfm_username   TEXT    PRIMARY KEY,'
        '  last_synced_ts INTEGER NOT NULL DEFAULT 0,'
        '  total_cached   INTEGER NOT NULL DEFAULT 0,'
        '  synced_at      INTEGER NOT NULL DEFAULT 0'
        ')'
    )
    con.commit()
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f:
                old = json.load(f)
            for uid, username in old.items():
                con.execute(
                    'INSERT OR IGNORE INTO users (discord_id, lastfm_username) VALUES (?, ?)',
                    (str(uid), username)
                )
            con.commit()
            os.rename(USERS_FILE, USERS_FILE + '.migrated')
        except Exception:
            pass
    con.close()


class FM(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        config = load_config()
        self.api_key = config['lastfm']['api_key']
        _init_db(DB_PATH)

    # ------------------------------------------------------------------ #
    #  DB helpers                                                          #
    # ------------------------------------------------------------------ #
    def _get_lfm(self, user):
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            'SELECT lastfm_username FROM users WHERE discord_id = ?',
            (str(user.id),)
        ).fetchone()
        con.close()
        return row[0] if row else None

    def _set_lfm(self, user_id, username):
        con = sqlite3.connect(DB_PATH)
        con.execute(
            'INSERT OR REPLACE INTO users (discord_id, lastfm_username) VALUES (?, ?)',
            (str(user_id), username)
        )
        con.commit()
        con.close()

    def _all_users(self):
        con = sqlite3.connect(DB_PATH)
        rows = con.execute('SELECT discord_id, lastfm_username FROM users').fetchall()
        con.close()
        return rows

    async def _guild_registered(self, guild):
        result = []
        for uid, lfm in self._all_users():
            member = guild.get_member(int(uid))
            if not member:
                try:
                    member = await guild.fetch_member(int(uid))
                except Exception:
                    continue
            result.append((member, lfm))
        return result

    def _get_crown(self, guild_id, artist_name):
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            'SELECT artist_display, discord_id, play_count FROM crowns WHERE guild_id = ? AND artist_name = ?',
            (guild_id, artist_name.strip().lower())
        ).fetchone()
        con.close()
        return row  # (artist_display, discord_id, play_count) or None

    def _set_crown(self, guild_id, artist_name, artist_display, discord_id, play_count):
        con = sqlite3.connect(DB_PATH)
        con.execute(
            'INSERT OR REPLACE INTO crowns (guild_id, artist_name, artist_display, discord_id, play_count) '
            'VALUES (?, ?, ?, ?, ?)',
            (guild_id, artist_name.strip().lower(), artist_display, str(discord_id), play_count)
        )
        con.commit()
        con.close()

    def _guild_crowns(self, guild_id):
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            'SELECT artist_display, discord_id, play_count FROM crowns WHERE guild_id = ? ORDER BY play_count DESC',
            (guild_id,)
        ).fetchall()
        con.close()
        return rows

    def _get_sync_state(self, lfm):
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            'SELECT last_synced_ts, total_cached, synced_at FROM scrobble_sync WHERE lfm_username = ?',
            (lfm,)
        ).fetchone()
        con.close()
        return row  # (last_synced_ts, total_cached, synced_at) or None

    def _update_sync_state(self, lfm, last_synced_ts, total_cached):
        con = sqlite3.connect(DB_PATH)
        con.execute(
            'INSERT OR REPLACE INTO scrobble_sync (lfm_username, last_synced_ts, total_cached, synced_at) '
            'VALUES (?, ?, ?, ?)',
            (lfm, last_synced_ts, total_cached, int(time.time()))
        )
        con.commit()
        con.close()

    def _insert_scrobbles(self, lfm, rows):
        con = sqlite3.connect(DB_PATH)
        con.executemany(
            'INSERT OR IGNORE INTO scrobbles (lfm_username, artist, track, album, scrobbled_at) '
            'VALUES (?, ?, ?, ?, ?)',
            [(lfm, r[0], r[1], r[2], r[3]) for r in rows]
        )
        con.commit()
        con.close()

    def _query_first_scrobble(self, lfm, artist_lower):
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            'SELECT artist, track, album, scrobbled_at FROM scrobbles '
            'WHERE lfm_username = ? AND LOWER(artist) = ? AND scrobbled_at >= 1000000000 ORDER BY scrobbled_at ASC LIMIT 1',
            (lfm, artist_lower)
        ).fetchone()
        con.close()
        return row  # (artist, track, album, scrobbled_at) or None

    def _count_scrobbles_for_artist(self, lfm, artist_lower):
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            'SELECT COUNT(*) FROM scrobbles WHERE lfm_username = ? AND LOWER(artist) = ?',
            (lfm, artist_lower)
        ).fetchone()
        con.close()
        return row[0] if row else 0

    def _count_cached_scrobbles(self, lfm):
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            'SELECT COUNT(*) FROM scrobbles WHERE lfm_username = ?',
            (lfm,)
        ).fetchone()
        con.close()
        return row[0] if row else 0

    # ------------------------------------------------------------------ #
    #  API helpers                                                         #
    # ------------------------------------------------------------------ #
    async def _api(self, session, params):
        params['api_key'] = self.api_key
        params['format']  = 'json'
        async with session.get(LASTFM_API, params=params) as resp:
            return await resp.json()

    async def _current_track(self, session, lfm):
        data = await self._api(session, {
            'method': 'user.getrecenttracks',
            'user': lfm,
            'limit': 1
        })
        tracks = data.get('recenttracks', {}).get('track', [])
        if not tracks:
            return None
        return tracks[0] if isinstance(tracks, list) else tracks

    async def _resolve_artist(self, session, query):
        """Return the canonical Last.fm artist name.
        Prefers an exact (case-insensitive) name match; falls back to the
        result with the most listeners for partial/variant queries."""
        data = await self._api(session, {
            'method': 'artist.search',
            'artist': query,
            'limit': 5
        })
        matches = data.get('results', {}).get('artistmatches', {}).get('artist', [])
        if not matches:
            return query
        query_lower = query.strip().lower()
        exact = next((m for m in matches if m['name'].strip().lower() == query_lower), None)
        if exact:
            return exact['name']
        best = max(matches, key=lambda m: int(m.get('listeners', 0) or 0))
        return best['name']

    async def _sync_scrobbles(self, session, lfm, status_callback=None):
        """Bulk-sync a user's scrobble history into the local DB.
        Returns (fetched_this_run, total_cached).
        """
        sync_state = self._get_sync_state(lfm)
        is_first_sync = sync_state is None
        from_ts = 0 if is_first_sync else sync_state[0] + 1

        params = {
            'method': 'user.getRecentTracks',
            'user': lfm,
            'limit': 200,
            'page': 1,
            'extended': 0,
        }
        if from_ts:
            params['from'] = from_ts

        # Probe page 1
        try:
            p1 = await self._api(session, params)
        except Exception:
            return (0, self._count_cached_scrobbles(lfm))

        attr = p1.get('recenttracks', {}).get('@attr', {})
        total_pages = int(attr.get('totalPages', 1))
        total = int(attr.get('total', 0))
        if total == 0:
            now_ts = int(time.time())
            cached = self._count_cached_scrobbles(lfm)
            self._update_sync_state(lfm, now_ts, cached)
            return (0, cached)

        def parse_page(data):
            tracks = data.get('recenttracks', {}).get('track', [])
            if isinstance(tracks, dict):
                tracks = [tracks]
            rows = []
            for t in tracks:
                if t.get('@attr', {}).get('nowplaying') == 'true':
                    continue
                uts = t.get('date', {}).get('uts')
                if not uts or int(uts) < 1000000000:
                    continue
                artist = t.get('artist', {}).get('#text', '') or ''
                track  = t.get('name', '') or ''
                album  = t.get('album', {}).get('#text', '') or ''
                rows.append((artist, track, album, int(uts)))
            return rows

        # Store page 1
        rows1 = parse_page(p1)
        if rows1:
            self._insert_scrobbles(lfm, rows1)
        fetched = len(rows1)

        if total_pages > 1:
            sem = asyncio.Semaphore(5)
            batch_size = 5
            pages = list(range(2, total_pages + 1))
            batch_num = 0

            for i in range(0, len(pages), batch_size):
                batch = pages[i:i + batch_size]

                async def fetch_page(pg):
                    p = dict(params)
                    p['page'] = pg
                    async with sem:
                        try:
                            return await self._api(session, p)
                        except Exception:
                            return {}

                results = await asyncio.gather(*[fetch_page(pg) for pg in batch])
                for data in results:
                    rows = parse_page(data)
                    if rows:
                        self._insert_scrobbles(lfm, rows)
                        fetched += len(rows)

                batch_num += 1
                if status_callback and batch_num % 4 == 0:
                    pages_done = min(i + batch_size + 1, total_pages)
                    await status_callback(pages_done, total_pages, fetched)

                if i + batch_size < len(pages):
                    await asyncio.sleep(1.0)

        # Finalize sync state
        con = sqlite3.connect(DB_PATH)
        max_ts_row = con.execute(
            'SELECT MAX(scrobbled_at) FROM scrobbles WHERE lfm_username = ?', (lfm,)
        ).fetchone()
        total_cached = con.execute(
            'SELECT COUNT(*) FROM scrobbles WHERE lfm_username = ?', (lfm,)
        ).fetchone()[0]
        con.close()

        last_synced_ts = max_ts_row[0] if max_ts_row and max_ts_row[0] else int(time.time())
        self._update_sync_state(lfm, last_synced_ts, total_cached)
        return (fetched, total_cached)

    def _no_lfm_msg(self):
        return 'No Last.fm username set. Use `!setfm <username>`.'

    # ------------------------------------------------------------------ #
    #  Commands: registration + now playing                                #
    # ------------------------------------------------------------------ #
    @commands.hybrid_command()
    @app_commands.describe(username='Your Last.fm username')
    async def setfm(self, ctx, username: str):
        """Set your Last.fm username."""
        self._set_lfm(ctx.author.id, username)
        await ctx.send(f'Last.fm username set to `{username}`.')

    @commands.hybrid_command()
    @app_commands.describe(member='User to look up (default: you)')
    async def fm(self, ctx, member: Optional[discord.Member] = None):
        """Show now playing / last played track."""
        user = member or ctx.author
        lfm = self._get_lfm(user)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        async with aiohttp.ClientSession() as session:
            track = await self._current_track(session, lfm)

        if not track:
            await ctx.send(f'No recent tracks found for `{lfm}`.')
            return

        now_playing = track.get('@attr', {}).get('nowplaying') == 'true'
        title  = track['name']
        artist = track['artist']['#text']
        album  = track['album']['#text']
        url    = track['url']
        image  = next((i['#text'] for i in track.get('image', [])
                       if i['size'] == 'large' and i['#text']), None)

        embed = discord.Embed(
            title=title, url=url,
            description=f'by **{artist}**' + (f' on *{album}*' if album else ''),
            color=0xD51007
        )
        embed.set_author(name=f'{"🎵 Now playing" if now_playing else "⏮ Last played"} — {lfm}')
        if image:
            embed.set_thumbnail(url=image)
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    @app_commands.describe(member='User to look up (default: you)')
    async def recent(self, ctx, member: Optional[discord.Member] = None):
        """Show 5 most recent tracks."""
        user = member or ctx.author
        lfm = self._get_lfm(user)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        async with aiohttp.ClientSession() as session:
            data = await self._api(session, {
                'method': 'user.getrecenttracks',
                'user': lfm,
                'limit': 5
            })

        tracks = data.get('recenttracks', {}).get('track', [])
        if not tracks:
            await ctx.send(f'No recent tracks found for `{lfm}`.')
            return

        lines = []
        for t in tracks[:5]:
            now = t.get('@attr', {}).get('nowplaying') == 'true'
            lines.append(f'{"▶" if now else "·"} **{t["name"]}** — {t["artist"]["#text"]}')

        embed = discord.Embed(
            title=f'Recent tracks — {lfm}',
            description='\n'.join(lines),
            color=0xD51007
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  Commands: plays / top lists                                         #
    # ------------------------------------------------------------------ #
    @commands.hybrid_command(aliases=['p'])
    @app_commands.describe(member='User to look up (default: you)', period='Time period')
    async def plays(self, ctx, member: Optional[discord.Member] = None, period: Period = 'week'):
        """Total scrobble count for a period."""
        user = member or ctx.author
        lfm = self._get_lfm(user)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        lfm_period = PERIODS.get(period, '7day')
        async with aiohttp.ClientSession() as session:
            data = await self._api(session, {
                'method': 'user.getrecenttracks',
                'user': lfm,
                'period': lfm_period,
                'limit': 1
            })

        total = data.get('recenttracks', {}).get('@attr', {}).get('total', '?')
        await ctx.send(
            f'**{lfm}** has **{int(total):,}** scrobbles this {period}.'
            if str(total).isdigit() else f'**{lfm}**: {total} scrobbles ({period})'
        )

    @commands.hybrid_command(aliases=['tt'])
    @app_commands.describe(member='User to look up (default: you)', period='Time period')
    async def toptracks(self, ctx, member: Optional[discord.Member] = None, period: Period = 'week'):
        """Top 10 tracks for a period."""
        user = member or ctx.author
        lfm = self._get_lfm(user)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        lfm_period = PERIODS.get(period, '7day')
        async with aiohttp.ClientSession() as session:
            data = await self._api(session, {
                'method': 'user.gettoptracks',
                'user': lfm,
                'period': lfm_period,
                'limit': 10
            })

        tracks = data.get('toptracks', {}).get('track', [])
        if not tracks:
            await ctx.send(f'No top tracks found for `{lfm}`.')
            return

        lines = [
            f'`{i+1}.` **{t["name"]}** — {t["artist"]["name"]} ({int(t["playcount"]):,} plays)'
            for i, t in enumerate(tracks)
        ]
        embed = discord.Embed(
            title=f'Top tracks ({period}) — {lfm}',
            description='\n'.join(lines),
            color=0xD51007
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['tab'])
    @app_commands.describe(member='User to look up (default: you)', period='Time period')
    async def topalbums(self, ctx, member: Optional[discord.Member] = None, period: Period = 'week'):
        """Top 10 albums for a period."""
        user = member or ctx.author
        lfm = self._get_lfm(user)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        lfm_period = PERIODS.get(period, '7day')
        async with aiohttp.ClientSession() as session:
            data = await self._api(session, {
                'method': 'user.gettopalbums',
                'user': lfm,
                'period': lfm_period,
                'limit': 10
            })

        albums = data.get('topalbums', {}).get('album', [])
        if not albums:
            await ctx.send(f'No top albums found for `{lfm}`.')
            return

        lines = [
            f'`{i+1}.` **{a["name"]}** — {a["artist"]["name"]} ({int(a["playcount"]):,} plays)'
            for i, a in enumerate(albums)
        ]
        embed = discord.Embed(
            title=f'Top albums ({period}) — {lfm}',
            description='\n'.join(lines),
            color=0xD51007
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['ta'])
    @app_commands.describe(member='User to look up (default: you)', period='Time period')
    async def topartists(self, ctx, member: Optional[discord.Member] = None, period: Period = 'week'):
        """Top 10 artists for a period."""
        user = member or ctx.author
        lfm = self._get_lfm(user)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        lfm_period = PERIODS.get(period, '7day')
        async with aiohttp.ClientSession() as session:
            data = await self._api(session, {
                'method': 'user.gettopartists',
                'user': lfm,
                'period': lfm_period,
                'limit': 10
            })

        artists = data.get('topartists', {}).get('artist', [])
        if not artists:
            await ctx.send(f'No top artists found for `{lfm}`.')
            return

        lines = [
            f'`{i+1}.` **{a["name"]}** — {int(a["playcount"]):,} plays'
            for i, a in enumerate(artists)
        ]
        embed = discord.Embed(
            title=f'Top artists ({period}) — {lfm}',
            description='\n'.join(lines),
            color=0xD51007
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  Commands: track / album / artist info                               #
    # ------------------------------------------------------------------ #
    @commands.hybrid_command(aliases=['tr'])
    @app_commands.describe(query='Artist - Track (leave blank for current track)')
    async def track(self, ctx, *, query: str = None):
        """Info + your play count for current or named track (Artist - Track)."""
        lfm = self._get_lfm(ctx.author)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        async with aiohttp.ClientSession() as session:
            if not query:
                t = await self._current_track(session, lfm)
                if not t:
                    await ctx.send('No recent track found.')
                    return
                artist_name = t['artist']['#text']
                track_name  = t['name']
            else:
                parts = query.split(' - ', 1)
                if len(parts) == 2:
                    artist_name, track_name = parts[0].strip(), parts[1].strip()
                else:
                    t = await self._current_track(session, lfm)
                    artist_name = t['artist']['#text'] if t else query
                    track_name  = query

            data = await self._api(session, {
                'method': 'track.getInfo',
                'artist': artist_name,
                'track': track_name,
                'username': lfm,
                'autocorrect': 1
            })

        ti = data.get('track', {})
        if not ti:
            await ctx.send(f'Track not found: {track_name}')
            return

        name       = ti.get('name', track_name)
        artist     = ti.get('artist', {}).get('name', artist_name)
        url        = ti.get('url', '')
        listeners  = int(ti.get('listeners', 0))
        scrobbles  = int(ti.get('playcount', 0))
        user_plays = int(ti.get('userplaycount', 0))
        album_name = ti.get('album', {}).get('title', '') if ti.get('album') else ''
        image = None
        if ti.get('album', {}).get('image'):
            image = next((i['#text'] for i in ti['album']['image']
                          if i['size'] == 'large' and i['#text']), None)

        embed = discord.Embed(title=name, url=url, color=0xD51007)
        embed.set_author(name=f'Track info — {lfm}')
        embed.add_field(name='Artist', value=artist, inline=True)
        if album_name:
            embed.add_field(name='Album', value=album_name, inline=True)
        embed.add_field(name='Your plays', value=f'{user_plays:,}', inline=True)
        embed.add_field(name='Global scrobbles', value=f'{scrobbles:,}', inline=True)
        embed.add_field(name='Listeners', value=f'{listeners:,}', inline=True)
        if image:
            embed.set_thumbnail(url=image)
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['tp'])
    @app_commands.describe(query='Artist - Track (leave blank for current track)')
    async def trackplays(self, ctx, *, query: str = None):
        """Play count for current or named track."""
        lfm = self._get_lfm(ctx.author)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        async with aiohttp.ClientSession() as session:
            if not query:
                t = await self._current_track(session, lfm)
                if not t:
                    await ctx.send('No recent track found.')
                    return
                artist_name = t['artist']['#text']
                track_name  = t['name']
            else:
                parts = query.split(' - ', 1)
                if len(parts) == 2:
                    artist_name, track_name = parts[0].strip(), parts[1].strip()
                else:
                    t = await self._current_track(session, lfm)
                    artist_name = t['artist']['#text'] if t else query
                    track_name  = query

            data = await self._api(session, {
                'method': 'track.getInfo',
                'artist': artist_name,
                'track': track_name,
                'username': lfm,
                'autocorrect': 1
            })

        ti = data.get('track', {})
        user_plays = int(ti.get('userplaycount', 0)) if ti else 0
        name   = ti.get('name', track_name) if ti else track_name
        artist = ti.get('artist', {}).get('name', artist_name) if ti else artist_name
        await ctx.send(f'**{lfm}** has played **{name}** by {artist} **{user_plays:,}** times.')

    @commands.hybrid_command(aliases=['a'])
    @app_commands.describe(query='Artist name (leave blank for current track\'s artist)')
    async def artist(self, ctx, *, query: str = None):
        """Info + your play count for current or named artist."""
        lfm = self._get_lfm(ctx.author)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        async with aiohttp.ClientSession() as session:
            if not query:
                t = await self._current_track(session, lfm)
                if not t:
                    await ctx.send('No recent track found.')
                    return
                query = t['artist']['#text']
            else:
                query = await self._resolve_artist(session, query)

            data = await self._api(session, {
                'method': 'artist.getInfo',
                'artist': query,
                'username': lfm,
                'autocorrect': 1
            })

        ai = data.get('artist', {})
        if not ai:
            await ctx.send(f'Artist not found: {query}')
            return

        name       = ai.get('name', query)
        url        = ai.get('url', '')
        listeners  = int(ai.get('stats', {}).get('listeners', 0))
        scrobbles  = int(ai.get('stats', {}).get('playcount', 0))
        user_plays = int(ai.get('stats', {}).get('userplaycount', 0))
        bio        = ai.get('bio', {}).get('summary', '')
        import re
        bio = re.sub(r'<[^>]+>', '', bio).split('Read more')[0].strip()[:300]

        embed = discord.Embed(title=name, url=url, color=0xD51007)
        embed.set_author(name=f'Artist info — {lfm}')
        embed.add_field(name='Your plays', value=f'{user_plays:,}', inline=True)
        embed.add_field(name='Global scrobbles', value=f'{scrobbles:,}', inline=True)
        embed.add_field(name='Listeners', value=f'{listeners:,}', inline=True)
        if bio:
            embed.add_field(name='Bio', value=bio, inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['ap'])
    @app_commands.describe(query='Artist name (leave blank for current track\'s artist)')
    async def artistplays(self, ctx, *, query: str = None):
        """Play count for current or named artist."""
        lfm = self._get_lfm(ctx.author)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        async with aiohttp.ClientSession() as session:
            if not query:
                t = await self._current_track(session, lfm)
                if not t:
                    await ctx.send('No recent track found.')
                    return
                query = t['artist']['#text']
            else:
                query = await self._resolve_artist(session, query)

            data = await self._api(session, {
                'method': 'artist.getInfo',
                'artist': query,
                'username': lfm,
                'autocorrect': 1
            })

        ai = data.get('artist', {})
        user_plays = int(ai.get('stats', {}).get('userplaycount', 0)) if ai else 0
        name = ai.get('name', query) if ai else query
        await ctx.send(f'**{lfm}** has played **{name}** **{user_plays:,}** times.')

    @commands.hybrid_command(aliases=['ab'])
    @app_commands.describe(query='Artist - Album (leave blank for current album)')
    async def album(self, ctx, *, query: str = None):
        """Info + your play count for current or named album (Artist - Album)."""
        lfm = self._get_lfm(ctx.author)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        async with aiohttp.ClientSession() as session:
            if not query:
                t = await self._current_track(session, lfm)
                if not t:
                    await ctx.send('No recent track found.')
                    return
                artist_name = t['artist']['#text']
                album_name  = t['album']['#text']
            else:
                parts = query.split(' - ', 1)
                if len(parts) == 2:
                    artist_name, album_name = parts[0].strip(), parts[1].strip()
                else:
                    t = await self._current_track(session, lfm)
                    artist_name = t['artist']['#text'] if t else query
                    album_name  = query

            data = await self._api(session, {
                'method': 'album.getInfo',
                'artist': artist_name,
                'album': album_name,
                'username': lfm,
                'autocorrect': 1
            })

        ali = data.get('album', {})
        if not ali:
            await ctx.send(f'Album not found: {album_name}')
            return

        name       = ali.get('name', album_name)
        artist     = ali.get('artist', artist_name)
        url        = ali.get('url', '')
        scrobbles  = int(ali.get('playcount', 0))
        listeners  = int(ali.get('listeners', 0))
        user_plays = int(ali.get('userplaycount', 0))
        image      = next((i['#text'] for i in ali.get('image', [])
                           if i['size'] == 'large' and i['#text']), None)

        embed = discord.Embed(title=name, url=url, color=0xD51007)
        embed.set_author(name=f'Album info — {lfm}')
        embed.add_field(name='Artist', value=artist, inline=True)
        embed.add_field(name='Your plays', value=f'{user_plays:,}', inline=True)
        embed.add_field(name='Global scrobbles', value=f'{scrobbles:,}', inline=True)
        embed.add_field(name='Listeners', value=f'{listeners:,}', inline=True)
        if image:
            embed.set_thumbnail(url=image)
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['abp'])
    @app_commands.describe(query='Artist - Album (leave blank for current album)')
    async def albumplays(self, ctx, *, query: str = None):
        """Play count for current or named album."""
        lfm = self._get_lfm(ctx.author)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        async with aiohttp.ClientSession() as session:
            if not query:
                t = await self._current_track(session, lfm)
                if not t:
                    await ctx.send('No recent track found.')
                    return
                artist_name = t['artist']['#text']
                album_name  = t['album']['#text']
            else:
                parts = query.split(' - ', 1)
                if len(parts) == 2:
                    artist_name, album_name = parts[0].strip(), parts[1].strip()
                else:
                    t = await self._current_track(session, lfm)
                    artist_name = t['artist']['#text'] if t else query
                    album_name  = query

            data = await self._api(session, {
                'method': 'album.getInfo',
                'artist': artist_name,
                'album': album_name,
                'username': lfm,
                'autocorrect': 1
            })

        ali = data.get('album', {})
        user_plays = int(ali.get('userplaycount', 0)) if ali else 0
        name   = ali.get('name', album_name) if ali else album_name
        artist = ali.get('artist', artist_name) if ali else artist_name
        await ctx.send(f'**{lfm}** has played **{name}** by {artist} **{user_plays:,}** times.')

    # ------------------------------------------------------------------ #
    #  Commands: who knows                                                 #
    # ------------------------------------------------------------------ #
    @commands.hybrid_command(aliases=['wk'])
    @app_commands.describe(artist='Artist name (leave blank for current track\'s artist)')
    async def whoknows(self, ctx, *, artist: str = None):
        """Server members ranked by plays for an artist."""
        async with aiohttp.ClientSession() as session:
            if not artist:
                lfm = self._get_lfm(ctx.author)
                if not lfm:
                    await ctx.send('Provide an artist name, or set your Last.fm with `.setfm <username>`.')
                    return
                t = await self._current_track(session, lfm)
                if not t:
                    await ctx.send('Could not get current artist. Provide an artist name.')
                    return
                artist = t['artist']['#text']
            else:
                artist = await self._resolve_artist(session, artist)

        registered = await self._guild_registered(ctx.guild)
        if not registered:
            await ctx.send('No registered Last.fm users in this server.')
            return

        async def fetch_plays(session, member, lfm):
            try:
                data = await self._api(session, {
                    'method': 'artist.getInfo',
                    'artist': artist,
                    'username': lfm,
                    'autocorrect': 1
                })
                plays = int(data.get('artist', {}).get('stats', {}).get('userplaycount', 0))
                return (member, plays)
            except Exception:
                return None

        async with ctx.typing():
            async with aiohttp.ClientSession() as session:
                raw = await asyncio.gather(*[fetch_plays(session, m, l) for m, l in registered])

        results = sorted(
            [r for r in raw if r and r[1] > 0],
            key=lambda x: x[1], reverse=True
        )

        crown_holder_id = None
        if results:
            top_member, top_plays = results[0]
            if top_plays >= 30:
                existing = self._get_crown(str(ctx.guild.id), artist.strip().lower())
                if existing is None:
                    self._set_crown(str(ctx.guild.id), artist, artist, str(top_member.id), top_plays)
                    crown_holder_id = top_member.id
                elif existing[1] == str(top_member.id):
                    self._set_crown(str(ctx.guild.id), artist, artist, str(top_member.id), top_plays)
                    crown_holder_id = top_member.id
                else:
                    old_member = ctx.guild.get_member(int(existing[1]))
                    old_name = old_member.display_name if old_member else existing[0]
                    self._set_crown(str(ctx.guild.id), artist, artist, str(top_member.id), top_plays)
                    await ctx.send(f'👑 **{top_member.display_name}** stole the crown for **{artist}** from {old_name} with {top_plays:,} plays!')
                    crown_holder_id = top_member.id

        if not results:
            await ctx.send(f'Nobody in this server has listened to **{artist}**.')
            return

        lines = []
        for i, (m, plays) in enumerate(results):
            prefix = '👑' if (i == 0 and crown_holder_id == m.id) else f'`{i+1}.`'
            lines.append(f'{prefix} **{m.display_name}** — {plays:,} plays')
        embed = discord.Embed(
            title=f'Who knows {artist}?',
            description='\n'.join(lines),
            color=0xD51007
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['wktr', 'wt'])
    @app_commands.describe(query='Artist - Track (leave blank for current track)')
    async def whoknowstrack(self, ctx, *, query: str = None):
        """Server members ranked by plays for a track."""
        lfm_author = self._get_lfm(ctx.author)

        async with aiohttp.ClientSession() as session:
            if not query:
                if not lfm_author:
                    await ctx.send('Provide a track, or set your Last.fm with `.setfm <username>`.')
                    return
                t = await self._current_track(session, lfm_author)
                if not t:
                    await ctx.send('No recent track found.')
                    return
                artist_name = t['artist']['#text']
                track_name  = t['name']
            else:
                parts = query.split(' - ', 1)
                if len(parts) == 2:
                    artist_name = await self._resolve_artist(session, parts[0].strip())
                    track_name  = parts[1].strip()
                else:
                    if not lfm_author:
                        await ctx.send('Use format: Artist - Track')
                        return
                    t = await self._current_track(session, lfm_author)
                    artist_name = t['artist']['#text'] if t else query
                    track_name  = query

            registered = await self._guild_registered(ctx.guild)
            if not registered:
                await ctx.send('No registered Last.fm users in this server.')
                return

            async def fetch_track_plays(session, member, lfm):
                try:
                    data = await self._api(session, {
                        'method': 'track.getInfo',
                        'artist': artist_name,
                        'track': track_name,
                        'username': lfm,
                        'autocorrect': 1
                    })
                    plays = int(data.get('track', {}).get('userplaycount', 0))
                    return (member.display_name, plays)
                except Exception:
                    return None

            async with ctx.typing():
                raw = await asyncio.gather(*[fetch_track_plays(session, m, l) for m, l in registered])

        results = sorted(
            [r for r in raw if r and r[1] > 0],
            key=lambda x: x[1], reverse=True
        )
        if not results:
            await ctx.send(f'Nobody in this server has played **{track_name}** by {artist_name}.')
            return

        lines = [f'`{i+1}.` **{name}** — {plays:,} plays'
                 for i, (name, plays) in enumerate(results)]
        embed = discord.Embed(
            title=f'Who knows {track_name} — {artist_name}?',
            description='\n'.join(lines),
            color=0xD51007
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['wkab', 'wa'])
    @app_commands.describe(query='Artist - Album (leave blank for current album)')
    async def whoknowsalbum(self, ctx, *, query: str = None):
        """Server members ranked by plays for an album."""
        lfm_author = self._get_lfm(ctx.author)

        async with aiohttp.ClientSession() as session:
            if not query:
                if not lfm_author:
                    await ctx.send('Provide an album, or set your Last.fm with `.setfm <username>`.')
                    return
                t = await self._current_track(session, lfm_author)
                if not t:
                    await ctx.send('No recent track found.')
                    return
                artist_name = t['artist']['#text']
                album_name  = t['album']['#text']
            else:
                parts = query.split(' - ', 1)
                if len(parts) == 2:
                    artist_name = await self._resolve_artist(session, parts[0].strip())
                    album_name  = parts[1].strip()
                else:
                    if not lfm_author:
                        await ctx.send('Use format: Artist - Album')
                        return
                    t = await self._current_track(session, lfm_author)
                    artist_name = t['artist']['#text'] if t else query
                    album_name  = query

            registered = await self._guild_registered(ctx.guild)
            if not registered:
                await ctx.send('No registered Last.fm users in this server.')
                return

            async def fetch_album_plays(session, member, lfm):
                try:
                    data = await self._api(session, {
                        'method': 'album.getInfo',
                        'artist': artist_name,
                        'album': album_name,
                        'username': lfm,
                        'autocorrect': 1
                    })
                    plays = int(data.get('album', {}).get('userplaycount', 0))
                    return (member.display_name, plays)
                except Exception:
                    return None

            async with ctx.typing():
                raw = await asyncio.gather(*[fetch_album_plays(session, m, l) for m, l in registered])

        results = sorted(
            [r for r in raw if r and r[1] > 0],
            key=lambda x: x[1], reverse=True
        )
        if not results:
            await ctx.send(f'Nobody in this server has played **{album_name}** by {artist_name}.')
            return

        lines = [f'`{i+1}.` **{name}** — {plays:,} plays'
                 for i, (name, plays) in enumerate(results)]
        embed = discord.Embed(
            title=f'Who knows {album_name} — {artist_name}?',
            description='\n'.join(lines),
            color=0xD51007
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  Commands: taste / overview / streak                                 #
    # ------------------------------------------------------------------ #
    @commands.hybrid_command(aliases=['t'])
    @app_commands.describe(other='The other user to compare with', period='Time period')
    async def taste(self, ctx, other: discord.Member, period: Period = 'all'):
        """Compare top artist overlap between you and another user."""
        lfm1 = self._get_lfm(ctx.author)
        lfm2 = self._get_lfm(other)
        if not lfm1:
            await ctx.send('Set your Last.fm with `.setfm <username>`.')
            return
        if not lfm2:
            await ctx.send(f'{other.display_name} has no Last.fm set.')
            return

        lfm_period = PERIODS.get(period, 'overall')

        async def fetch_top_artists(session, lfm):
            data = await self._api(session, {
                'method': 'user.gettopartists',
                'user': lfm,
                'period': lfm_period,
                'limit': 1000
            })
            artists = data.get('topartists', {}).get('artist', [])
            return {a['name'].lower(): (a['name'], int(a['playcount'])) for a in artists}

        async with ctx.typing():
            async with aiohttp.ClientSession() as session:
                map1, map2 = await asyncio.gather(
                    fetch_top_artists(session, lfm1),
                    fetch_top_artists(session, lfm2)
                )

        shared_keys = set(map1) & set(map2)
        if not shared_keys:
            await ctx.send(f'No shared artists found between **{lfm1}** and **{lfm2}**.')
            return

        overlap_pct = round(len(shared_keys) / max(len(map1), len(map2)) * 100)
        shared = sorted(
            [(map1[k][0], map1[k][1], map2[k][1]) for k in shared_keys],
            key=lambda x: x[1] + x[2], reverse=True
        )[:10]

        lines = [f'**{name}** — {p1:,} / {p2:,}' for name, p1, p2 in shared]
        embed = discord.Embed(
            title=f'Taste comparison — {lfm1} vs {lfm2}',
            description=f'**{overlap_pct}% overlap** ({len(shared_keys)} shared artists)\n\n' + '\n'.join(lines),
            color=0xD51007
        )
        embed.set_footer(text=f'{lfm1} plays / {lfm2} plays')
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['o'])
    @app_commands.describe(member='User to look up (default: you)', period='Time period')
    async def overview(self, ctx, member: Optional[discord.Member] = None, period: Period = 'week'):
        """Top track + album + artist summary for a period."""
        user = member or ctx.author
        lfm = self._get_lfm(user)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        lfm_period = PERIODS.get(period, '7day')

        async def fetch_top(session, method, key, subkey):
            data = await self._api(session, {
                'method': method,
                'user': lfm,
                'period': lfm_period,
                'limit': 1
            })
            items = data.get(key, {}).get(subkey, [])
            return items[0] if items else None

        async with ctx.typing():
            async with aiohttp.ClientSession() as session:
                top_artist, top_album, top_track = await asyncio.gather(
                    fetch_top(session, 'user.gettopartists', 'topartists', 'artist'),
                    fetch_top(session, 'user.gettopalbums',  'topalbums',  'album'),
                    fetch_top(session, 'user.gettoptracks',  'toptracks',  'track'),
                )

        embed = discord.Embed(title=f'Overview ({period}) — {lfm}', color=0xD51007)
        if top_artist:
            embed.add_field(
                name='Top artist',
                value=f'**{top_artist["name"]}** — {int(top_artist["playcount"]):,} plays',
                inline=False
            )
        if top_album:
            embed.add_field(
                name='Top album',
                value=f'**{top_album["name"]}** — {top_album["artist"]["name"]} ({int(top_album["playcount"]):,} plays)',
                inline=False
            )
        if top_track:
            embed.add_field(
                name='Top track',
                value=f'**{top_track["name"]}** — {top_track["artist"]["name"]} ({int(top_track["playcount"]):,} plays)',
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['str'])
    @app_commands.describe(member='User to look up (default: you)')
    async def streak(self, ctx, member: Optional[discord.Member] = None):
        """Current listening streak (artist / album / track)."""
        user = member or ctx.author
        lfm = self._get_lfm(user)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        async with ctx.typing():
            async with aiohttp.ClientSession() as session:
                data = await self._api(session, {
                    'method': 'user.getrecenttracks',
                    'user': lfm,
                    'limit': 200
                })

        tracks = data.get('recenttracks', {}).get('track', [])
        if not tracks:
            await ctx.send(f'No recent tracks found for `{lfm}`.')
            return

        if tracks and tracks[0].get('@attr', {}).get('nowplaying') == 'true':
            tracks = tracks[1:]

        if not tracks:
            await ctx.send('Not enough history to calculate a streak.')
            return

        def calc_streak(tracks, key_fn):
            if not tracks:
                return 0, ''
            ref = key_fn(tracks[0])
            count = 0
            for t in tracks:
                if key_fn(t).lower() == ref.lower():
                    count += 1
                else:
                    break
            return count, ref

        artist_streak, artist_name = calc_streak(tracks, lambda t: t['artist']['#text'])
        album_streak,  album_name  = calc_streak(tracks, lambda t: t['album']['#text'])
        track_streak,  track_name  = calc_streak(tracks, lambda t: t['name'])

        embed = discord.Embed(title=f'Listening streak — {lfm}', color=0xD51007)
        embed.add_field(name='Artist', value=f'**{artist_name}** × {artist_streak}', inline=True)
        if album_name:
            embed.add_field(name='Album', value=f'**{album_name}** × {album_streak}', inline=True)
        embed.add_field(name='Track', value=f'**{track_name}** × {track_streak}', inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['dd', 'firstlisten'])
    @app_commands.describe(artist='Artist name (leave blank for current track\'s artist)')
    async def discoverydate(self, ctx, *, artist: str = None):
        """When did you first listen to an artist? (uses local scrobble cache)"""
        lfm = self._get_lfm(ctx.author)
        if not lfm:
            await ctx.send(self._no_lfm_msg())
            return

        async with aiohttp.ClientSession() as session:
            # Resolve artist name
            if not artist:
                t = await self._current_track(session, lfm)
                if not t:
                    await ctx.send('No recent track found. Provide an artist name.')
                    return
                artist = t['artist']['#text']
            else:
                artist = await self._resolve_artist(session, artist)

            # Decide whether to sync
            sync_state = self._get_sync_state(lfm)
            now_ts = int(time.time())
            need_sync = (
                sync_state is None or
                (now_ts - sync_state[2]) > 3600
            )

            if need_sync:
                sync_type = 'full' if sync_state is None else 'delta'
                status_msg = await ctx.send(
                    f'Syncing scrobble history for **{lfm}** ({sync_type})… this may take a while.'
                )
                last_edit = [0.0]

                async def progress_callback(pages_done, total_pages, fetched):
                    if time.time() - last_edit[0] >= 3.0:
                        last_edit[0] = time.time()
                        pct = int(pages_done / total_pages * 100)
                        try:
                            await status_msg.edit(
                                content=f'Syncing **{lfm}**… {pct}% ({pages_done}/{total_pages} pages, {fetched:,} tracks)'
                            )
                        except Exception:
                            pass

                fetched, total_cached = await self._sync_scrobbles(session, lfm, progress_callback)
                try:
                    await status_msg.edit(
                        content=f'Sync complete — {fetched:,} new scrobbles fetched ({total_cached:,} total cached).'
                    )
                except Exception:
                    pass

        # Query cache
        result = self._query_first_scrobble(lfm, artist.lower())
        if not result:
            await ctx.send(f'No scrobbles found for **{artist}** in cache for `{lfm}`.')
            return

        r_artist, r_track, r_album, r_ts = result
        artist_plays = self._count_scrobbles_for_artist(lfm, artist.lower())
        sync_state = self._get_sync_state(lfm)
        total_cached = sync_state[1] if sync_state else self._count_cached_scrobbles(lfm)
        synced_at = sync_state[2] if sync_state else 0

        dt = datetime.datetime.utcfromtimestamp(r_ts)
        date_fmt = dt.strftime('%-d %B %Y, %H:%M UTC')
        disc_ts = f'<t:{r_ts}:D>'

        embed = discord.Embed(
            title=f'Discovery date — {r_artist}',
            color=0xD51007
        )
        embed.set_author(name=lfm)
        embed.add_field(name='First listened', value=f'{disc_ts}\n{date_fmt}', inline=False)
        first_track_val = f'**{r_track}**'
        if r_album:
            first_track_val += f' on *{r_album}*'
        embed.add_field(name='First track', value=first_track_val, inline=False)
        embed.add_field(name='Total plays in cache', value=f'{artist_plays:,}', inline=True)
        synced_str = datetime.datetime.utcfromtimestamp(synced_at).strftime('%-d %b %Y %H:%M UTC') if synced_at else 'never'
        embed.set_footer(text=f'Cache last updated {synced_str} · {total_cached:,} scrobbles cached')
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    @app_commands.describe(year='Year to review (default: last year)')
    async def year(self, ctx, year: Optional[int] = None):
        """Year in review — server-wide top artists, albums, tracks and listener stats."""
        if year is None:
            year = datetime.datetime.now(datetime.timezone.utc).year - 1

        tz = datetime.timezone.utc
        start_ts = int(datetime.datetime(year, 1, 1, tzinfo=tz).timestamp())
        end_ts   = int(datetime.datetime(year + 1, 1, 1, tzinfo=tz).timestamp()) - 1

        registered = await self._guild_registered(ctx.guild)
        if not registered:
            await ctx.send('No registered Last.fm users in this server.')
            return

        lfm_names = [lfm for _, lfm in registered]
        ph = ','.join('?' * len(lfm_names))

        async with ctx.typing():
            con = sqlite3.connect(DB_PATH)

            total = con.execute(
                f'SELECT COUNT(*) FROM scrobbles WHERE lfm_username IN ({ph}) '
                f'AND scrobbled_at BETWEEN ? AND ?',
                lfm_names + [start_ts, end_ts]
            ).fetchone()[0]

            if total == 0:
                con.close()
                await ctx.send(f'No scrobbles cached for {year}. Try `.sync` first.')
                return

            top_artists = con.execute(
                f'SELECT artist, COUNT(*) as plays FROM scrobbles '
                f'WHERE lfm_username IN ({ph}) AND scrobbled_at BETWEEN ? AND ? '
                f'GROUP BY LOWER(artist) ORDER BY plays DESC LIMIT 5',
                lfm_names + [start_ts, end_ts]
            ).fetchall()

            top_albums = con.execute(
                f'SELECT album, artist, COUNT(*) as plays FROM scrobbles '
                f'WHERE lfm_username IN ({ph}) AND scrobbled_at BETWEEN ? AND ? '
                f'AND album != "" '
                f'GROUP BY LOWER(album), LOWER(artist) ORDER BY plays DESC LIMIT 5',
                lfm_names + [start_ts, end_ts]
            ).fetchall()

            top_tracks = con.execute(
                f'SELECT track, artist, COUNT(*) as plays FROM scrobbles '
                f'WHERE lfm_username IN ({ph}) AND scrobbled_at BETWEEN ? AND ? '
                f'GROUP BY LOWER(track), LOWER(artist) ORDER BY plays DESC LIMIT 5',
                lfm_names + [start_ts, end_ts]
            ).fetchall()

            top_listener = con.execute(
                f'SELECT lfm_username, COUNT(*) as plays FROM scrobbles '
                f'WHERE lfm_username IN ({ph}) AND scrobbled_at BETWEEN ? AND ? '
                f'GROUP BY lfm_username ORDER BY plays DESC LIMIT 1',
                lfm_names + [start_ts, end_ts]
            ).fetchone()

            user_top = []
            for member, lfm in registered:
                row = con.execute(
                    'SELECT artist, COUNT(*) as plays FROM scrobbles '
                    'WHERE lfm_username = ? AND scrobbled_at BETWEEN ? AND ? '
                    'GROUP BY LOWER(artist) ORDER BY plays DESC LIMIT 1',
                    (lfm, start_ts, end_ts)
                ).fetchone()
                if row:
                    user_top.append((member.display_name, row[0], row[1]))

            con.close()

        embed = discord.Embed(
            title=f'\U0001f3b5 {ctx.guild.name} \u2014 {year} in Review',
            description=f'**{total:,}** scrobbles across **{len(registered)}** listener{"s" if len(registered) != 1 else ""}',
            color=0xD51007
        )

        if top_artists:
            lines = [f'`{i+1}.` **{a}** \u2014 {p:,} plays' for i, (a, p) in enumerate(top_artists)]
            embed.add_field(name='Top Artists', value='\n'.join(lines), inline=False)

        if top_albums:
            lines = [f'`{i+1}.` **{alb}** \u2014 *{art}* \u2014 {p:,} plays' for i, (alb, art, p) in enumerate(top_albums)]
            embed.add_field(name='Top Albums', value='\n'.join(lines), inline=False)

        if top_tracks:
            lines = [f'`{i+1}.` **{t}** \u2014 *{art}* \u2014 {p:,} plays' for i, (t, art, p) in enumerate(top_tracks)]
            embed.add_field(name='Top Tracks', value='\n'.join(lines), inline=False)

        if top_listener:
            top_member = next((m for m, lfm in registered if lfm == top_listener[0]), None)
            top_name = top_member.display_name if top_member else top_listener[0]
            embed.add_field(
                name='Most Active Listener',
                value=f'**{top_name}** \u2014 {top_listener[1]:,} scrobbles',
                inline=False
            )

        if user_top:
            lines = [f'**{name}**: {artist} ({plays:,})' for name, artist, plays in user_top]
            embed.add_field(name='Personal Top Artist', value='\n'.join(lines), inline=False)

        embed.set_footer(text=f'Based on locally cached scrobbles \u00b7 Jan\u2013Dec {year}')
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  Commands: server-wide aggregates                                    #
    # ------------------------------------------------------------------ #
    async def _server_aggregate(self, ctx, method, key, subkey, title):
        registered = await self._guild_registered(ctx.guild)
        if not registered:
            await ctx.send('No registered Last.fm users in this server.')
            return

        async def fetch_top(session, lfm):
            try:
                data = await self._api(session, {
                    'method': method,
                    'user': lfm,
                    'period': 'overall',
                    'limit': 50
                })
                return data.get(key, {}).get(subkey, [])
            except Exception:
                return []

        async with ctx.typing():
            async with aiohttp.ClientSession() as session:
                all_lists = await asyncio.gather(*[fetch_top(session, lfm) for _, lfm in registered])

        aggregate = {}
        for items in all_lists:
            for item in items:
                name = item['name']
                plays = int(item.get('playcount', 0))
                aggregate[name] = aggregate.get(name, 0) + plays

        if not aggregate:
            await ctx.send('No data found.')
            return

        top = sorted(aggregate.items(), key=lambda x: x[1], reverse=True)[:10]
        lines = [f'`{i+1}.` **{name}** — {plays:,} plays'
                 for i, (name, plays) in enumerate(top)]
        embed = discord.Embed(title=title, description='\n'.join(lines), color=0xD51007)
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def serverartists(self, ctx):
        """Aggregate top artists across all server members."""
        await self._server_aggregate(
            ctx, 'user.gettopartists', 'topartists', 'artist',
            f'Server top artists — {ctx.guild.name}'
        )

    @commands.hybrid_command()
    async def serveralbums(self, ctx):
        """Aggregate top albums across all server members."""
        await self._server_aggregate(
            ctx, 'user.gettopalbums', 'topalbums', 'album',
            f'Server top albums — {ctx.guild.name}'
        )

    @commands.hybrid_command()
    async def servertracks(self, ctx):
        """Aggregate top tracks across all server members."""
        await self._server_aggregate(
            ctx, 'user.gettoptracks', 'toptracks', 'track',
            f'Server top tracks — {ctx.guild.name}'
        )

    # ------------------------------------------------------------------ #
    #  Commands: crowns                                                    #
    # ------------------------------------------------------------------ #
    @commands.hybrid_command()
    @app_commands.describe(artist='Artist name')
    async def crown(self, ctx, *, artist: str):
        """Show who holds the crown for an artist in this server."""
        existing = self._get_crown(str(ctx.guild.id), artist.strip().lower())
        if existing is None:
            await ctx.send(f'No crown holder for **{artist}** in this server yet.')
            return
        artist_display, discord_id, play_count = existing
        member = ctx.guild.get_member(int(discord_id))
        name = member.display_name if member else f'<@{discord_id}>'
        await ctx.send(f'👑 **{name}** holds the crown for **{artist_display}** with {play_count:,} plays.')

    @commands.hybrid_command()
    @app_commands.describe(member='User to look up (default: you)')
    async def crowns(self, ctx, member: Optional[discord.Member] = None):
        """List a user's crowns in this server."""
        user = member or ctx.author
        all_crowns = self._guild_crowns(str(ctx.guild.id))
        user_crowns = [(ad, pc) for ad, did, pc in all_crowns if did == str(user.id)]
        if not user_crowns:
            await ctx.send(f'**{user.display_name}** holds no crowns in this server.')
            return
        total = len(user_crowns)
        lines = [f'`{i+1}.` **{ad}** — {pc:,} plays' for i, (ad, pc) in enumerate(user_crowns[:20])]
        embed = discord.Embed(
            title=f'👑 {user.display_name}\'s crowns ({total} total)',
            description='\n'.join(lines),
            color=0xD51007
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def servercrowns(self, ctx):
        """All crown holders in this server, sorted by play count."""
        all_crowns = self._guild_crowns(str(ctx.guild.id))
        if not all_crowns:
            await ctx.send('No crowns have been awarded in this server yet.')
            return
        lines = []
        for i, (artist_display, discord_id, play_count) in enumerate(all_crowns[:15]):
            member = ctx.guild.get_member(int(discord_id))
            name = member.display_name if member else f'<@{discord_id}>'
            lines.append(f'`{i+1}.` **{artist_display}** — {name} ({play_count:,} plays)')
        embed = discord.Embed(
            title=f'Server crowns — {ctx.guild.name}',
            description='\n'.join(lines),
            color=0xD51007
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def topcrowns(self, ctx):
        """Server members ranked by number of crowns held."""
        all_crowns = self._guild_crowns(str(ctx.guild.id))
        if not all_crowns:
            await ctx.send('No crowns have been awarded in this server yet.')
            return
        counts = {}
        for _, discord_id, _ in all_crowns:
            counts[discord_id] = counts.get(discord_id, 0) + 1
        ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        lines = []
        for i, (discord_id, count) in enumerate(ranked[:15]):
            member = ctx.guild.get_member(int(discord_id))
            name = member.display_name if member else f'<@{discord_id}>'
            lines.append(f'`{i+1}.` **{name}** — {count} crown{"s" if count != 1 else ""}')
        embed = discord.Embed(
            title=f'Top crown holders — {ctx.guild.name}',
            description='\n'.join(lines),
            color=0xD51007
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(FM(bot))

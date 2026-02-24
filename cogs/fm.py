import asyncio
import sqlite3
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
        if not artist:
            lfm = self._get_lfm(ctx.author)
            if not lfm:
                await ctx.send('Provide an artist name, or set your Last.fm with `.setfm <username>`.')
                return
            async with aiohttp.ClientSession() as session:
                t = await self._current_track(session, lfm)
            if not t:
                await ctx.send('Could not get current artist. Provide an artist name.')
                return
            artist = t['artist']['#text']

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
                    artist_name, track_name = parts[0].strip(), parts[1].strip()
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
                    artist_name, album_name = parts[0].strip(), parts[1].strip()
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

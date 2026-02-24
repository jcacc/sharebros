# cogs/imdb.py

import discord
from discord.ext import commands
import requests
import yaml

def load_config(config_file='./config.yaml'):
    with open(config_file) as file:
        config = yaml.safe_load(file)
    return config

CONFIG = load_config()

TMDB_BASE = 'https://api.themoviedb.org/3'
TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p/w500'

class IMDb(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.headers = {'Authorization': f'Bearer {CONFIG["tmdb"]["read_access_token"]}'}

    @commands.command(name='imdb', help='Search for a movie or show.')
    async def imdb(self, ctx, *, query):
        result = self.search(query)
        if not result:
            await ctx.send(f'No results found for "{query}".')
            return

        media_type = result.get('media_type')
        if media_type not in ('movie', 'tv'):
            await ctx.send(f'No results found for "{query}".')
            return

        details = self.get_details(result['id'], media_type)
        if not details:
            await ctx.send('Could not fetch details for that title.')
            return

        embed = self.build_embed(details, media_type)
        await ctx.send(embed=embed)

        title = details.get('title') or details.get('name', '?')
        year = (details.get('release_date') or details.get('first_air_date') or '')[:4]
        print(f'[IMDB] {ctx.author} searched for "{query}"')
        print(f'[IMDB] ↳ returned: {title} ({year})')

    def search(self, query):
        resp = requests.get(
            f'{TMDB_BASE}/search/multi',
            params={'query': query},
            headers=self.headers,
        )
        results = resp.json().get('results', [])
        # Return first movie or tv result
        for r in results:
            if r.get('media_type') in ('movie', 'tv'):
                return r
        return None

    def get_details(self, tmdb_id, media_type):
        resp = requests.get(
            f'{TMDB_BASE}/{media_type}/{tmdb_id}',
            params={'append_to_response': 'credits'},
            headers=self.headers,
        )
        return resp.json() if resp.ok else None

    def build_embed(self, data, media_type):
        title = data.get('title') or data.get('name', 'Unknown')
        date = data.get('release_date') or data.get('first_air_date') or ''
        year = date[:4] if date else 'N/A'
        rating = data.get('vote_average')
        rating_str = f'{rating:.1f}/10' if rating else 'N/A'
        plot = data.get('overview') or 'No plot available.'
        genres = ', '.join(g['name'] for g in data.get('genres', []))
        poster_path = data.get('poster_path')
        tmdb_url = f'https://www.themoviedb.org/{media_type}/{data["id"]}'

        credits = data.get('credits', {})
        cast = ', '.join(m['name'] for m in credits.get('cast', [])[:5]) or 'N/A'

        if media_type == 'movie':
            directors = [m['name'] for m in credits.get('crew', []) if m.get('job') == 'Director']
            creator = ', '.join(directors) or 'N/A'
            creator_label = 'Director'
        else:
            creators = [c['name'] for c in data.get('created_by', [])]
            creator = ', '.join(creators) or 'N/A'
            creator_label = 'Created By'

        embed = discord.Embed(
            title=f'{title} ({year})',
            url=tmdb_url,
            color=0x01b4e4,  # TMDb blue
        )
        embed.add_field(name='Rating', value=f'⭐ {rating_str}', inline=True)
        embed.add_field(name='Genre', value=genres or 'N/A', inline=True)
        embed.add_field(name=creator_label, value=creator, inline=False)
        embed.add_field(name='Cast', value=cast, inline=False)
        embed.add_field(name='Plot', value=plot, inline=False)

        if poster_path:
            embed.set_thumbnail(url=f'{TMDB_IMAGE_BASE}{poster_path}')

        return embed

async def setup(bot):
    await bot.add_cog(IMDb(bot))

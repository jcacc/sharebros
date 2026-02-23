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

OMDB_URL = 'http://www.omdbapi.com/'

class IMDb(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = CONFIG['omdb']['api_key']

    @commands.command(name='imdb', help='Search for a movie or show on IMDb.')
    async def imdb(self, ctx, *, query):
        data = self.search(query)
        if not data or data.get('Response') == 'False':
            await ctx.send(f'No results found for "{query}".')
            return

        embed = self.build_embed(data)
        await ctx.send(embed=embed)
        print(f'[IMDB] {ctx.author} searched for "{query}"')
        print(f'[IMDB] ↳ returned: {data["Title"]} ({data["Year"]})')

    def search(self, query):
        response = requests.get(
            OMDB_URL,
            params={
                'apikey': self.api_key,
                't': query,
                'plot': 'short',
            }
        )
        return response.json()

    def build_embed(self, data):
        title = data.get('Title', 'Unknown')
        year = data.get('Year', '')
        rating = data.get('imdbRating', 'N/A')
        plot = data.get('Plot', 'No plot available.')
        poster = data.get('Poster', '')
        imdb_id = data.get('imdbID', '')
        genre = data.get('Genre', 'N/A')
        director = data.get('Director', 'N/A')
        actors = data.get('Actors', 'N/A')

        imdb_url = f'https://www.imdb.com/title/{imdb_id}/' if imdb_id else None

        embed = discord.Embed(
            title=f'{title} ({year})',
            url=imdb_url,
            color=0xf5c518,  # IMDb yellow
        )
        embed.add_field(name='Rating', value=f'⭐ {rating}/10', inline=True)
        embed.add_field(name='Genre', value=genre, inline=True)
        embed.add_field(name='Director', value=director, inline=False)
        embed.add_field(name='Cast', value=actors, inline=False)
        embed.add_field(name='Plot', value=plot, inline=False)

        if poster and poster != 'N/A':
            embed.set_thumbnail(url=poster)

        return embed

async def setup(bot):
    await bot.add_cog(IMDb(bot))

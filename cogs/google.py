import discord
from discord.ext import commands
import requests
import json
import yaml

def load_config(config_file = './config.yaml'):
    with open(config_file) as file:
        config = yaml.safe_load(file)
    return config

CONFIG = load_config()

class Google(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def gis(self, ctx, *, query):
        images = await self.search(query)
        result = self.strip_pinterest(images)
        await ctx.send(result)
        print(f'[GIS] {ctx.author} searched for \"{query}\"')
        print(f'[GIS] ↳ returned: {result}')


    async def search(self, query):
        api_url = 'https://www.googleapis.com/customsearch/v1?'
        api_key = CONFIG['imagesearch']['google_api_key']
        search_engine_id = CONFIG['imagesearch']['search_engine_id']

        query = requests.get(
            api_url,
            params={
                'key': api_key,
                'cx': search_engine_id,
                'searchType': 'image',
                'num': 10,
                'q': query
            }
        )
        images = query.json()
        return images['items']

    def strip_pinterest(self, results):
        for result in results:
            if 'pinimg' not in result['link']:
                return result['link']

def setup(bot):
    bot.add_cog(Google(bot))
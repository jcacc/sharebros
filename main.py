# main.py

import discord
from discord.ext import commands
import os

TOKEN = os.getenv('DISCORD_BOT_TOKEN')

initial_extensions = [
    'cogs.greetings',
    'cogs.roll',
    'cogs.echo',
    'cogs.vampire',
    'cogs.crazy',
    'cogs.itysl',
    'cogs.bible',
    'cogs.google',
    'cogs.trump',
    'cogs.detroiters',
    'cogs.deadwood',
    'cogs.fixembed',
]

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

class ShareBro(commands.Bot):
    async def setup_hook(self):
        for extension in initial_extensions:
            try:
                await self.load_extension(extension)
                print(f'[BOT] Cog loaded successfully: {extension}')
            except Exception as e:
                print(f'[BOT] Cog "{extension}" failed to load. Error: {e}')

bot = ShareBro(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')

bot.run(TOKEN)

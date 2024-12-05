# main.py

import discord
from discord.ext import commands
import os

# Get the bot's token from the system environment variables
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Set up the bot with a command prefix, e.g., '!'
intents = discord.Intents.default()
intents.messages = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Event handler for when the bot is ready
@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')

# Load cogs
initial_extensions = [
    'cogs.greetings',
    'cogs.roll',
    'cogs.echo',
]

if __name__ == '__main__':
    for extension in initial_extensions:
        bot.load_extension(extension)

# Run the bot with the specified token
bot.run(TOKEN)

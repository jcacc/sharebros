# cogs/vampire.py

import discord
import random
from discord.ext import commands

class Vampire(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='vampire', help='Pulls a random line from a text file and repeats it back.')
    async def vampire(self, ctx):
        try:
            with open('/home/jca/dev/python/sharebro/cogs/vampire_flow.txt', 'r') as file:
                lines = file.readlines()
                if lines:
                    quote = random.choice(lines).strip()
                    await ctx.send(quote)
                else:
                    await ctx.send('The file is empty. Please add some quotes.')
        except FileNotFoundError:
            await ctx.send('The quotes file could not be found. Make sure "vampire_flow.txt" is in the correct directory.')

# Required function to add this cog to the bot (Synchronous Version)
def setup(bot):
    bot.add_cog(Vampire(bot))

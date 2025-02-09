# cogs/detroiters.py

import discord
import random
from discord.ext import commands

class detroiters(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='detroiters', help='detroiters flow')
    async def detroiters(self, ctx, word: str = None):
        try:
            with open('/home/jca/dev/python/sharebro/cogs/detroiters.txt', 'r') as file:
                lines = file.readlines()

                # Filter the lines if a word is provided
                if word:
                    filtered_lines = [line for line in lines if word.lower() in line.lower()]
                else:
                    filtered_lines = lines

                # Choose a random line if there are matching results
                if filtered_lines:
                    quote = random.choice(filtered_lines).strip()
                    await ctx.send(quote)
                else:
                    await ctx.send(f'No quotes found containing the word "{word}".')

        except FileNotFoundError:
            await ctx.send('The quotes file could not be found. Make sure "detroiters.txt" is in the correct directory.')

# Required function to add this cog to the bot (Synchronous Version)
def setup(bot):
    bot.add_cog(detroiters(bot))

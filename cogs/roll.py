# cogs/roll.py

import random
from discord.ext import commands

class Roll(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='roll', help='Rolls a dice. Example: !roll 6')
    async def roll(self, ctx, number_of_sides: int):
        if number_of_sides > 1:
            result = random.randint(1, number_of_sides)
            await ctx.send(f'You rolled a {result} (1-{number_of_sides})!')
        else:
            await ctx.send('Please provide a valid number of sides greater than 1.')

def setup(bot):
    bot.add_cog(Roll(bot))

# cogs/greetings.py

import discord
from discord.ext import commands

class Greetings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='hello', help='Responds with a friendly greeting!')
    async def hello(self, ctx):
        await ctx.send(f'Hello, {ctx.author.name}! How can I assist you today?')

async def setup(bot):
    await bot.add_cog(Greetings(bot))

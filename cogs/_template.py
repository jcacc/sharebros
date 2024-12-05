# cogs/template.py
# replace TemplateCog and template_command with your specific cog and command names.

import discord
from discord.ext import commands

class TemplateCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Example command within this cog
    @commands.command(name='template_command', help='This is a template command.')
    async def template_command(self, ctx):
        await ctx.send('This is a response from the template command!')

# Required function to add this cog to the bot
async def setup(bot):
    await bot.add_cog(TemplateCog(bot))

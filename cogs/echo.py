# cogs/echo.py

import discord
from discord.ext import commands

class Echo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='echo', help='Repeats your message.')
    async def echo(self, ctx, *, message: str):
        await ctx.send(message)

    @commands.Cog.listener()
    async def on_message(self, message):
        # Check if the bot itself is not the author and if it is a direct message
        if message.author == self.bot.user or not isinstance(message.channel, discord.DMChannel):
            return

        # Echo the message back in private chat
        await message.channel.send(f'Echo: {message.content}')

        # Send the message to a specific channel in the server
        guild = self.bot.guilds[0]  # Assuming the bot is only connected to one server
        channel = discord.utils.get(guild.text_channels, name='general')  # Replace 'general' with your channel name
        if channel:
            await channel.send(f'{message.content}')

# Required function to add this cog to the bot (Synchronous Version)
def setup(bot):
    bot.add_cog(Echo(bot))
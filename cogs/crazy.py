# cogs/crazy_emoji.py

import discord
import random
from discord.ext import commands

class CrazyEmoji(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages from the bot itself
        if message.author == self.bot.user:
            return

        # Check if the message contains the word "crazy"
        if "crazy" in message.content.lower():
            responses = [
                "Purple monkey dishwasher!",
                "Flying spaghetti monster!",
                "You're totally out of your mind!",
                "That's just wild!",
                "Absolutely bonkers!",
                "Banana hammock surprise!",
                "You're crazy, but I like it!",
                "That's random!",
                "Flibberty gibbet!"
            ]
            response = f'🤪 {random.choice(responses)}'
            await message.channel.send(response)

# Required function to add this cog to the bot (Synchronous Version)
def setup(bot):
    bot.add_cog(CrazyEmoji(bot))

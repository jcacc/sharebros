import re
import discord
from discord.ext import commands

PROXY_MAP = {
    r'https?://(?:www\.)?twitter\.com': 'https://fxtwitter.com',
    r'https?://(?:www\.)?x\.com': 'https://fixupx.com',
    r'https?://(?:www\.)?instagram\.com': 'https://ddinstagram.com',
    r'https?://(?:www\.)?tiktok\.com': 'https://fxtiktok.com',
    r'https?://(?:www\.)?reddit\.com': 'https://vxreddit.com',
}

class FixEmbed(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        fixed = message.content
        for pattern, replacement in PROXY_MAP.items():
            fixed = re.sub(pattern, replacement, fixed)

        if fixed != message.content:
            urls = re.findall(r'https?://\S+', fixed)
            await message.reply(' '.join(urls), mention_author=False)
            print(f'[FIXEMBED] fixed link for {message.author}')

async def setup(bot):
    await bot.add_cog(FixEmbed(bot))

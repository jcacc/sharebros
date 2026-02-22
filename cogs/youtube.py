import discord
from discord.ext import commands
import asyncio
import yt_dlp


YDL_OPTS = {
    'quiet': True,
    'skip_download': True,
    'extract_flat': True,
    'no_warnings': True,
}


def _search(query):
    """Blocking yt-dlp search — run in executor."""
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(f'ytsearch5:{query}', download=False)
    return info.get('entries', [])


class YouTube(commands.Cog):
    @commands.hybrid_command(aliases=['yt'])
    async def youtube(self, ctx, *, query: str):
        """Search YouTube and return the top 5 results."""
        async with ctx.typing():
            loop = asyncio.get_event_loop()
            entries = await loop.run_in_executor(None, _search, query)

        if not entries:
            await ctx.send(f'No results found for `{query}`.')
            return

        lines = []
        for i, e in enumerate(entries[:5], 1):
            title    = e.get('title', 'Unknown')
            url      = e.get('url') or f"https://www.youtube.com/watch?v={e.get('id', '')}"
            channel  = e.get('channel') or e.get('uploader') or ''
            duration = e.get('duration')
            dur_str  = f'{int(duration) // 60}:{int(duration) % 60:02d}' if duration else ''
            meta     = ' · '.join(filter(None, [channel, dur_str]))
            lines.append(f'`{i}.` **[{title}]({url})**' + (f'\n    {meta}' if meta else ''))

        embed = discord.Embed(
            title=f'YouTube results for "{query}"',
            description='\n'.join(lines),
            color=0xFF0000
        )
        # Thumbnail from first result
        thumb = entries[0].get('thumbnail') or entries[0].get('thumbnails', [{}])[0].get('url')
        if thumb:
            embed.set_thumbnail(url=thumb)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(YouTube())

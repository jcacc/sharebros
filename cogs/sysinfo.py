import discord
from discord.ext import commands
import psutil
import datetime


class SysInfo(commands.Cog):
    @commands.hybrid_command(aliases=['top'])
    async def sysinfo(self, ctx):
        """Show a snapshot of system resource usage."""
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        uptime = datetime.datetime.now() - datetime.datetime.fromtimestamp(psutil.boot_time())
        uptime_str = str(uptime).split('.')[0]  # strip microseconds

        # Top 5 processes by CPU
        procs = sorted(
            psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']),
            key=lambda p: p.info['cpu_percent'] or 0,
            reverse=True
        )[:5]
        proc_lines = [
            f'`{p.info["pid"]:>6}` {p.info["name"][:20]:<20} CPU {p.info["cpu_percent"] or 0:.1f}%  MEM {p.info["memory_percent"] or 0:.1f}%'
            for p in procs
        ]

        embed = discord.Embed(title='🖥 System info — lampPost', color=0x2ecc71)
        embed.add_field(name='CPU', value=f'{cpu:.1f}%', inline=True)
        embed.add_field(
            name='Memory',
            value=f'{mem.used / 1024**3:.1f} / {mem.total / 1024**3:.1f} GB ({mem.percent:.0f}%)',
            inline=True
        )
        embed.add_field(
            name='Disk',
            value=f'{disk.used / 1024**3:.1f} / {disk.total / 1024**3:.1f} GB ({disk.percent:.0f}%)',
            inline=True
        )
        embed.add_field(name='Uptime', value=uptime_str, inline=True)
        embed.add_field(name='Top processes', value='```\n' + '\n'.join(proc_lines) + '\n```', inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(SysInfo())

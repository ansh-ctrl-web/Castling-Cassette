import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
from collections import deque
import re
import requests
import json
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
import random
from keep_alive import keep_alive

# Load environment variables
load_dotenv()

# Bot configuration
intents = nextcord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Platform support functions
class PlatformHandler:
    @staticmethod
    def is_spotify_url(url):
        return 'spotify.com' in url
    
    @staticmethod
    def is_apple_music_url(url):
        return 'music.apple.com' in url
    
    @staticmethod
    def is_soundcloud_url(url):
        return 'soundcloud.com' in url
    
    @staticmethod
    def is_youtube_url(url):
        return 'youtube.com' in url or 'youtu.be' in url
    
    @staticmethod
    async def get_spotify_track_info(url):
        """Extract track info from Spotify URL"""
        try:
            if '/track/' in url:
                track_id = url.split('/track/')[1].split('?')[0]
            else:
                return None
            return {
                'platform': 'Spotify',
                'search_query': None,
                'url': url
            }
        except Exception as e:
            print(f"Error processing Spotify URL: {e}")
            return None
    
    @staticmethod
    async def get_apple_music_info(url):
        """Extract track info from Apple Music URL"""
        try:
            return {
                'platform': 'Apple Music',
                'search_query': None,
                'url': url
            }
        except Exception as e:
            print(f"Error processing Apple Music URL: {e}")
            return None
    
    @staticmethod
    async def search_youtube_for_track(artist, title):
        """Search YouTube for a track by artist and title"""
        search_query = f"{artist} {title}".strip()
        return f"ytsearch:{search_query}"

# Enhanced yt-dlp options with more platform support
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': False,
    'extractors': ['youtube', 'soundcloud'],
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(nextcord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')
        self.thumbnail = data.get('thumbnail')
        self.uploader = data.get('uploader', '')
        self.view_count = data.get('view_count', 0)

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            return data['entries']
        
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(nextcord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
    
    @classmethod
    async def create_source(cls, data, *, loop=None, stream=False):
        """Create a single source from data dict"""
        loop = loop or asyncio.get_event_loop()
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(nextcord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class MusicQueue:
    def __init__(self):
        self.queue = deque()
        self.current = None
        self.loop = False
        self.loop_queue = False
        self.autoplay = True  # NEW: Autoplay enabled by default
        self.history = deque(maxlen=50)  # NEW: Keep track of played songs

    def add_song(self, song):
        self.queue.append(song)

    def get_next(self):
        if self.loop and self.current:
            return self.current
        if self.queue:
            self.current = self.queue.popleft()
            if self.loop_queue:
                self.queue.append(self.current)
            return self.current
        if self.autoplay and self.current:
            return None  # This will trigger autoplay in play_next
        self.current = None
        return None

    def skip(self):
        if self.loop_queue and self.current and not self.loop:
            self.queue.append(self.current)
        return self.get_next()

    def clear(self):
        self.queue.clear()
        self.current = None

    def shuffle(self):
        import random
        queue_list = list(self.queue)
        random.shuffle(queue_list)
        self.queue = deque(queue_list)
    
    def add_to_history(self, song):
        """NEW: Add song to history"""
        if song and song not in self.history:
            self.history.append(song)

# Global music queues for each guild
music_queues = {}

def get_queue(guild_id):
    if guild_id not in music_queues:
        music_queues[guild_id] = MusicQueue()
    return music_queues[guild_id]

# NEW: Smart autoplay function
async def get_related_song(current_song):
    """Get a related song based on the current song using YouTube search"""
    try:
        if not current_song or not current_song.title:
            return None
        title = current_song.title.lower()
        stopwords = ['official', 'video', 'audio', 'lyrics', 'hd', 'hq', 'music', 'song', 'ft', 'feat', 'featuring']
        words = re.findall(r'\b\w+\b', title)
        keywords = [word for word in words if word not in stopwords and len(word) > 2]
        if not keywords:
            return None
        search_terms = keywords[:3]
        search_query = f"ytsearch:{' '.join(search_terms)} music"
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ytdl.extract_info(search_query, download=False)
        )
        if 'entries' in data and data['entries']:
            entries = [entry for entry in data['entries'][:10] if entry and entry.get('title') != current_song.title]
            if entries:
                selected = random.choice(entries)
                return await YTDLSource.create_source(selected, loop=asyncio.get_event_loop(), stream=True)
        return None
    except Exception as e:
        print(f"Error getting related song: {e}")
        return None

@bot.event
async def on_ready():
    print(f'🎵 {bot.user} (Castling Cassette) has connected to Discord!')
    await bot.change_presence(activity=nextcord.Game(name="♛ !help for commands"))

@bot.command(name='join', help='Joins a voice channel')
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send("You are not connected to a voice channel!")
        return
    channel = ctx.message.author.voice.channel
    if ctx.voice_client is not None:
        if ctx.voice_client.channel.id == channel.id:
            await ctx.send("♛ I'm already in this voice channel!")
        else:
            await ctx.voice_client.move_to(channel)
            await ctx.send(f"♛ Moved to {channel}")
    else:
        await channel.connect()
        await ctx.send(f"♛ Connected to {channel}")

@bot.command(name='leave', help='Leaves the voice channel')
async def leave(ctx):
    if ctx.voice_client:
        queue = get_queue(ctx.guild.id)
        queue.clear()
        await ctx.voice_client.disconnect()
        await ctx.send("♔ Disconnected from voice channel")
    else:
        await ctx.send("I'm not in a voice channel!")

@bot.command(name='play', help='Plays music from YouTube, SoundCloud, Spotify, Apple Music, and more')
async def play(ctx, *, url):
    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("You need to be in a voice channel!")
            return
    try:
        async with ctx.typing():
            platform_handler = PlatformHandler()
            queue = get_queue(ctx.guild.id)
            if platform_handler.is_spotify_url(url):
                embed = nextcord.Embed(
                    title="⚠️ Spotify Support",
                    description="Direct Spotify playback isn't supported due to DRM restrictions.\n"
                               "Please provide the song name instead, and I'll find it on YouTube!",
                    color=0xff6b35
                )
                embed.add_field(name="Tip", value="Try: `!play artist - song name` or copy the song title", inline=False)
                await ctx.send(embed=embed)
                return
            elif platform_handler.is_apple_music_url(url):
                embed = nextcord.Embed(
                    title="⚠️ Apple Music Support",
                    description="Direct Apple Music playback isn't supported due to DRM restrictions.\n"
                               "Please provide the song name instead, and I'll find it on YouTube!",
                    color=0xff6b35
                )
                embed.add_field(name="Tip", value="Try: `!play artist - song name` or copy the song title", inline=False)
                await ctx.send(embed=embed)
                return
            elif platform_handler.is_soundcloud_url(url) or platform_handler.is_youtube_url(url) or not url.startswith('http'):
                result = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
                if isinstance(result, list):
                    added_count = 0
                    for entry in result:
                        if entry:
                            player = await YTDLSource.create_source(entry, loop=bot.loop, stream=True)
                            queue.add_song(player)
                            added_count += 1
                    platform = "SoundCloud" if platform_handler.is_soundcloud_url(url) else "YouTube"
                    embed = nextcord.Embed(
                        title=f"{platform} Playlist Added",
                        description=f"Added **{added_count}** songs to the queue",
                        color=0x00ff00
                    )
                    embed.add_field(name="Songs in queue", value=len(queue.queue), inline=True)
                    await ctx.send(embed=embed)
                else:
                    queue.add_song(result)
                    platform_emoji = "🎵"
                    if platform_handler.is_soundcloud_url(url):
                        platform_emoji = "🔊"
                    elif platform_handler.is_youtube_url(url):
                        platform_emoji = "📺"
                    embed = nextcord.Embed(
                        title=f"{platform_emoji} Added to Queue",
                        description=f"**{result.title}**",
                        color=0x00ff00
                    )
                    if result.thumbnail:
                        embed.set_thumbnail(url=result.thumbnail)
                    embed.add_field(name="Position in queue", value=len(queue.queue), inline=True)
                    await ctx.send(embed=embed)
            else:
                embed = nextcord.Embed(
                    title="❌ Unsupported Platform",
                    description="This platform is not supported. Try:\n"
                               "• YouTube URLs or search terms\n"
                               "• SoundCloud URLs\n"
                               "• Song names (I'll search YouTube)",
                    color=0xff0000
                )
                await ctx.send(embed=embed)
                return
            if not ctx.voice_client.is_playing():
                await play_next(ctx)
    except Exception as e:
        error_embed = nextcord.Embed(
            title="❌ Error",
            description=f"An error occurred: {str(e)}",
            color=0xff0000
        )
        await ctx.send(embed=error_embed)

# UPDATED: Enhanced play_next with autoplay and spam fix
async def play_next(ctx):
    queue = get_queue(ctx.guild.id)
    player = queue.get_next()
    if not player and queue.autoplay and queue.current:
        try:
            print("Attempting autoplay...")
            queue.add_to_history(queue.current)
            related_song = await get_related_song(queue.current)
            if related_song:
                player = related_song
                queue.current = player
                embed = nextcord.Embed(
                    title="🎲 Autoplay",
                    description=f"Playing related song: **{player.title}**",
                    color=0x9932cc
                )
                if player.thumbnail:
                    embed.set_thumbnail(url=player.thumbnail)
                embed.set_footer(text="Use !autoplay off to disable autoplay")
                await ctx.send(embed=embed)
            else:
                print("No related song found for autoplay.")
        except Exception as e:
            print(f"Autoplay error: {e}")
    if player and not ctx.voice_client.is_playing():
        def after_playing(error):
            if error:
                print(f'Player error: {error}')
            if not ctx.voice_client.is_playing():
                coro = play_next(ctx)
                fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
                try:
                    fut.result()
                except:
                    pass
        ctx.voice_client.play(player, after=after_playing)
        if len(queue.queue) > 0 or not hasattr(ctx, '_autoplay_notified'):
            embed = nextcord.Embed(
                title="♛ Now Playing",
                description=f"**{player.title}**",
                color=0x0099ff
            )
            if player.thumbnail:
                embed.set_thumbnail(url=player.thumbnail)
            if player.duration:
                minutes = player.duration // 60
                seconds = player.duration % 60
                embed.add_field(name="Duration", value=f"{minutes:02d}:{seconds:02d}", inline=True)
            await ctx.send(embed=embed)
            if not len(queue.queue) > 0:
                ctx._autoplay_notified = True

# NEW: Autoplay command
@bot.command(name='autoplay', help='Toggle autoplay on/off')
async def toggle_autoplay(ctx, setting=None):
    queue = get_queue(ctx.guild.id)
    if setting is None:
        queue.autoplay = not queue.autoplay
    elif setting.lower() in ['on', 'true', '1', 'yes']:
        queue.autoplay = True
    elif setting.lower() in ['off', 'false', '0', 'no']:
        queue.autoplay = False
    else:
        await ctx.send("❌ Use: `!autoplay on` or `!autoplay off`")
        return
    status = "enabled" if queue.autoplay else "disabled"
    embed = nextcord.Embed(
        title=f"🎲 Autoplay {status.title()}",
        description=f"Autoplay is now **{status}**",
        color=0x00ff00 if queue.autoplay else 0xff6b35
    )
    if queue.autoplay:
        embed.add_field(
            name="How it works",
            value="When the queue is empty, I'll automatically play related songs based on what you were listening to!",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name='pause', help='Pauses the current song')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused")
    else:
        await ctx.send("Nothing is playing!")

@bot.command(name='resume', help='Resumes the current song')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed")
    else:
        await ctx.send("Nothing is paused!")

@bot.command(name='skip', help='Skips the current song')
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Skipped")
    else:
        await ctx.send("Nothing is playing!")

@bot.command(name='stop', help='Stops music and clears the queue')
async def stop(ctx):
    if ctx.voice_client:
        queue = get_queue(ctx.guild.id)
        queue.clear()
        ctx.voice_client.stop()
        await ctx.send("⏹️ Stopped and cleared queue")
    else:
        await ctx.send("Nothing is playing!")

# UPDATED: Enhanced queue display
@bot.command(name='queue', help='Shows the current queue')
async def show_queue(ctx):
    queue = get_queue(ctx.guild.id)
    if not queue.queue and not queue.current:
        embed = nextcord.Embed(
            title="📋 Queue is empty",
            description="Add some songs with `!play <song>`",
            color=0x9932cc
        )
        if queue.autoplay:
            embed.add_field(
                name="🎲 Autoplay Enabled",
                value="I'll play related songs when the queue is empty!",
                inline=False
            )
        await ctx.send(embed=embed)
        return
    embed = nextcord.Embed(title="📋 Music Queue", color=0x9932cc)
    if queue.current:
        embed.add_field(
            name="♛ Now Playing",
            value=f"**{queue.current.title}**",
            inline=False
        )
    if queue.queue:
        queue_list = []
        for i, song in enumerate(list(queue.queue)[:10], 1):
            queue_list.append(f"{i}. {song.title}")
        embed.add_field(
            name=f"📝 Up Next ({len(queue.queue)} songs)",
            value="\n".join(queue_list),
            inline=False
        )
        if len(queue.queue) > 10:
            embed.add_field(
                name="",
                value=f"... and {len(queue.queue) - 10} more songs",
                inline=False
            )
    settings = []
    if queue.loop:
        settings.append("🔂 Loop Song")
    if queue.loop_queue:
        settings.append("🔁 Loop Queue")
    if queue.autoplay:
        settings.append("🎲 Autoplay")
    if settings:
        embed.add_field(
            name="⚙️ Settings",
            value=" • ".join(settings),
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name='clear', help='Clears the queue')
async def clear_queue(ctx):
    queue = get_queue(ctx.guild.id)
    queue.clear()
    await ctx.send("🗑️ Queue cleared")

@bot.command(name='shuffle', help='Shuffles the queue')
async def shuffle(ctx):
    queue = get_queue(ctx.guild.id)
    if queue.queue:
        queue.shuffle()
        await ctx.send("🔀 Queue shuffled")
    else:
        await ctx.send("Queue is empty!")

@bot.command(name='loop', help='Toggles loop for current song')
async def loop(ctx):
    queue = get_queue(ctx.guild.id)
    queue.loop = not queue.loop
    status = "enabled" if queue.loop else "disabled"
    await ctx.send(f"🔂 Loop {status}")

@bot.command(name='loopqueue', help='Toggles loop for entire queue')
async def loop_queue(ctx):
    queue = get_queue(ctx.guild.id)
    queue.loop_queue = not queue.loop_queue
    status = "enabled" if queue.loop_queue else "disabled"
    await ctx.send(f"🔁 Queue loop {status}")

@bot.command(name='volume', help='Changes the volume (0-100)')
async def volume(ctx, volume: int):
    if ctx.voice_client is None:
        return await ctx.send("Not connected to a voice channel.")
    if not 0 <= volume <= 100:
        return await ctx.send("Volume must be between 0 and 100")
    ctx.voice_client.source.volume = volume / 100
    await ctx.send(f"🔊 Volume set to {volume}%")

# UPDATED: Now playing command with better display
@bot.command(name='nowplaying', aliases=['np'], help='Shows current song info')
async def now_playing(ctx):
    queue = get_queue(ctx.guild.id)
    if not queue.current:
        await ctx.send("Nothing is playing!")
        return
    embed = nextcord.Embed(
        title="♛ Now Playing",
        description=f"**{queue.current.title}**",
        color=0x0099ff
    )
    if queue.current.thumbnail:
        embed.set_thumbnail(url=queue.current.thumbnail)
    await ctx.send(embed=embed)

@bot.command(name='search', help='Search and play from multiple platforms')
async def search_play(ctx, platform: str, *, query):
    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("You need to be in a voice channel!")
            return
    platform = platform.lower()
    try:
        async with ctx.typing():
            if platform in ['youtube', 'yt']:
                search_query = f"ytsearch:{query}"
            elif platform in ['soundcloud', 'sc']:
                search_query = f"scsearch:{query}"
            elif platform in ['spotify', 'sp']:
                await ctx.send("🎵 Searching YouTube for Spotify track...")
                search_query = f"ytsearch:{query}"
            else:
                await ctx.send("❌ Supported platforms: youtube, soundcloud, spotify")
                return
            result = await YTDLSource.from_url(search_query, loop=bot.loop, stream=True)
            queue = get_queue(ctx.guild.id)
            queue.add_song(result)
            platform_emojis = {
                'youtube': '📺', 'yt': '📺',
                'soundcloud': '🔊', 'sc': '🔊',
                'spotify': '🎵', 'sp': '🎵'
            }
            embed = nextcord.Embed(
                title=f"{platform_emojis.get(platform, '🎵')} Found and Added",
                description=f"**{result.title}**",
                color=0x00ff00
            )
            if result.thumbnail:
                embed.set_thumbnail(url=result.thumbnail)
            embed.add_field(name="Platform", value=platform.title(), inline=True)
            embed.add_field(name="Position in queue", value=len(queue.queue), inline=True)
            await ctx.send(embed=embed)
            if not ctx.voice_client.is_playing():
                await play_next(ctx)
    except Exception as e:
        await ctx.send(f"❌ Search failed: {str(e)}")

@bot.command(name='platforms', help='Show supported platforms')
async def show_platforms(ctx):
    embed = nextcord.Embed(
        title="🎵 Supported Platforms",
        color=0x9932cc
    )
    embed.add_field(
        name="✅ Direct Playback",
        value="📺 **YouTube** - Full support (URLs, playlists, search)\n"
              "🔊 **SoundCloud** - Full support (URLs, playlists, search)",
        inline=False
    )
    embed.add_field(
        name="⚠️ Search Only (DRM Protected)",
        value="🎵 **Spotify** - Provide song names, I'll find on YouTube\n"
              "🍎 **Apple Music** - Provide song names, I'll find on YouTube\n"
              "🎼 **Other platforms** - Provide song names for YouTube search",
        inline=False
    )
    embed.add_field(
        name="💡 Usage Tips",
        value="• Use URLs for direct playback (YouTube/SoundCloud)\n"
              "• Use song names for other platforms\n"
              "• Try: `!search spotify song name`\n"
              "• Or just: `!play artist - song title`",
        inline=False
    )
    embed.set_footer(text="Spotify/Apple Music use DRM protection, so we search YouTube instead!")
    await ctx.send(embed=embed)

# Error handling
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument! Check `!help` for command usage.")
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send("Command not found! Use `!help` to see available commands.")
    else:
        await ctx.send(f"An error occurred: {str(error)}")

# Run the bot
if __name__ == "__main__":
    # Start the keep-alive server for Render deployment
    keep_alive()
    
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN not found in environment variables!")
        print("Please set DISCORD_TOKEN in your environment or .env file")
        exit(1)
    
    print("🎵 Starting Castling Cassette Bot...")
    bot.run(TOKEN)

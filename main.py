import ctypes
import discord
from discord.ext import commands
import bot_config
import race_manager
import bot_commands  # updated import for new folder name

# === Bot Setup ===
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# === Set console window title ===
ctypes.windll.kernel32.SetConsoleTitleW("FFIVALLRace Bot")

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    race_manager.load_races()
    race_manager.load_users()

    # Register all slash commands
    bot_commands.register(bot)

    # Start cleanup loop
    race_manager.cleanup_inactive_races.start(bot)

    print("✅ All slash commands registered & cleanup started!")

@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return

    # Reset last activity for race channels
    channel_id = str(message.channel.id)
    if channel_id in race_manager.races:
        race_manager.last_activity[int(channel_id)] = discord.utils.utcnow()
        print(f"⏱️ Activity detected in race channel {message.channel.name}, timer reset.")

    await bot.process_commands(message)  # required for ! commands if you add them later

# === Run Bot ===
bot.run(bot_config.TOKEN)

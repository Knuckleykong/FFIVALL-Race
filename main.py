import ctypes
import discord
from discord.ext import commands
import bot_config
import race_manager
import bot_commands          # race commands package
import bot_commands.user_commands as user_commands  # NEW: user commands
from bot_commands.race_commands import register_views  # Persistent Join/Watch buttons

# === Bot Setup ===
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# === Set console window title ===
ctypes.windll.kernel32.SetConsoleTitleW("FFIVALLRace Bot")

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

    # --- Configure file paths (IMPORTANT for persistence) ---
    race_manager.configure_files(bot_config.DATA_FILE, bot_config.USERS_FILE)

    # --- Load persistent data ---
    race_manager.load_races()
    race_manager.load_users()
    race_manager.load_last_activity()

    # --- Register slash commands ---
    bot_commands.register(bot)   # Race-related commands
    user_commands.register(bot)  # User/preset commands

    # --- Register persistent views (Join/Watch buttons) ---
    register_views(bot)

    # --- Sync commands globally (or use guild sync for dev speed) ---
    await bot.tree.sync()

    # --- Resume pending race cleanup timers ---
    await race_manager.resume_cleanup_on_startup(bot)

    print("✅ All slash commands registered & persistent cleanup timers resumed!")

@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return

    # Reset activity timer for race channels
    channel_id = str(message.channel.id)
    if channel_id in race_manager.races:
        race_manager.last_activity[channel_id] = discord.utils.utcnow()
        race_manager.save_last_activity()
        print(f"⏱️ Activity detected in race channel {message.channel.name}, timer reset.")

    await bot.process_commands(message)

# === Run Bot ===
bot.run(bot_config.TOKEN)

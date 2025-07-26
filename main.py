import ctypes
import discord
from discord.ext import tasks
import bot_config
import race_manager
import commands  # this loads our new init file

bot = discord.Client(intents=discord.Intents.all())
bot.tree = discord.app_commands.CommandTree(bot)

# === Set console window title ===
ctypes.windll.kernel32.SetConsoleTitleW("FFIVALLRace Bot")

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    race_manager.load_races()
    race_manager.load_users()
    commands.register(bot)  # <---- Register all commands in one call
    print("✅ All slash commands registered!")

bot.run(bot_config.TOKEN)


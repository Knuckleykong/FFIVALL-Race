import os
from dotenv import load_dotenv
import discord

load_dotenv(r"C:\Users\Administrator\Desktop\FIVALLRACE\FFIVALLRace.env")

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", 0))
RACE_ALERT_ROLE_ID = int(os.getenv("RACE_ALERT_ROLE_ID", 0))
RACE_CATEGORY_ID = int(os.getenv("RACE_CATEGORY_ID", 0))
API_KEY = os.getenv("FF4FE_API_KEY")
FF6WC_API_KEY = os.getenv("FF6WC_API_KEY")

DATA_FILE = os.getenv("RACE_DATA_FILE", "races.json").strip('"')
USERS_FILE = os.getenv("USERS_FILE", "users.json").strip('"')

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

PRESET_FILES = {
    "FF4FE": os.getenv("FF4FE_PRESETS_FILE"),
    "FF1R": os.getenv("FF1R_PRESETS_FILE"),
    "FF5CD": os.getenv("FF5CD_PRESETS_FILE"),
    "FFMQR": os.getenv("FFMQR_PRESETS_FILE"),
    "FF6WC": os.getenv("FF6WC_PRESETS_FILE"),
}
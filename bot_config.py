import os
from dotenv import load_dotenv

# === Load .env file ===
load_dotenv(r"path_to_file")

# === Discord Bot Token ===
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# === Channel & Role IDs ===
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", 0))
RACE_ALERT_ROLE_ID = int(os.getenv("RACE_ALERT_ROLE_ID", 0))
RACE_CATEGORY_ID = int(os.getenv("RACE_CATEGORY_ID", 0))

# === Race Data Files ===
DATA_FILE = os.getenv("RACE_DATA_FILE")
USERS_FILE = os.getenv("USERS_FILE")
LAST_ACTIVITY_FILE = os.getenv("LAST_ACTIVITY_FILE", "last_activity.json")

# === Preset JSON file locations ===
FF4FE_PRESETS_FILE = os.getenv("FF4FE_PRESETS_FILE")
FF1R_PRESETS_FILE = os.getenv("FF1R_PRESETS_FILE")
FF5CD_PRESETS_FILE = os.getenv("FF5CD_PRESETS_FILE")
FFMQR_PRESETS_FILE = os.getenv("FFMQR_PRESETS_FILE")
FF6WC_PRESETS_FILE = os.getenv("FF6WC_PRESETS_FILE")

# === API Keys ===
FF4FE_API_KEY = os.getenv("FF4FE_API_KEY")
FF6WC_API_KEY = os.getenv("FF6WC_API_KEY")  # optional

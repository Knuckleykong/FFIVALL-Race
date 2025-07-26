import json
import os
from datetime import datetime, timezone
from discord.ext import tasks

# === Globals ===
races = {}
users = {}
last_activity = {}

# === File paths (set at runtime from main.py) ===
DATA_FILE = None
USERS_FILE = None
LAST_ACTIVITY_FILE = "last_activity.json"

def configure_files(data_file, users_file, last_activity_file=None):
    global DATA_FILE, USERS_FILE, LAST_ACTIVITY_FILE
    DATA_FILE = data_file
    USERS_FILE = users_file
    if last_activity_file:
        LAST_ACTIVITY_FILE = last_activity_file

# === Race Data Persistence ===
def load_races():
    if DATA_FILE and os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            races.update(json.load(f))

def save_races():
    if DATA_FILE:
        with open(DATA_FILE, "w") as f:
            json.dump(races, f, indent=4)

# === Users Data Persistence ===
def load_users():
    global users
    if USERS_FILE and os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            users.update(json.load(f))

def save_users():
    if USERS_FILE:
        with open(USERS_FILE, "w") as f:
            json.dump(users, f, indent=4)

# === Last Activity Persistence ===
def load_last_activity():
    global last_activity
    if LAST_ACTIVITY_FILE and os.path.exists(LAST_ACTIVITY_FILE):
        with open(LAST_ACTIVITY_FILE, "r") as f:
            data = json.load(f)
            last_activity = {int(k): datetime.fromisoformat(v) for k, v in data.items()}

def save_last_activity():
    if LAST_ACTIVITY_FILE:
        with open(LAST_ACTIVITY_FILE, "w") as f:
            json.dump({str(k): v.isoformat() for k, v in last_activity.items()}, f, indent=4)

# === User Helpers ===
def ensure_user_exists(user_id):
    user_id = str(user_id)
    if user_id not in users:
        users[user_id] = {"shards": 100, "races_joined": {}, "races_won": {}}

def award_crystal_shards(user_id, randomizer):
    user_id = str(user_id)
    ensure_user_exists(user_id)
    users[user_id]["races_won"][randomizer] = users[user_id]["races_won"].get(randomizer, 0) + 1
    users[user_id]["shards"] += 10
    save_users()

def increment_participation(user_id, randomizer):
    user_id = str(user_id)
    ensure_user_exists(user_id)
    users[user_id]["races_joined"][randomizer] = users[user_id]["races_joined"].get(randomizer, 0) + 1
    users[user_id]["shards"] += 2
    save_users()

# === Cleanup Timer Trigger ===
def start_cleanup_timer(channel_id):
    last_activity[int(channel_id)] = datetime.now(timezone.utc)
    save_last_activity()

    race = races.get(str(channel_id))
    if race and race.get("race_type") == "live" and not race.get("finished", False):
        race["finished"] = True
        save_races()
    elif race and race.get("race_type") == "async" and not race.get("async_finished", False):
        race["async_finished"] = True
        save_races()

# === Cleanup Task ===
@tasks.loop(minutes=1)
async def cleanup_inactive_races(bot):
    now = datetime.now(timezone.utc)
    for channel_id, last_active in list(last_activity.items()):
        race = races.get(str(channel_id))
        if not race or not race.get("started"):
            continue

        race_type = race.get("race_type", "live")
        runners = race.get("runners", {})
        joined = set(map(str, race.get("joined_users", [])))
        finished = {uid for uid, data in runners.items() if data["status"] in ["done", "forfeit"]}
        all_runners_finished = (finished == joined)

        # Check if race is actually done
        if race_type == "live" and not (all_runners_finished or race.get("finished")):
            continue
        if race_type == "async" and not (all_runners_finished and race.get("async_finished")):
            continue

        # Wait for inactivity
        if (now - last_active).total_seconds() > 600:
            guild = bot.guilds[0]  # assumes single guild bot
            race_channel = guild.get_channel(int(channel_id))
            if race_channel:
                try:
                    await race_channel.delete()
                except Exception as e:
                    print(f"❌ Failed to delete race channel {channel_id}: {e}")

            spoilers_id = race.get("spoilers_channel_id")
            if spoilers_id:
                spoilers_channel = guild.get_channel(spoilers_id)
                if spoilers_channel:
                    try:
                        await spoilers_channel.delete()
                    except Exception as e:
                        print(f"❌ Failed to delete spoilers channel {spoilers_id}: {e}")

            ann_channel_id = race.get("announcement_channel_id")
            ann_message_id = race.get("announcement_message_id")
            if ann_channel_id and ann_message_id:
                ann_channel = guild.get_channel(ann_channel_id)
                if ann_channel:
                    try:
                        ann_msg = await ann_channel.fetch_message(ann_message_id)
                        await ann_msg.delete()
                        print(f"🧹 Deleted race announcement message {ann_message_id}")
                    except Exception as e:
                        print(f"❌ Failed to delete announcement message {ann_message_id}: {e}")

            races.pop(str(channel_id), None)
            last_activity.pop(channel_id, None)
            save_races()
            save_last_activity()
            print(f"🧹 Cleaned up race room {channel_id} and associated spoilers room.")

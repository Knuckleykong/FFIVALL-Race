import json
import os
from datetime import datetime, timezone
import discord
from bot_config import DATA_FILE, USERS_FILE

races = {}
users = {}
last_activity = {}

def load_races():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            races.update(json.load(f))

def save_races():
    with open(DATA_FILE, "w") as f:
        json.dump(races, f, indent=4)

def load_users():
    global users
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            users.update(json.load(f))

def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

def ensure_user_exists(user_id):
    user_id = str(user_id)
    if user_id not in users:
        users[user_id] = {"shards": 100, "races_joined": {}, "races_won": {}}

def award_crystal_shards(user_id, randomizer):
    ensure_user_exists(user_id)
    users[user_id]["races_won"][randomizer] = users[user_id]["races_won"].get(randomizer, 0) + 1
    users[user_id]["shards"] += 10
    save_users()

def increment_participation(user_id, randomizer):
    ensure_user_exists(user_id)
    users[user_id]["races_joined"][randomizer] = users[user_id]["races_joined"].get(randomizer, 0) + 1
    users[user_id]["shards"] += 2
    save_users()

def start_cleanup_timer(channel_id):
    last_activity[channel_id] = datetime.now(timezone.utc)

from discord.ext import tasks

@tasks.loop(minutes=1)
async def cleanup_inactive_races():
    now = datetime.now(timezone.utc)
    for channel_id, last_active in list(last_activity.items()):
        race = races.get(str(channel_id))
        if not race or not race.get("started"):
            continue

        runners = race.get("runners", {})
        joined = set(map(str, race.get("joined_users", [])))
        finished = {uid for uid, data in runners.items() if data["status"] in ["done", "forfeit"]}

        all_runners_finished = (finished == joined)
        if race["race_type"] == "live" and not all_runners_finished:
            continue
        if race["race_type"] == "async" and (not all_runners_finished or not race.get("async_finished")):
            continue

        race["finished"] = True
        save_races()

        if (now - last_active).total_seconds() > 600:
            guild = discord.utils.get(tasks.loop._bot.guilds)
            if guild:
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
            print(f"🧹 Cleaned up race room {channel_id} and associated spoilers room.")

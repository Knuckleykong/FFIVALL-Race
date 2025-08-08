import json
import os
import asyncio
from datetime import datetime, timezone, timedelta
from discord.ext import tasks

# === Globals ===
races = {}
users = {}
last_activity = {}

# === File paths (populated from bot_config) ===
DATA_FILE = None
USERS_FILE = None
LAST_ACTIVITY_FILE = "last_activity.json"

# === Internal bot reference for reap loop ===
_bot_ref = None

# === Thresholds ===
CLEANUP_THRESHOLD_SECONDS = 10 * 60  # 10 minutes


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
    for uid in list(users.keys()):
        ensure_user_exists(uid)


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
            parsed = {}
            for k, v in data.items():
                try:
                    parsed[k] = datetime.fromisoformat(v)
                except Exception:
                    continue
            last_activity = parsed  # assign to module-level
    # else leave as empty dict


def save_last_activity():
    if LAST_ACTIVITY_FILE:
        with open(LAST_ACTIVITY_FILE, "w") as f:
            serializable = {}
            for k, v in last_activity.items():
                if isinstance(v, datetime):
                    serializable[k] = v.isoformat()
            json.dump(serializable, f, indent=4)


# === Activity Helper ===
def touch_activity(channel_id):
    channel_id = str(channel_id)
    last_activity[channel_id] = datetime.now(timezone.utc)
    save_last_activity()


# === User Helpers ===
def ensure_user_exists(user_id):
    user_id = str(user_id)
    if user_id not in users:
        users[user_id] = {
            "crystal_shards": 100,
            "races_joined": {},
            "races_won": {}
        }
    else:
        users[user_id].setdefault("crystal_shards", 100)
        users[user_id].setdefault("races_joined", {})
        users[user_id].setdefault("races_won", {})


def award_crystal_shards(user_id, randomizer):
    ensure_user_exists(user_id)
    user_id = str(user_id)
    users[user_id]["races_won"][randomizer] = users[user_id]["races_won"].get(randomizer, 0) + 1
    users[user_id]["crystal_shards"] += 10
    save_users()


def increment_participation(user_id, randomizer):
    ensure_user_exists(user_id)
    user_id = str(user_id)
    users[user_id]["races_joined"][randomizer] = users[user_id]["races_joined"].get(randomizer, 0) + 1
    users[user_id]["crystal_shards"] += 2
    save_users()


# === Cleanup Timer Trigger (Persistent) ===
def start_cleanup_timer(channel_id, delay=600):
    """Schedule cleanup for a race room in delay seconds (default 10 min)."""
    channel_id = str(channel_id)
    # Update last activity (usually called when race finishes)
    touch_activity(channel_id)

    race = races.get(channel_id)
    if race:
        # Mark race finished flags
        if race.get("race_type") == "live" and not race.get("finished", False):
            race["finished"] = True
        elif race.get("race_type") == "async" and not race.get("async_finished", False):
            race["async_finished"] = True

        # Mark cleanup pending (for compatibility / visibility)
        race["cleanup_pending"] = True
        race["cleanup_scheduled_for"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        save_races()
    # NOTE: actual deletion is driven by the reap loop (or startup sweep)


# === Reaper Loop ===
@tasks.loop(seconds=30)
async def _reap_inactive_races():
    global _bot_ref
    if not _bot_ref:
        return
    now = datetime.now(timezone.utc)
    for channel_id, race in list(races.items()):
        try:
            # Always ensure channel_id is string
            cid = str(channel_id)
            last = last_activity.get(cid)
            if not last:
                continue
            # Normalize last to datetime
            if isinstance(last, str):
                try:
                    last_dt = datetime.fromisoformat(last)
                except Exception:
                    continue
            elif isinstance(last, datetime):
                last_dt = last
            else:
                continue

            # LIVE races: require live_finished flag (set in finalize_race)
            if race.get("race_type") == "live" and race.get("live_finished", False):
                if (now - last_dt).total_seconds() >= CLEANUP_THRESHOLD_SECONDS:
                    print(f"[DEBUG] Reaper auto-cleanup triggered for live race {cid}")
                    await cleanup_race(_bot_ref, cid)
                    continue  # already removed

            # ASYNC races: require finishasync_used
            if race.get("race_type") == "async" and race.get("finishasync_used", False):
                if (now - last_dt).total_seconds() >= CLEANUP_THRESHOLD_SECONDS:
                    print(f"[DEBUG] Reaper auto-cleanup triggered for async race {cid}")
                    await cleanup_race(_bot_ref, cid)
                    continue
        except Exception as e:
            print(f"[DEBUG] Error in reap loop for channel {channel_id}: {e}")


def init_cleanup(bot):
    """Initialize the background reap loop with the bot context."""
    global _bot_ref
    _bot_ref = bot
    if not _reap_inactive_races.is_running():
        _reap_inactive_races.start()
    print("[DEBUG] Cleanup reap loop started.")


# === Resume Cleanup on Bot Startup ===
async def resume_cleanup_on_startup(bot):
    """On startup, immediately sweep overdue cleanups and start the reap loop."""
    # Ensure last activity/races are loaded before calling
    init_cleanup(bot)

    now = datetime.now(timezone.utc)
    for channel_id, race in list(races.items()):
        cid = str(channel_id)
        last = last_activity.get(cid)
        if not last:
            continue
        if isinstance(last, str):
            try:
                last_dt = datetime.fromisoformat(last)
            except Exception:
                continue
        elif isinstance(last, datetime):
            last_dt = last
        else:
            continue

        # LIVE overdue
        if race.get("race_type") == "live" and race.get("live_finished", False):
            if (now - last_dt).total_seconds() >= CLEANUP_THRESHOLD_SECONDS:
                print(f"[DEBUG] Cleanup overdue for live race channel {cid} on startup.")
                await cleanup_race(bot, cid)
                continue

        # ASYNC overdue
        if race.get("race_type") == "async" and race.get("finishasync_used", False):
            if (now - last_dt).total_seconds() >= CLEANUP_THRESHOLD_SECONDS:
                print(f"[DEBUG] Cleanup overdue for async race channel {cid} on startup.")
                await cleanup_race(bot, cid)
                continue


# === Cleanup Logic ===
async def cleanup_race(bot, channel_id):
    """Delete race and spoiler channels, announcement message, and remove race entry."""
    channel_id = str(channel_id)
    race = races.get(channel_id)
    if not race:
        return

    guild = None
    # Try to find the guild: prefer stored guild_id
    guild_id = race.get("guild_id")
    if guild_id:
        guild = next((g for g in bot.guilds if g.id == guild_id), None)
    if not guild and bot.guilds:
        guild = bot.guilds[0]  # fallback to first

    if not guild:
        print(f"[DEBUG] Unable to locate guild for cleanup of {channel_id}")
        return

    # Delete race channel
    try:
        race_channel = guild.get_channel(int(channel_id))
        if race_channel:
            await race_channel.delete()
    except Exception as e:
        print(f"❌ Failed to delete race channel {channel_id}: {e}")

    # Delete spoilers channel
    spoilers_id = race.get("spoilers_channel_id")
    if spoilers_id:
        try:
            spoilers_channel = guild.get_channel(spoilers_id)
            if spoilers_channel:
                await spoilers_channel.delete()
        except Exception as e:
            print(f"❌ Failed to delete spoilers channel {spoilers_id}: {e}")

    # Delete announcement message
    ann_channel_id = race.get("announcement_channel_id")
    ann_message_id = race.get("announcement_message_id")
    if ann_channel_id and ann_message_id:
        try:
            ann_channel = guild.get_channel(ann_channel_id)
            if ann_channel:
                ann_msg = await ann_channel.fetch_message(ann_message_id)
                await ann_msg.delete()
        except Exception as e:
            print(f"❌ Failed to delete announcement message {ann_message_id}: {e}")

    # Remove race data
    races.pop(channel_id, None)
    last_activity.pop(channel_id, None)
    save_races()
    save_last_activity()
    print(f"🧹 Cleaned up race room {channel_id} and associated spoilers room.")

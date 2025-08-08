import re
import discord
from discord import app_commands
import asyncio
import random
import os
from datetime import datetime, timezone
import traceback

from race_manager import (
    races, save_races, save_last_activity, last_activity, start_cleanup_timer,
    award_crystal_shards, increment_participation, users,
    save_users, ensure_user_exists, touch_activity, load_races
)

from utils.spoilers import get_or_create_spoiler_room
from utils.wagers import handle_wager_payout
from utils.seeds import generate_seed, load_presets_for
from bot_config import ANNOUNCE_CHANNEL_ID, RACE_ALERT_ROLE_ID, RACE_CATEGORY_ID


# --- Helper: Normalize legacy statuses ---
def _normalize_status(status):
    if status == "ff":
        return "forfeit"
    return status


# --- Helper: Check if user is still in an active live race ---
def user_in_active_live_race(user_id):
    for race in races.values():
        if race.get("race_type") != "live" or race.get("finished", False):
            continue

        if user_id in race.get("joined_users", []):
            return True

        raw_status = race.get("runners", {}).get(str(user_id), {}).get("status")
        normalized = _normalize_status(raw_status)
        if normalized and normalized not in ("done", "forfeit"):
            return True

    return False


def all_live_done_or_forfeit(race):
    return all(
        _normalize_status(race.get("runners", {}).get(str(uid), {}).get("status")) in ("done", "forfeit")
        for uid in race.get("joined_users", [])
    )


# === Option 1 helper: strict time string parsing ===
def parse_strict_time_str(time_str: str) -> str | None:
    """
    Accepts S, M:SS, or H:MM:SS. Validates that minutes/seconds are 0-59.
    Returns canonical H:MM:SS (e.g. "0:05:07") or None if invalid.
    """
    parts = time_str.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None

    if len(nums) == 1:
        seconds = nums[0]
        if seconds < 0:
            return None
        h = 0
        m = seconds // 60
        s = seconds % 60
        return f"{h}:{m:02}:{s:02}"
    elif len(nums) == 2:
        minutes, seconds = nums
        if not (0 <= minutes < 60 and 0 <= seconds < 60):
            return None
        return f"0:{minutes:02}:{seconds:02}"
    elif len(nums) == 3:
        hours, minutes, seconds = nums
        if hours < 0 or not (0 <= minutes < 60 and 0 <= seconds < 60):
            return None
        return f"{hours}:{minutes:02}:{seconds:02}"
    else:
        return None


# === Shared helpers ===
async def grant_race_access(channel: discord.TextChannel, member: discord.abc.User, view=True, send=True):
    """Grant a user access to a race channel."""
    if channel:
        await channel.set_permissions(member, view_channel=view, send_messages=send)


async def ensure_spoiler_and_grant(race, guild, user=None):
    """
    Ensure the spoiler room exists, lock it to finished/forfeit runners,
    and optionally grant view access to a specific user.
    Returns the spoiler channel or None.
    """
    if not race.get("spoilers_channel_id"):
        spoiler = await get_or_create_spoiler_room(guild, race)
        race["spoilers_channel_id"] = spoiler.id
        save_races()
    else:
        spoiler = guild.get_channel(race.get("spoilers_channel_id"))
    if not spoiler:
        return None

    await lock_spoiler_channel_to_finishers(guild, race)
    if user:
        await spoiler.set_permissions(user, view_channel=True)
    return spoiler


def format_entrants_display(race, guild):
    """Return the formatted entrants string for /entrants, handling live and async plus winners."""
    race_type = race.get("race_type", "live")
    results = race.get("results", {}) or {}
    runners_data = race.get("runners", {}) or {}
    finishasync_used = race.get("finishasync_used", False)
    lines = []

    for user_id in race.get("joined_users", []):
        member = guild.get_member(int(user_id))
        name = member.display_name if member else f"Unknown ({user_id})"
        status = _normalize_status(runners_data.get(str(user_id), {}).get("status", ""))

        if race_type == "async":
            if status == "done":
                if finishasync_used:
                    time = results.get(str(user_id), {}).get("time", "??")
                    lines.append(f"**{name}** — Finished in {time}")
                else:
                    lines.append(f"**{name}** — Finished")
            elif status == "forfeit":
                lines.append(f"**{name}** — Forfeit")
            else:
                lines.append(f"**{name}** — Running")
        else:  # live
            if status == "done":
                time = results.get(str(user_id), {}).get("time")
                if time:
                    lines.append(f"**{name}** — Finished in {time}")
                else:
                    lines.append(f"**{name}** — Finished")
            elif status == "forfeit":
                lines.append(f"**{name}** — Forfeit")
            elif user_id in race.get("ready_users", []):
                lines.append(f"**{name}** — Ready")
            else:
                lines.append(f"**{name}** — Not Ready")

    # Winner lines
    if race_type == "async" and finishasync_used:
        winner_id = race.get("winner_id")
        if winner_id:
            winner_member = guild.get_member(int(winner_id))
            winner_name = winner_member.display_name if winner_member else f"Unknown ({winner_id})"
            lines.append(f"\n🏆 **Winner: {winner_name}**")
    if race_type == "live" and race.get("winner_id"):
        winner_member = guild.get_member(int(race["winner_id"]))
        winner_name = winner_member.display_name if winner_member else f"Unknown ({race['winner_id']})"
        lines.append(f"\n🏆 **Winner: {winner_name}**")

    return "\n".join(lines) if lines else "No entrants."


# === Helper: Restrict Spoiler Channel to Finishers/Forfeits ===
async def lock_spoiler_channel_to_finishers(guild, race):
    spoiler_channel = guild.get_channel(race.get("spoilers_channel_id"))
    if not spoiler_channel:
        return
    await spoiler_channel.set_permissions(guild.default_role, view_channel=False)
    for uid, data in race.get("runners", {}).items():
        status = _normalize_status(data.get("status"))
        if status in ["done", "forfeit"]:
            member = guild.get_member(int(uid))
            if member:
                await spoiler_channel.set_permissions(member, view_channel=True)


# === Channel ordering helpers ===
async def ensure_spoiler_below(race_channel: discord.TextChannel, spoiler_channel: discord.TextChannel):
    if race_channel.category_id != spoiler_channel.category_id:
        return
    category = race_channel.category
    if not category:
        return

    channels = [c for c in category.channels if not isinstance(c, discord.Thread)]
    channels.sort(key=lambda c: c.position)

    try:
        race_idx = next(i for i, c in enumerate(channels) if c.id == race_channel.id)
    except StopIteration:
        print(f"[WARN] Race channel '{race_channel.name}' not found in category for reordering.")
        return

    if race_idx + 1 < len(channels) and channels[race_idx + 1].id == spoiler_channel.id:
        return  # already correct

    new_order = []
    for c in channels:
        if c.id == race_channel.id:
            new_order.append(c)
            new_order.append(spoiler_channel)
        elif c.id == spoiler_channel.id:
            continue
        else:
            new_order.append(c)

    if hasattr(race_channel.guild, "edit_channel_positions"):
        positions = [{"id": c.id, "position": idx} for idx, c in enumerate(new_order)]
        try:
            print(f"[DEBUG] Bulk reordering to place '{spoiler_channel.name}' after '{race_channel.name}'.")
            await race_channel.guild.edit_channel_positions(positions=positions)
            return
        except Exception as e:
            print(f"[WARN] bulk reorder failed: {e}; falling back to single-channel edit. Attempted order: {[c.name for c in new_order]}")

    # Fallback
    try:
        target_pos = race_channel.position + 1
        await spoiler_channel.edit(position=target_pos)
        print(f"[DEBUG] Fallback: set position of '{spoiler_channel.name}' to {target_pos} to follow '{race_channel.name}'.")
    except Exception as e:
        print(f"[WARN] Fallback reposition failed for '{spoiler_channel.name}': {e}")


# === helper: fix existing spoiler ordering on startup ===
async def reorder_all_spoilers_on_startup(bot):
    for channel_id, race in list(races.items()):
        spoiler_id = race.get("spoilers_channel_id")
        if not spoiler_id:
            continue
        guild = bot.get_guild(race.get("guild_id")) or (bot.guilds[0] if bot.guilds else None)
        if not guild:
            continue
        race_chan = guild.get_channel(race.get("channel_id"))
        spoiler_chan = guild.get_channel(spoiler_id)
        if race_chan and spoiler_chan:
            await ensure_spoiler_below(race_chan, spoiler_chan)
            await asyncio.sleep(0.25)  # throttle to avoid rate limits


# === Persistent View for Join/Watch Buttons ===
class RaceAnnouncementView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Join Race", style=discord.ButtonStyle.green, custom_id="join_race_button")
    async def join_race(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)

            match = re.search(r"\*\*(.*?)\*\*", interaction.message.content)
            race_name = match.group(1) if match else None
            if not race_name:
                await interaction.followup.send("❌ Could not determine race name.", ephemeral=True)
                return

            race = next((r for r in races.values() if r.get("race_name", "").lower() == race_name.lower()), None)
            if not race:
                await interaction.followup.send("❌ This race no longer exists!", ephemeral=True)
                return

            new_join = False
            if interaction.user.id not in race.get("joined_users", []):
                race.setdefault("joined_users", []).append(interaction.user.id)
                save_races()
                new_join = True
                await interaction.followup.send(f"{interaction.user.mention} has joined the race!", ephemeral=True)
            else:
                await interaction.followup.send("ℹ️ You are already in this race, access confirmed.", ephemeral=True)

            race_channel = interaction.guild.get_channel(race.get("channel_id"))
            if race_channel:
                await grant_race_access(race_channel, interaction.user, view=True, send=True)
                if new_join:
                    await race_channel.send(f"👋 {interaction.user.display_name} has joined the race!")
                touch_activity(race_channel.id)
        except Exception as e:
            print(f"[ERROR] Join button exception: {e}")
            traceback.print_exc()
            try:
                await interaction.followup.send("❌ An error occurred while joining the race.", ephemeral=True)
            except:
                pass

    @discord.ui.button(label="Watch Race", style=discord.ButtonStyle.blurple, custom_id="watch_race")
    async def watch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            print(f"[DEBUG] Watch button clicked by {interaction.user}")

            match = re.search(r"\*\*(.*?)\*\*", interaction.message.content)
            race_name = match.group(1) if match else None
            if not race_name:
                await interaction.followup.send("❌ Could not determine race name.", ephemeral=True)
                return

            target_race = next((r for r in races.values() if r.get("race_name", "").lower() == race_name.lower()), None)
            if not target_race:
                await interaction.followup.send(f"❌ No race found with name `{race_name}`.", ephemeral=True)
                return

            guild = interaction.guild
            channel = guild.get_channel(target_race.get("channel_id"))
            if not channel:
                await interaction.followup.send(f"❌ Could not locate the channel for `{race_name}`.", ephemeral=True)
                return

            overwrites = channel.overwrites_for(interaction.user)
            if not overwrites.view_channel:
                await grant_race_access(channel, interaction.user, view=True, send=True)
                await interaction.followup.send(f"👀 You can now view and chat in `{race_name}`.", ephemeral=True)
                await channel.send(f"👋 {interaction.user.display_name} is now watching the race.")
            else:
                await interaction.followup.send(f"ℹ️ You already have access to `{race_name}`.", ephemeral=True)

            touch_activity(channel.id)
        except Exception as e:
            print(f"[ERROR] Watch button exception: {e}")
            traceback.print_exc()
            try:
                await interaction.followup.send("❌ An error occurred while watching the race.", ephemeral=True)
            except:
                pass


# === Persistent View Registration ===
def register_views(bot):
    @bot.event
    async def on_ready():
        try:
            await bot.tree.sync()  # ensure commands are up-to-date
            print("[DEBUG] Application commands synced.")
        except Exception as e:
            print(f"[ERROR] Failed to sync commands: {e}")
        bot.add_view(RaceAnnouncementView())
        print(f"[DEBUG] Ready as {bot.user}; persistent views registered.")

        # reorder spoiler rooms under their race rooms
        await reorder_all_spoilers_on_startup(bot)


# === Register Commands ===
def register(bot):
    # Normalize any legacy "ff" statuses to "forfeit"
    modified = False
    for race in races.values():
        runners = race.get("runners", {})
        for uid, data in runners.items():
            if data.get("status") == "ff":
                data["status"] = "forfeit"
                modified = True
    if modified:
        save_races()

    # === /newrace ===
    @bot.tree.command(name="newrace", description="Start a new race room")
    @app_commands.describe(randomizer="Randomizer to use", race_type="Race type: Live or Async")
    @app_commands.choices(randomizer=[
        app_commands.Choice(name="FF4FE", value="FF4FE"),
        app_commands.Choice(name="FF6WC", value="FF6WC"),
        app_commands.Choice(name="FF1R", value="FF1R"),
        app_commands.Choice(name="FF5CD", value="FF5CD"),
        app_commands.Choice(name="FFMQR", value="FFMQR")
    ])
    @app_commands.choices(race_type=[
        app_commands.Choice(name="Live", value="live"),
        app_commands.Choice(name="Async", value="async")
    ])
    async def newrace(interaction: discord.Interaction,
                      randomizer: app_commands.Choice[str],
                      race_type: app_commands.Choice[str]):
        try:
            await interaction.response.defer(ephemeral=True)

            if race_type.value == "live" and user_in_active_live_race(interaction.user.id):
                await interaction.followup.send(
                    "❌ You are already in another live race. Finish or forfeit it before creating a new one.",
                    ephemeral=True
                )
                return

            guild = interaction.guild
            parent_category = guild.get_channel(RACE_CATEGORY_ID)
            if not parent_category or not isinstance(parent_category, discord.CategoryChannel):
                await interaction.followup.send(f"❌ Could not find race category ID `{RACE_CATEGORY_ID}`.", ephemeral=True)
                return

            hash_code = ''.join(random.choices("0123456789ABCDEF", k=4))
            race_channel_name = f"{randomizer.value.lower()}-{hash_code}-{race_type.value}"
            channel = await guild.create_text_channel(race_channel_name, category=parent_category)

            races[str(channel.id)] = {
                "race_name": race_channel_name,
                "randomizer": randomizer.value,
                "channel_id": channel.id,
                "category_id": parent_category.id,
                "race_type": race_type.value,
                "creator_id": interaction.user.id,
                "joined_users": [interaction.user.id],
                "ready_users": [],
                "runners": {},
                "started": False,
                "finished": False,
                "guild_id": guild.id
            }

            touch_activity(channel.id)
            save_races()
            save_last_activity()

            await channel.set_permissions(guild.default_role, view_channel=False)
            await grant_race_access(channel, interaction.user, view=True, send=True)
            await channel.send(
                f"🏁 Race **{race_channel_name}** created using **{randomizer.name}**!\n"
                f"📌 Race type: **{race_type.name}**"
            )

            announcement_channel = guild.get_channel(ANNOUNCE_CHANNEL_ID)
            race_role = guild.get_role(RACE_ALERT_ROLE_ID)
            if announcement_channel and race_role:
                announcement_msg = await announcement_channel.send(
                    content=(
                        f"{race_role.mention} A new race room **{race_channel_name}** has been created!\n"
                        f"Randomizer: **{randomizer.name}** | Type: **{race_type.name}**\n"
                        "Click below to join or watch:"
                    ),
                    view=RaceAnnouncementView()
                )
                races[str(channel.id)]["announcement_channel_id"] = announcement_channel.id
                races[str(channel.id)]["announcement_message_id"] = announcement_msg.id
                save_races()

            await interaction.followup.send(
                f"✅ Race room `{race_channel_name}` created. You have been added as a runner.",
                ephemeral=True
            )
        except Exception as e:
            print(f"[ERROR] /newrace failed: {e}")
            traceback.print_exc()
            await interaction.followup.send("❌ Internal error occurred while creating race.", ephemeral=True)

    # === /ready ===
    @bot.tree.command(name="ready", description="Mark yourself as ready")
    async def ready(interaction: discord.Interaction):
        try:
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)

            if not race or interaction.user.id not in race.get("joined_users", []):
                await interaction.response.send_message("❌ You are not part of this race.", ephemeral=True)
                return

            if race.get("race_type") == "async":
                await interaction.response.send_message("⚠️ Ready check is not required in async races.", ephemeral=True)
                return

            if interaction.user.id in race.get("ready_users", []):
                await interaction.response.send_message("✅ You are already marked ready.", ephemeral=True)
                return

            race.setdefault("ready_users", []).append(interaction.user.id)
            touch_activity(channel_id)
            save_races()
            await interaction.response.send_message(f"✅ {interaction.user.mention} is ready!", ephemeral=False)
        except Exception as e:
            print(f"[ERROR] /ready failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Internal error occurred.", ephemeral=True)

    # === /entrants ===
    @bot.tree.command(name="entrants", description="Show race entrants and status")
    async def entrants(interaction: discord.Interaction):
        try:
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)
            if not race:
                await interaction.response.send_message("❌ No active race found in this channel.", ephemeral=True)
                return

            display = format_entrants_display(race, interaction.guild)
            await interaction.response.send_message(f"**Entrants:**\n{display}")
        except Exception as e:
            print(f"[ERROR] /entrants failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Internal error occurred.", ephemeral=True)

    # === /startrace ===
    @bot.tree.command(name="startrace", description="Start the race with a countdown")
    @app_commands.describe(countdown_seconds="Countdown time in seconds (default 10)")
    async def startrace(interaction: discord.Interaction, countdown_seconds: int = 10):
        try:
            await interaction.response.defer(ephemeral=False)
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)

            if not race:
                await interaction.followup.send("❌ No active race found in this channel.")
                return

            if race.get("race_type") == "async":
                await interaction.followup.send("⛔ Disabled for async races. Use `/startasync`.", ephemeral=True)
                return

            if race.get("started"):
                await interaction.followup.send("🚦 The race has already started.", ephemeral=True)
                return

            if not race.get("seed_set", False):
                await interaction.followup.send("⛔ A seed must be generated or submitted before starting.", ephemeral=True)
                return

            missing = [uid for uid in race.get("joined_users", []) if uid not in race.get("ready_users", [])]
            if missing:
                await interaction.followup.send("⛔ Not all users are marked ready.", ephemeral=True)
                return

            try:
                ann_channel_id = race.get("announcement_channel_id")
                ann_message_id = race.get("announcement_message_id")
                if ann_channel_id and ann_message_id:
                    ann_channel = interaction.guild.get_channel(ann_channel_id)
                    if ann_channel:
                        ann_msg = await ann_channel.fetch_message(ann_message_id)
                        await ann_msg.delete()
                        print(f"[DEBUG] Deleted announcement message {ann_message_id}")
            except Exception as e:
                print(f"[DEBUG] Failed to delete announcement message: {e}")

            await interaction.channel.send(f"⏳ Countdown starting for **{countdown_seconds}** seconds...")
            for i in range(countdown_seconds, 0, -1):
                await interaction.channel.send(f"{i}...")
                await asyncio.sleep(1)
            await interaction.channel.send("🏁 **GO!** The race has started!")

            race["started"] = True
            race["start_time"] = datetime.now(timezone.utc).isoformat()
            race["finish_times"] = {}
            touch_activity(channel_id)
            save_races()

            if not race.get("joined_users"):
                await interaction.followup.send("⚠️ No tracked runners in this live race; auto-finalizing now.")
                finalize_race(interaction.guild, race, channel_id)
                return

            await interaction.followup.send("Race officially started.")
        except Exception as e:
            print(f"[ERROR] /startrace failed: {e}")
            traceback.print_exc()
            await interaction.followup.send("❌ Internal error occurred.", ephemeral=True)

    # === /rollseed ===
    @bot.tree.command(name="rollseed", description="Roll a seed for the current race room")
    @app_commands.describe(flags_or_preset="Preset name or full flagstring")
    async def rollseed(interaction: discord.Interaction, flags_or_preset: str = None):
        try:
            await interaction.response.defer(ephemeral=False)
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)

            if not race or interaction.user.id not in race.get("joined_users", []):
                await interaction.followup.send("❌ You are not part of this race.", ephemeral=True)
                return

            touch_activity(channel_id)

            if race.get("randomizer") in ["FF5CD", "FF6WC"]:
                await interaction.followup.send("❌ `/rollseed` is disabled for FF5CD and FF6WC. Use `/submitseed`.", ephemeral=True)
                return

            if race.get("seed_set", False):
                await interaction.followup.send("⚠️ A seed has already been set for this race.", ephemeral=True)
                return

            preset_used = flags_or_preset or "random"
            seed_url = await asyncio.to_thread(generate_seed, race["randomizer"], preset_used)

            if seed_url:
                msg = await interaction.channel.send(
                    f"🔀 **Seed Rolled** using preset/flags: `{preset_used}`\n📎 {seed_url}"
                )
                try:
                    await msg.pin()
                except Exception as e:
                    print(f"[DEBUG] Failed to pin message: {e}")
                race["seed_set"] = True
                save_races()
                await interaction.followup.send("✅ Seed rolled and pinned.")
            else:
                await interaction.followup.send("⚠️ Failed to generate seed.", ephemeral=True)
        except Exception as e:
            print(f"[ERROR] /rollseed failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Internal error occurred.", ephemeral=True)

    @rollseed.autocomplete("flags_or_preset")
    async def preset_autocomplete(interaction: discord.Interaction, current: str):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race:
            return []
        presets = load_presets_for(race["randomizer"])
        return [
            app_commands.Choice(name=name, value=name)
            for name in presets
            if current.lower() in name.lower()
        ][:25]

    # === /startasync ===
    @bot.tree.command(name="startasync", description="Start an asynchronous race (async only)")
    async def startasync(interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=False)
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)
            if not race or race.get("race_type") != "async":
                await interaction.followup.send("❌ This command can only be used in async race rooms.", ephemeral=True)
                return
            if race.get("started"):
                await interaction.followup.send("⚠️ This async race has already been started.", ephemeral=True)
                return
            race["started"] = True
            race["start_time"] = datetime.now(timezone.utc).isoformat()
            touch_activity(channel_id)
            save_races()
            await interaction.followup.send("🕓 This asynchronous race is now marked as started.", ephemeral=True)
        except Exception as e:
            print(f"[ERROR] /startasync failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Internal error occurred.", ephemeral=True)

    # === /done ===
    @bot.tree.command(name="done", description="Mark yourself as finished (or submit your time for async)")
    async def done(interaction: discord.Interaction, time: str = None):
        try:
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)
            if not race or interaction.user.id not in race.get("joined_users", []):
                await interaction.response.send_message("❌ You are not part of this race.", ephemeral=True)
                return

            if race.get("race_type") == "live" and not race.get("started", False):
                await interaction.response.send_message(
                    "❌ Live race has not started yet. Use `/startrace` first.", ephemeral=True
                )
                return

            touch_activity(channel_id)

            if race.get("done_blocked", False):
                await interaction.response.send_message(
                    "❌ This race has been finalized. No further submissions.", ephemeral=True
                )
                return

            results = race.setdefault("results", {})
            runners = race.setdefault("runners", {})

            if race.get("race_type") == "async":
                if not time:
                    await interaction.response.send_message(
                        "❌ Async races require a time: `/done 1:23:45`.", ephemeral=True
                    )
                    return
                normalized = parse_strict_time_str(time)
                if not normalized:
                    await interaction.response.send_message(
                        "❌ Invalid time format. Use S, M:SS, or H:MM:SS with minutes/seconds 0–59.", ephemeral=True
                    )
                    return
                results[str(interaction.user.id)] = {"time": normalized}
                runners[str(interaction.user.id)] = {"status": "done"}
                save_races()
                await interaction.response.send_message(f"✅ {interaction.user.mention} has finished in `{normalized}`!", ephemeral=False)
            else:
                if str(interaction.user.id) in results:
                    await interaction.response.send_message("❌ You’re already marked done.", ephemeral=True)
                    return
                start_iso = race.get("start_time")
                if not start_iso:
                    await interaction.response.send_message("❌ Race start time missing; use `/startrace` first.", ephemeral=True)
                    return
                try:
                    start_dt = datetime.fromisoformat(start_iso)
                except Exception:
                    start_dt = datetime.fromisoformat(start_iso)
                elapsed = datetime.now(timezone.utc) - start_dt
                tstr = str(elapsed).split(".")[0]
                results[str(interaction.user.id)] = {"time": tstr}
                runners[str(interaction.user.id)] = {"status": "done"}
                save_races()
                await interaction.response.send_message(f"✅ {interaction.user.mention} finished in `{tstr}`!", ephemeral=False)

            spoiler = await ensure_spoiler_and_grant(race, interaction.guild, user=interaction.user)

            race_chan = interaction.guild.get_channel(race.get("channel_id"))
            if race_chan and spoiler:
                await ensure_spoiler_below(race_chan, spoiler)

            if race.get("race_type") == "live" and all_live_done_or_forfeit(race):
                finalize_race(interaction.guild, race, channel_id)
        except Exception as e:
            print(f"[ERROR] /done failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Internal error occurred.", ephemeral=True)

    # === /undone ===
    @bot.tree.command(name="undone", description="Revert your done or forfeit (or submitted time) so you can redo it")
    async def undone(interaction: discord.Interaction):
        try:
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)
            if not race or interaction.user.id not in race.get("joined_users", []):
                await interaction.response.send_message("❌ You are not part of this race.", ephemeral=True)
                return

            # Block if finalized
            if race.get("race_type") == "live" and race.get("live_finished", False):
                await interaction.response.send_message("❌ Cannot undo; this live race has already been finalized.", ephemeral=True)
                return
            if race.get("race_type") == "async" and race.get("finishasync_used", False):
                await interaction.response.send_message("❌ Cannot undo; this async race has already been finalized.", ephemeral=True)
                return

            runners = race.setdefault("runners", {})
            results = race.setdefault("results", {})

            uid_str = str(interaction.user.id)
            raw_status = runners.get(uid_str, {}).get("status")
            normalized = _normalize_status(raw_status)

            if normalized not in ("done", "forfeit"):
                await interaction.response.send_message("ℹ️ You are not marked as done or forfeited; nothing to undo.", ephemeral=True)
                return

            # Remove their result and runner entry
            results.pop(uid_str, None)
            runners.pop(uid_str, None)

            # If they were the recorded winner, clear it so it can be recomputed later
            if race.get("winner_id") == uid_str or race.get("winner_id") == interaction.user.id:
                race["winner_id"] = None

            save_races()
            touch_activity(channel_id)

            # Revoke spoiler access if applicable
            if race.get("spoilers_channel_id"):
                spoiler = interaction.guild.get_channel(race["spoilers_channel_id"])
                if spoiler:
                    try:
                        await spoiler.set_permissions(interaction.user, view_channel=False)
                    except Exception:
                        pass

            await interaction.response.send_message("✅ Your done/forfeit/time submission has been reverted. You can redo it now.", ephemeral=True)
        except Exception as e:
            print(f"[ERROR] /undone failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Internal error occurred.", ephemeral=True)


    # === /finishasync ===
    @bot.tree.command(name="finishasync", description="Close async race and show results")
    async def finishasync(interaction: discord.Interaction):
        try:
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)

            if not race or race.get("race_type") != "async":
                await interaction.response.send_message(
                    "❌ This command can only be used in an async race room.", ephemeral=True
                )
                return

            if not race.get("started", False):
                await interaction.response.send_message(
                    "❌ Async race has not been started yet. Use `/startasync` first.", ephemeral=True
                )
                return

            if interaction.user.id != race.get("creator_id"):
                await interaction.response.send_message(
                    "❌ Only the race creator can finalize this async race.", ephemeral=True
                )
                return

            touch_activity(channel_id)

            race["finishasync_used"] = True
            race["done_blocked"] = True

            results = race.setdefault("results", {})
            runners_data = race.setdefault("runners", {})

            def format_time(seconds):
                h = seconds // 3600
                m = (seconds % 3600) // 60
                s = seconds % 60
                return f"{h}:{m:02}:{s:02}"

            for user_id in race.get("joined_users", []):
                user_key = str(user_id)
                runner = runners_data.get(user_key, {})
                status = _normalize_status(runner.get("status"))
                if status == "done" and "finish_time" in runner:
                    results[user_key] = {"time": format_time(runner["finish_time"])}
                elif status == "done" and "finish_time" not in runner:
                    if results.get(user_key, {}).get("time"):
                        pass
                    else:
                        results[user_key] = {"time": "0:00:00"}
                else:
                    results[user_key] = {"time": "FF"}
                    runners_data[user_key] = {"status": "forfeit"}

            def time_to_seconds(timestr):
                if timestr == "FF":
                    return float('inf')
                h, m, s = map(int, timestr.split(":"))
                return h * 3600 + m * 60 + s

            valid_results = {uid: data for uid, data in results.items() if data["time"] != "FF"}
            if valid_results:
                winner_id, _ = min(valid_results.items(), key=lambda x: time_to_seconds(x[1]["time"]))
                race["winner_id"] = winner_id
            else:
                race["winner_id"] = None

            for user_id, data in results.items():
                if data["time"] != "FF":
                    h, m, s = map(int, data["time"].split(":"))
                    total_seconds = h * 3600 + m * 60 + s
                    runners_data[user_id]["finish_time"] = total_seconds
                else:
                    runners_data[user_id]["finish_time"] = None

            save_races()

            spoiler = await ensure_spoiler_and_grant(race, interaction.guild)

            finalize_race(interaction.guild, race, channel_id)

            try:
                ann_channel_id = race.get("announcement_channel_id")
                ann_message_id = race.get("announcement_message_id")
                if ann_channel_id and ann_message_id:
                    ann_channel = interaction.guild.get_channel(ann_channel_id)
                    if ann_channel:
                        ann_msg = await ann_channel.fetch_message(ann_message_id)
                        await ann_msg.delete()
                        print(f"[DEBUG] Deleted async announcement message {ann_message_id}")
            except Exception as e:
                print(f"[DEBUG] Failed to delete async announcement message: {e}")

            start_cleanup_timer(channel_id)

            entrants_list = []
            for user_id in race.get("joined_users", []):
                member = interaction.guild.get_member(int(user_id))
                name = member.display_name if member else f"Unknown ({user_id})"
                time_value = results.get(str(user_id), {}).get("time", "FF")
                if time_value == "FF":
                    entrants_list.append(f"**{name}** — Forfeit")
                else:
                    entrants_list.append(f"**{name}** — Finished in {time_value}")

            if race.get("winner_id"):
                winner_member = interaction.guild.get_member(int(race["winner_id"]))
                winner_name = winner_member.display_name if winner_member else f"Unknown ({race['winner_id']})"
                entrants_list.append(f"\n🏆 **Winner: {winner_name}**")

            entrants_display = "\n".join(entrants_list) if entrants_list else "No entrants."
            await interaction.response.send_message(f"**Async race finalized!**\n{entrants_display}")
        except Exception as e:
            print(f"[ERROR] /finishasync failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Internal error occurred.", ephemeral=True)

    # === /quit ===
    @bot.tree.command(name="quit", description="Leave race tracking but stay in the room")
    async def quit_race(interaction: discord.Interaction):
        try:
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)

            if not race or interaction.user.id not in race.get("joined_users", []):
                await interaction.response.send_message("❌ You are not tracked in this race.", ephemeral=True)
                return

            race["joined_users"] = [uid for uid in race.get("joined_users", []) if uid != interaction.user.id]
            race["ready_users"] = [uid for uid in race.get("ready_users", []) if uid != interaction.user.id]

            if "finish_times" in race:
                race["finish_times"].pop(str(interaction.user.id), None)
            runners = race.setdefault("runners", {})
            runners.pop(str(interaction.user.id), None)

            touch_activity(channel_id)
            save_last_activity()
            save_races()

            await interaction.channel.send(f"🚪 {interaction.user.display_name} is no longer a tracked racer in this room.")
            await interaction.response.send_message("✅ You are no longer a tracked racer but still have access.", ephemeral=True)
        except Exception as e:
            print(f"[ERROR] /quit failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Internal error occurred.", ephemeral=True)

    # === /ff ===
    @bot.tree.command(name="ff", description="Forfeit the current race")
    async def ff(interaction: discord.Interaction):
        try:
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)
            if not race or interaction.user.id not in race.get("joined_users", []):
                await interaction.response.send_message("❌ You are not part of this race.", ephemeral=True)
                return

            if race.get("race_type") == "live" and not race.get("started", False):
                await interaction.response.send_message(
                    "❌ Live race not started. Use `/startrace` first.", ephemeral=True
                )
                return

            touch_activity(channel_id)

            runners = race.setdefault("runners", {})
            status = _normalize_status(runners.get(str(interaction.user.id), {}).get("status"))
            if status in ("done", "forfeit"):
                await interaction.response.send_message("⚠️ Already finished or forfeited.", ephemeral=True)
                return

            runners[str(interaction.user.id)] = {"status": "forfeit"}
            results = race.setdefault("results", {})
            results[str(interaction.user.id)] = {"time": "FF"}
            save_races()

            spoiler = await ensure_spoiler_and_grant(race, interaction.guild, user=interaction.user)

            await interaction.response.send_message(f"🏳️ {interaction.user.mention} forfeited.", ephemeral=False)

            race_chan = interaction.guild.get_channel(race.get("channel_id"))
            if race_chan and spoiler:
                await ensure_spoiler_below(race_chan, spoiler)

            if race.get("race_type") == "live" and all_live_done_or_forfeit(race):
                finalize_race(interaction.guild, race, channel_id)
        except Exception as e:
            print(f"[ERROR] /ff failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Internal error occurred.", ephemeral=True)

    # === /finishlive ===
    @bot.tree.command(name="finishlive", description="Force finalize a live race (for cleanup when no participants remain)")
    async def finishlive(interaction: discord.Interaction):
        try:
            channel_id = str(interaction.channel.id)
            race = races.get(channel_id)
            if not race or race.get("race_type") != "live":
                await interaction.response.send_message("❌ This only works in live race rooms.", ephemeral=True)
                return
            if not race.get("started", False):
                await interaction.response.send_message("❌ Race has not started yet.", ephemeral=True)
                return
            if race.get("live_finished", False):
                await interaction.response.send_message("ℹ️ Race is already finalized.", ephemeral=True)
                return

            finalize_race(interaction.guild, race, channel_id)
            await interaction.response.send_message("✅ Live race manually finalized; cleanup will proceed.", ephemeral=True)
        except Exception as e:
            print(f"[ERROR] /finishlive failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Internal error occurred.", ephemeral=True)

    # === Debug: list registered commands ===
    @bot.tree.command(name="listcmds", description="Debug: list registered commands")
    async def listcmds(interaction: discord.Interaction):
        try:
            cmds = await bot.tree.fetch_commands()
            names = ", ".join(c.name for c in cmds)
            await interaction.response.send_message(f"Registered commands: {names}", ephemeral=True)
        except Exception as e:
            print(f"[ERROR] /listcmds failed: {e}")
            traceback.print_exc()
            await interaction.response.send_message("❌ Could not fetch commands.", ephemeral=True)


# === Finalize Race Helper ===
def finalize_race(guild, race, channel_id):
    finishers = [
        (uid, data["finish_time"])
        for uid, data in race.get("runners", {}).items()
        if _normalize_status(data.get("status")) == "done" and data.get("finish_time") is not None
    ]

    if finishers:
        winner_id, _ = min(finishers, key=lambda x: x[1])
        award_crystal_shards(winner_id, race["randomizer"])
        handle_wager_payout(race, winner_id, users)

        pot = sum(race.get("wagers", {}).values())
        total_awarded = 10 + pot + 2

        winner_member = guild.get_member(int(winner_id))
        winner_name = winner_member.mention if winner_member else f"<@{winner_id}>"

        channel = guild.get_channel(race.get("channel_id"))
        if channel:
            asyncio.create_task(channel.send(
                f"🏁 Race finished! Winner: {winner_name} — **{total_awarded} shards awarded**"
            ))
    else:
        channel = guild.get_channel(race.get("channel_id"))
        if channel:
            asyncio.create_task(channel.send("🏁 Race finished! No finishers to award."))

    for uid in race.get("runners", {}).keys():
        ensure_user_exists(uid)
        increment_participation(uid, race["randomizer"])

    # Set appropriate finalization flags
    if race.get("race_type") == "live":
        race["live_finished"] = True
    else:
        race["async_finalized"] = True

    save_races()
    start_cleanup_timer(channel_id)

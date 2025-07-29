import re
import discord
from discord import app_commands
import asyncio
import random
import os
from datetime import datetime, timezone

from race_manager import (
    races, save_races, save_last_activity, last_activity, start_cleanup_timer,
    award_crystal_shards, increment_participation, users,
    save_users, ensure_user_exists
)

from utils.spoilers import get_or_create_spoiler_room
from utils.wagers import handle_wager_payout
from utils.seeds import generate_seed, load_presets_for
from bot_config import ANNOUNCE_CHANNEL_ID, RACE_ALERT_ROLE_ID, RACE_CATEGORY_ID

# === View for Join/Watch Buttons ===
class RaceAnnouncementView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view

    @discord.ui.button(label="Join Race", style=discord.ButtonStyle.green, custom_id="join_race")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            print(f"[DEBUG] Join button clicked by {interaction.user}")
            match = re.search(r"\*\*(.*?)\*\*", interaction.message.content)
            race_name = match.group(1) if match else None
            if not race_name:
                await interaction.response.send_message("❌ Could not determine race name.", ephemeral=True)
                return

            race_channel_id = next((cid for cid, r in races.items() if r["race_name"].lower() == race_name.lower()), None)
            if not race_channel_id:
                await interaction.response.send_message(f"❌ No race found with name `{race_name}`.", ephemeral=True)
                return

            race = races[race_channel_id]
            if interaction.user.id in race["joined_users"]:
                await interaction.response.send_message("✅ You are already in this race.", ephemeral=True)
                return

            race["joined_users"].append(interaction.user.id)
            guild = interaction.guild
            race_channel = guild.get_channel(race["channel_id"])
            if race_channel:
                await race_channel.set_permissions(interaction.user, view_channel=True, send_messages=True)
                await race_channel.send(f"👋 {interaction.user.display_name} has joined the race!")
            save_races()
            await interaction.response.send_message(f"✅ You have joined `{race_name}`.", ephemeral=True)
        except Exception as e:
            print(f"[ERROR] Join button exception: {e}")
            await interaction.response.send_message("❌ An error occurred while joining the race.", ephemeral=True)

    @discord.ui.button(label="Watch Race", style=discord.ButtonStyle.blurple, custom_id="watch_race")
    async def watch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            print(f"[DEBUG] Watch button clicked by {interaction.user}")
            match = re.search(r"\*\*(.*?)\*\*", interaction.message.content)
            race_name = match.group(1) if match else None
            if not race_name:
                await interaction.response.send_message("❌ Could not determine race name.", ephemeral=True)
                return

            target_race = next((r for r in races.values() if r["race_name"].lower() == race_name.lower()), None)
            if not target_race:
                await interaction.response.send_message(f"❌ No race found with name `{race_name}`.", ephemeral=True)
                return

            guild = interaction.guild
            channel = guild.get_channel(target_race["channel_id"])
            if not channel:
                await interaction.response.send_message(f"❌ Could not locate the channel for `{race_name}`.", ephemeral=True)
                return

            await channel.set_permissions(interaction.user, view_channel=True, send_messages=True)
            await interaction.response.send_message(f"👀 You can now view and chat in `{race_name}`.", ephemeral=True)
            await channel.send(f"👋 {interaction.user.display_name} is now watching the race.")
        except Exception as e:
            print(f"[ERROR] Watch button exception: {e}")
            await interaction.response.send_message("❌ An error occurred while watching the race.", ephemeral=True)

# === Helper: Restrict Spoiler Channel to Finishers/Forfeits ===
async def lock_spoiler_channel_to_finishers(guild, race):
    spoiler_channel = guild.get_channel(race.get("spoilers_channel_id"))
    if not spoiler_channel:
        return
    # Reset default view (no one by default)
    await spoiler_channel.set_permissions(guild.default_role, view_channel=False)
    # Allow only finishers/forfeits
    for uid, data in race.get("runners", {}).items():
        if data["status"] in ["done", "forfeit"]:
            member = guild.get_member(int(uid))
            if member:
                await spoiler_channel.set_permissions(member, view_channel=True)

# === Persistent View Registration ===
def register_views(bot):
    @bot.event
    async def on_ready():
        bot.add_view(RaceAnnouncementView())
        print("[DEBUG] Persistent Join/Watch buttons registered.")

# === Register Commands ===
def register(bot):
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
    async def newrace(interaction: discord.Interaction, randomizer: app_commands.Choice[str], race_type: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)
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
            "creator_id": interaction.user.id,  # <-- Only creator can finish async
            "joined_users": [interaction.user.id],
            "ready_users": [],
            "runners": {},
            "started": False,
            "guild_id": guild.id
        }

        last_activity[str(channel.id)] = discord.utils.utcnow()
        save_last_activity()
        save_races()

        await channel.set_permissions(guild.default_role, view_channel=False)
        await channel.set_permissions(interaction.user, view_channel=True, send_messages=True)
        await channel.send(f"🏁 Race **{race_channel_name}** created using **{randomizer.name}**!\n📌 Race type: **{race_type.name}**")

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

        await interaction.followup.send(f"✅ Race room `{race_channel_name}` created. You have been added as a runner.", ephemeral=True)

    # === /startrace ===
    @bot.tree.command(name="startrace", description="Start the race with a countdown")
    @app_commands.describe(countdown_seconds="Countdown time in seconds (default 10)")
    async def startrace(interaction: discord.Interaction, countdown_seconds: int = 10):
        await interaction.response.defer(ephemeral=False)
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)

        if not race or interaction.user.id not in race["joined_users"]:
            await interaction.followup.send("❌ You are not part of this race.")
            return
        if race.get("race_type") == "async":
            await interaction.followup.send("⛔ Disabled for async races. Use `/startasync`.")
            return
        if race.get("started"):
            await interaction.followup.send("🚦 The race has already started.")
            return
        if not race.get("seed_set", False):
            await interaction.followup.send("⛔ A seed must be generated or submitted before starting.")
            return

        # Check readiness
        missing = [uid for uid in race["joined_users"] if uid not in race["ready_users"]]
        if missing:
            await interaction.followup.send("⛔ Not all users are marked ready.")
            return

        # Remove announcement message
        try:
            ann_channel_id = race.get("announcement_channel_id")
            ann_message_id = race.get("announcement_message_id")
            if ann_channel_id and ann_message_id:
                ann_channel = interaction.guild.get_channel(ann_channel_id)
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
        save_races()
        await interaction.followup.send("Race officially started.")


    # === /ready ===
    @bot.tree.command(name="ready", description="Mark yourself as ready")
    async def ready(interaction: discord.Interaction):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race or interaction.user.id not in race["joined_users"]:
            await interaction.response.send_message("❌ You are not part of this race.", ephemeral=True)
            return
        if race.get("race_type") == "async":
            await interaction.response.send_message("⚠️ Ready check is not required in async races.", ephemeral=True)
            return
        if interaction.user.id in race["ready_users"]:
            await interaction.response.send_message("✅ You are already marked ready.", ephemeral=True)
            return
        race["ready_users"].append(interaction.user.id)
        save_races()
        await interaction.response.send_message(f"✅ {interaction.user.mention} is ready!", ephemeral=False)

    # === /entrants ===
    @bot.tree.command(name="entrants", description="List all racers and their ready status.")
    async def entrants(interaction: discord.Interaction):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race:
            await interaction.response.send_message("❌ This is not a valid race room.", ephemeral=True)
            return
        entrants_list = []
        for uid in race.get("joined_users", []):
            member = interaction.guild.get_member(uid)
            name = member.display_name if member else f"User({uid})"
            status = "✅ Ready" if uid in race.get("ready_users", []) else "❌ Not Ready"
            entrants_list.append(f"- **{name}**: {status}")
        entrants_output = "\n".join(entrants_list) or "No entrants yet."
        await interaction.response.send_message(
            f"**Entrants for `{race['race_name']}`**:\n{entrants_output}",
            ephemeral=False
        )
    # === /rollseed ===
    @bot.tree.command(name="rollseed", description="Roll a seed for the current race room")
    @app_commands.describe(flags_or_preset="Preset name or full flagstring")
    async def rollseed(interaction: discord.Interaction, flags_or_preset: str = None):
        await interaction.response.defer(ephemeral=False)
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race or interaction.user.id not in race["joined_users"]:
            await interaction.followup.send("❌ You are not part of this race.")
            return
        if race["randomizer"] == "FF5CD":
            await interaction.followup.send("❌ `/rollseed` is disabled for FF5CD. Use `/submitseed`.")
            return

        preset_used = flags_or_preset or "random"
        seed_url = generate_seed(race["randomizer"], preset_used)
        if seed_url:
            msg = await interaction.channel.send(f"🔀 **Seed Rolled** using preset/flags: `{preset_used}`\n📎 {seed_url}")
            try:
                await msg.pin()
            except Exception as e:
                print(f"[DEBUG] Failed to pin message: {e}")
            race["seed_set"] = True
            save_races()
            await interaction.followup.send("✅ Seed rolled and pinned.")
        else:
            await interaction.followup.send("⚠️ Failed to generate seed.")

    # === Autocomplete for /rollseed ===
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
        await interaction.response.defer(ephemeral=False)
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race or race.get("race_type") != "async":
            await interaction.followup.send("❌ This command can only be used in async race rooms.")
            return
        if race.get("started"):
            await interaction.followup.send("⚠️ This async race has already been started.")
            return
        race["started"] = True
        race["start_time"] = datetime.now(timezone.utc).isoformat()
        save_races()
        await interaction.followup.send("🕓 This asynchronous race is now marked as started.")

    # === /done ===
    @bot.tree.command(name="done", description="Mark yourself as done")
    @app_commands.describe(time="(Async only) Finish time in H:MM:SS format")
    async def done(interaction: discord.Interaction, time: str = None):
        await interaction.response.defer(ephemeral=False)
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race:
            await interaction.followup.send("This command must be used inside an active race room.")
            return

        runners = race.setdefault("runners", {})
        user_id = str(interaction.user.id)
        if user_id in runners and runners[user_id]["status"] in ["done", "forfeit"]:
            await interaction.followup.send("You are already marked as finished or forfeited.")
            return

        # Calculate finish time
        if race["race_type"] == "async":
            if not time:
                await interaction.followup.send("Please provide your finish time (H:MM:SS).")
                return
            try:
                parts = list(map(int, time.split(":")))
                finish_seconds = parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
            except:
                await interaction.followup.send("Invalid time format. Use H:MM:SS or MM:SS.")
                return
        else:
            race_start = datetime.fromisoformat(race["start_time"])
            finish_seconds = int((datetime.now(timezone.utc) - race_start).total_seconds())

        # Save finish
        runners[user_id] = {"status": "done", "finish_time": finish_seconds}
        save_races()

        # Format race time into H:MM:SS
        hrs = finish_seconds // 3600
        mins = (finish_seconds % 3600) // 60
        secs = finish_seconds % 60
        race_time = f"{hrs}:{mins:02}:{secs:02}"

        # Trigger spoiler access
        spoiler_channel = await get_or_create_spoiler_room(interaction.guild, race)
        await lock_spoiler_channel_to_finishers(interaction.guild, race)

        # Check for all finished (LIVE races only)
        if race.get("race_type") == "live":
            joined = set(map(str, race.get("joined_users", [])))
            finished = {uid for uid, data in race.get("runners", {}).items() if data["status"] in ["done", "forfeit"]}
            if finished == joined:
                race["finished"] = True
                save_races()
                start_cleanup_timer(channel_id)

        await interaction.followup.send(f"{interaction.user.mention} has finished with **{race_time}**!")


    # === /ff ===
    @bot.tree.command(name="ff", description="Forfeit the current race")
    async def ff(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race:
            await interaction.followup.send("This command must be used inside an active race room.")
            return
        runners = race.setdefault("runners", {})
        user_id = str(interaction.user.id)
        if user_id in runners and runners[user_id]["status"] in ["done", "forfeit"]:
            await interaction.followup.send("You are already marked as finished or forfeited.")
            return

        runners[user_id] = {"status": "forfeit", "finish_time": None}
        save_races()

        # Trigger spoiler access
        spoiler_channel = await get_or_create_spoiler_room(interaction.guild, race)
        await lock_spoiler_channel_to_finishers(interaction.guild, race)

        # === NEW: Check for all finished (LIVE races only) ===
        if race.get("race_type") == "live":
            joined = set(map(str, race.get("joined_users", [])))
            finished = {uid for uid, data in race.get("runners", {}).items() if data["status"] in ["done", "forfeit"]}
            if finished == joined:
                race["finished"] = True
                save_races()
                start_cleanup_timer(channel_id)

        await interaction.followup.send(f"{interaction.user.mention} has forfeited the race.")

    # === /finishasync ===
    @bot.tree.command(name="finishasync", description="Finish and close the async race.")
    async def finishasync(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)

        if not race or race["race_type"] != "async":
            await interaction.followup.send("This command must be used inside an active async race room.")
            return

        # Only race creator can close the async race
        if interaction.user.id != race.get("creator_id"):
            await interaction.followup.send("⛔ Only the race creator can finish this async race.")
            return

        runners = race.get("runners", {})
        finishers = [(uid, data["finish_time"]) for uid, data in runners.items() if data["status"] == "done" and data["finish_time"] is not None]
        if finishers:
            winner_id, _ = min(finishers, key=lambda x: x[1])
            award_crystal_shards(winner_id, race["randomizer"])
            handle_wager_payout(race, winner_id, users)
            winner_member = interaction.guild.get_member(int(winner_id))
            winner_mention = winner_member.mention if winner_member else f"<@{winner_id}>"
            await interaction.followup.send(f"Async race finished! Winner: {winner_mention}")
        else:
            await interaction.followup.send("Async race finished! No finishers to award.")

        for uid in runners.keys():
            increment_participation(uid, race["randomizer"])

        # Remove announcement message if present
        try:
            ann_channel_id = race.get("announcement_channel_id")
            ann_message_id = race.get("announcement_message_id")
            if ann_channel_id and ann_message_id:
                ann_channel = interaction.guild.get_channel(ann_channel_id)
                ann_msg = await ann_channel.fetch_message(ann_message_id)
                await ann_msg.delete()
                print(f"[DEBUG] Deleted async announcement message {ann_message_id}")
        except Exception as e:
            print(f"[DEBUG] Failed to delete async announcement message: {e}")

        race.pop("announcement_channel_id", None)
        race.pop("announcement_message_id", None)
        race["async_finished"] = True
        save_races()
        start_cleanup_timer(channel_id)

    # === /quit ===
    @bot.tree.command(name="quit", description="Leave race tracking but stay in the room")
    async def quit(interaction: discord.Interaction):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race or interaction.user.id not in race["joined_users"]:
            await interaction.response.send_message("❌ You are not tracked in this race.", ephemeral=True)
            return
        race["joined_users"].remove(interaction.user.id)
        race["ready_users"] = [uid for uid in race["ready_users"] if uid != interaction.user.id]
        race.get("finish_times", {}).pop(str(interaction.user.id), None)
        save_last_activity()
        save_races()
        await interaction.channel.send(f"🚪 {interaction.user.display_name} is no longer a tracked racer in this room.")
        await interaction.response.send_message("✅ You are no longer a tracked racer but still have access.", ephemeral=True)

    # === /submitseed ===
    @bot.tree.command(name="submitseed", description="Upload a seed file to be used for the race")
    @app_commands.describe(seed_file="Attach the seed file (.sfc, .smc, .zip, etc.)")
    async def submitseed(interaction: discord.Interaction, seed_file: discord.Attachment):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race:
            await interaction.response.send_message("❌ This is not a valid race room.", ephemeral=True)
            return
        if race.get("submitted_seed"):
            await interaction.response.send_message("⚠️ A seed has already been submitted for this race.", ephemeral=True)
            return
        if not seed_file.filename.lower().endswith((".sfc", ".smc", ".zip")):
            await interaction.response.send_message("❌ Please upload a valid seed file (.sfc, .smc, .zip).", ephemeral=True)
            return
        os.makedirs("race_seeds", exist_ok=True)
        seed_path = f"race_seeds/{channel_id}_{seed_file.filename}"
        await seed_file.save(seed_path)
        race["submitted_seed"] = {"filename": seed_file.filename, "url": seed_file.url}
        race["seed_set"] = True
        save_races()
        message = await interaction.channel.send(f"📥 {interaction.user.mention} submitted the official race seed: [`{seed_file.filename}`]({seed_file.url})")
        try:
            await message.pin()
        except:
            pass
        await interaction.response.send_message("✅ Seed file submitted, shared, and pinned.", ephemeral=True)

# === finalize_race ===
def finalize_race(guild, race, channel_id):
    finishers = [
        (uid, data["finish_time"])
        for uid, data in race["runners"].items()
        if data["status"] == "done" and data["finish_time"] is not None
    ]

    if finishers:
        winner_id, _ = min(finishers, key=lambda x: x[1])
        award_crystal_shards(winner_id, race["randomizer"])
        handle_wager_payout(race, winner_id, users)
        winner_member = guild.get_member(int(winner_id))
        winner_name = winner_member.mention if winner_member else f"<@{winner_id}>"
        channel = guild.get_channel(race["channel_id"])
        if channel:
            asyncio.create_task(channel.send(f"🏁 Race finished! Winner: {winner_name}"))
    else:
        channel = guild.get_channel(race["channel_id"])
        if channel:
            asyncio.create_task(channel.send("🏁 Race finished! No finishers to award."))

    for uid in race["runners"].keys():
        increment_participation(uid, race["randomizer"])

    race["live_finished"] = True
    save_races()
    start_cleanup_timer(channel_id)  # cleanup handles race + spoiler deletion

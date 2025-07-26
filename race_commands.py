import discord
from discord import app_commands
import random
from datetime import datetime, timezone
from race_manager import races, save_races, last_activity, start_cleanup_timer, award_crystal_shards, increment_participation, users, save_users
from utils.spoilers import get_or_create_spoiler_room
from utils.wagers import handle_wager_payout
from bot_config import ANNOUNCE_CHANNEL_ID, RACE_ALERT_ROLE_ID, RACE_CATEGORY_ID

def register(bot):
    # === NEW RACE ===
    @bot.tree.command(name="newrace", description="Start a new race room")
    @app_commands.describe(
        randomizer="Randomizer to use",
        race_type="Type of race: Live (everyone starts together) or async (individual start)"
    )
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
        guild = interaction.guild
        parent_category = guild.get_channel(RACE_CATEGORY_ID)
        if not parent_category or not isinstance(parent_category, discord.CategoryChannel):
            await interaction.response.send_message(
                f"❌ Could not find the main race category with ID `{RACE_CATEGORY_ID}`.",
                ephemeral=True
            )
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
            "joined_users": [interaction.user.id],
            "ready_users": [],
            "runners": {},
            "started": False
        }
        last_activity[channel.id] = datetime.now(timezone.utc)
        save_races()

        await channel.set_permissions(guild.default_role, view_channel=False)
        await channel.set_permissions(interaction.user, view_channel=True, send_messages=True)
        await channel.send(f"🏁 Race **{race_channel_name}** created using **{randomizer.name}** randomizer!\n📌 Race type: **{race_type.name}**")
        await interaction.response.send_message(f"✅ Race room `{race_channel_name}` created.", ephemeral=True)

        announcement_channel = guild.get_channel(ANNOUNCE_CHANNEL_ID)
        race_role = guild.get_role(RACE_ALERT_ROLE_ID)
        if announcement_channel and race_role:
            view = discord.ui.View()
            async def join_callback(interact: discord.Interaction):
                await joinrace(interact, race_channel_name)
            async def watch_callback(interact: discord.Interaction):
                await watchrace(interact, race_channel_name)
            view.add_item(discord.ui.Button(label="Join Race", style=discord.ButtonStyle.success))
            view.add_item(discord.ui.Button(label="Watch Race", style=discord.ButtonStyle.primary))
            announcement_msg = await announcement_channel.send(
                content=f"{race_role.mention} A new race room **{race_channel_name}** has been created!\n"
                        f"Randomizer: **{randomizer.name}** | Type: **{race_type.name}**\n"
                        f"Click below to join or watch:",
                view=view
            )
            races[str(channel.id)]["announcement_channel_id"] = announcement_channel.id
            races[str(channel.id)]["announcement_message_id"] = announcement_msg.id
            save_races()

    # === DONE ===
    @bot.tree.command(name="done", description="Mark yourself as done (sync auto time, async manual time).")
    @app_commands.describe(time="(Async only) Your finish time in H:MM:SS format")
    async def done(interaction: discord.Interaction, time: str = None):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race:
            await interaction.response.send_message("This command must be used inside an active race room.", ephemeral=True)
            return

        runners = race.setdefault("runners", {})
        user_id = str(interaction.user.id)
        if user_id in runners and runners[user_id]["status"] in ["done", "forfeit"]:
            await interaction.response.send_message("You are already marked as finished or forfeited.", ephemeral=True)
            return

        finish_seconds = None
        if race["race_type"] == "async":
            if not time:
                await interaction.response.send_message("Please provide your finish time in H:MM:SS format (async).", ephemeral=True)
                return
            try:
                parts = list(map(int, time.split(":")))
                if len(parts) == 3:
                    h, m, s = parts
                elif len(parts) == 2:
                    h, m, s = 0, parts[0], parts[1]
                else:
                    raise ValueError
                finish_seconds = h * 3600 + m * 60 + s
            except ValueError:
                await interaction.response.send_message("Invalid time format. Use H:MM:SS or MM:SS.", ephemeral=True)
                return
        else:
            race_start = datetime.fromisoformat(race["start_time"])
            now = datetime.now(timezone.utc)
            finish_seconds = int((now - race_start).total_seconds())

        runners[user_id] = {"status": "done", "finish_time": finish_seconds}
        save_races()

        spoiler_channel = await get_or_create_spoiler_room(interaction.guild, race)
        member = interaction.guild.get_member(interaction.user.id)
        if spoiler_channel and member:
            await spoiler_channel.set_permissions(member, view_channel=True)

        await interaction.response.send_message(f"{interaction.user.mention} has finished!")

        # Auto cleanup trigger
        joined = set(map(str, race["joined_users"]))
        finished = {uid for uid, data in runners.items() if data["status"] in ["done", "forfeit"]}
        if finished == joined and race["race_type"] == "live":
            finalize_race(interaction.guild, race, channel_id)

    # === FORFEIT ===
    @bot.tree.command(name="ff", description="Forfeit the current race.")
    async def ff(interaction: discord.Interaction):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race:
            await interaction.response.send_message("This command must be used inside an active race room.", ephemeral=True)
            return

        runners = race.setdefault("runners", {})
        user_id = str(interaction.user.id)
        if user_id in runners and runners[user_id]["status"] in ["done", "forfeit"]:
            await interaction.response.send_message("You are already marked as finished or forfeited.", ephemeral=True)
            return

        runners[user_id] = {"status": "forfeit", "finish_time": None}
        save_races()

        spoiler_channel = await get_or_create_spoiler_room(interaction.guild, race)
        member = interaction.guild.get_member(interaction.user.id)
        if spoiler_channel and member:
            await spoiler_channel.set_permissions(member, view_channel=True)

        await interaction.response.send_message(f"{interaction.user.mention} has forfeited the race.")

        joined = set(map(str, race["joined_users"]))
        finished = {uid for uid, data in runners.items() if data["status"] in ["done", "forfeit"]}
        if finished == joined and race["race_type"] == "live":
            finalize_race(interaction.guild, race, channel_id)

    # === FINISH ASYNC ===
    @bot.tree.command(name="finishasync", description="Finish and close the async race.")
    async def finishasync(interaction: discord.Interaction):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race or race["race_type"] != "async":
            await interaction.response.send_message("This command must be used inside an active async race room.", ephemeral=True)
            return

        runners = race.get("runners", {})
        finishers = [(uid, data["finish_time"]) for uid, data in runners.items() if data["status"] == "done" and data["finish_time"] is not None]

        if finishers:
            winner_id, winner_time = min(finishers, key=lambda x: x[1])
            award_crystal_shards(winner_id, race["randomizer"])
            handle_wager_payout(race, winner_id, users)
            winner_member = interaction.guild.get_member(int(winner_id))
            winner_mention = winner_member.mention if winner_member else f"<@{winner_id}>"
            await interaction.response.send_message(f"Async race finished! Winner: {winner_mention}")
        else:
            await interaction.response.send_message("Async race finished! No finishers to award.")

        for uid in runners.keys():
            increment_participation(uid, race["randomizer"])

        race["async_finished"] = True
        save_races()
        start_cleanup_timer(channel_id)

    # === START RACE ===
    @bot.tree.command(name="startrace", description="Start the race with a countdown")
    @app_commands.describe(countdown_seconds="Number of seconds before the race starts (default: 10)")
    async def startrace(interaction: discord.Interaction, countdown_seconds: int = 10):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race or interaction.user.id not in race["joined_users"]:
            await interaction.response.send_message("❌ You are not part of this race.", ephemeral=True)
            return
        if race.get("race_type") == "async":
            await interaction.response.send_message("⛔ This command is disabled for asynchronous races. Use `/startasync` instead.", ephemeral=True)
            return
        if race.get("started"):
            await interaction.response.send_message("🚦 The race has already started.", ephemeral=True)
            return
        if not race.get("seed_set", False):
            await interaction.response.send_message("⛔ A seed must be generated or submitted before starting.", ephemeral=True)
            return
        missing = [uid for uid in race["joined_users"] if uid not in race["ready_users"]]
        if missing:
            await interaction.response.send_message("⛔ Not all users are marked ready.", ephemeral=True)
            return
        await interaction.response.send_message(f"⏳ Countdown starting for **{countdown_seconds}** seconds...")
        for i in range(countdown_seconds, 0, -1):
            await interaction.channel.send(f"{i}...")
            await asyncio.sleep(1)
        await interaction.channel.send("🏁 **GO!** The race has started!")
        race["started"] = True
        race["start_time"] = datetime.now(timezone.utc).isoformat()
        race["finish_times"] = {}
        save_races()

    # === JOIN ===
    @bot.tree.command(name="joinrace", description="Join a race by its name")
    @app_commands.describe(race_name="Name of the race you want to join")
    async def joinrace(interaction: discord.Interaction, race_name: str):
        race_channel_id = None
        for channel_id, race in races.items():
            if race["race_name"].lower() == race_name.lower():
                race_channel_id = channel_id
                break
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

    # === READY ===
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
        await interaction.response.send_message(f"✅ {interaction.user.display_name} is ready!")

    # === QUIT ===
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
        save_races()
        await interaction.channel.send(f"🚪 {interaction.user.display_name} is no longer a tracked racer in this room.")
        await interaction.response.send_message("✅ You are no longer a tracked racer but still have access.", ephemeral=True)

    # === WATCH ===
    @bot.tree.command(name="watchrace", description="Gain access to watch a race without participating")
    @app_commands.describe(race_name="Name of the race room you want to watch (e.g., ff4fe-1234)")
    async def watchrace(interaction: discord.Interaction, race_name: str):
        target_race = None
        for race in races.values():
            if race["race_name"].lower() == race_name.lower():
                target_race = race
                break
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

    # === START ASYNC ===
    @bot.tree.command(name="startasync", description="Start an asynchronous race (only for async rooms)")
    async def startasync(interaction: discord.Interaction):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        if not race or race.get("race_type") != "async":
            await interaction.response.send_message("❌ This command can only be used in asynchronous race rooms.", ephemeral=True)
            return
        if race.get("started"):
            await interaction.response.send_message("⚠️ This async race has already been started.", ephemeral=True)
            return
        race["started"] = True
        race["start_time"] = datetime.now(timezone.utc).isoformat()
        save_races()
        await interaction.response.send_message("🕓 This asynchronous race is now marked as started.")

def finalize_race(guild, race, channel_id):
    finishers = [(uid, data["finish_time"]) for uid, data in race["runners"].items() if data["status"] == "done" and data["finish_time"] is not None]
    if finishers:
        winner_id, _ = min(finishers, key=lambda x: x[1])
        award_crystal_shards(winner_id, race["randomizer"])
        handle_wager_payout(race, winner_id, users)
        winner_member = guild.get_member(int(winner_id))
        winner_name = winner_member.mention if winner_member else f"<@{winner_id}>"
        channel = guild.get_channel(race["channel_id"])
        if channel:
            import asyncio
            asyncio.create_task(channel.send(f"🏁 Race finished! Winner: {winner_name}"))
    else:
        channel = guild.get_channel(race["channel_id"])
        if channel:
            import asyncio
            asyncio.create_task(channel.send("🏁 Race finished! No finishers to award."))
    for uid in race["runners"].keys():
        increment_participation(uid, race["randomizer"])
    race["live_finished"] = True
    save_races()
    start_cleanup_timer(channel_id)

@bot.tree.command(name="rollseed", description="Generate a new seed")
@app_commands.describe(flags_or_preset="Preset name or full flagstring")
async def rollseed(interaction: discord.Interaction, flags_or_preset: str = None):
    channel_id = str(interaction.channel.id)
    race = races.get(channel_id)

    if not race or interaction.user.id not in race["joined_users"]:
        await interaction.response.send_message("❌ You are not part of this race.", ephemeral=True)
        return

    # Disable for FF5CD
    if race["randomizer"] in ["FF5CD"]:
        await interaction.response.send_message(
            f"❌ The `/rollseed` command is disabled for `{race['randomizer']}`.\n"
            f"Please upload a seed file manually using `/submitseed`.",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)
    preset_used = flags_or_preset or "random"
    seed_url = generate_seed(race["randomizer"], preset_used)

    if seed_url:
        message = await interaction.followup.send(
            f"🔀 Rolled seed using preset/flags: `{preset_used}`\n📎 Link: {seed_url}"
        )
        try:
            await message.pin()
        except discord.Forbidden:
            print("⚠️ Missing permissions to pin message.")
        except discord.HTTPException as e:
            print(f"❌ Failed to pin rolled seed message: {e}")

        # Mark that a seed has been set
        race["seed_set"] = True
        save_races()
    else:
        await interaction.followup.send("⚠️ Failed to generate seed.")

@rollseed.autocomplete("flags_or_preset")
async def preset_autocomplete(interaction: discord.Interaction, current: str):
    channel_id = str(interaction.channel.id)
    race = races.get(channel_id)
    if not race:
        return []
    presets = load_presets_for(race["randomizer"])
    return [
        app_commands.Choice(name=name, value=name)
        for name in presets.keys()
        if current.lower() in name.lower()
    ][:25]

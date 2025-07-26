import discord
from discord import app_commands
import random
import asyncio
from datetime import datetime, timezone

from race_manager import (
    races, save_races, last_activity, start_cleanup_timer,
    award_crystal_shards, increment_participation, users, save_users
)
from utils.spoilers import get_or_create_spoiler_room
from utils.wagers import handle_wager_payout
from utils.seed_generator import generate_seed, load_presets_for
from bot_config import ANNOUNCE_CHANNEL_ID, RACE_ALERT_ROLE_ID

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
        parent_category = guild.get_channel(int(interaction.guild.id))  # placeholder, replace with per-randomizer lookup
        if not parent_category or not isinstance(parent_category, discord.CategoryChannel):
            await interaction.response.send_message(
                f"❌ Could not find the main race category for `{randomizer.value}`.",
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

        # Announcement
        announcement_channel = guild.get_channel(ANNOUNCE_CHANNEL_ID)
        race_role = guild.get_role(RACE_ALERT_ROLE_ID)
        if announcement_channel and race_role:
            view = discord.ui.View()
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

    # === ROLLSEED ===
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
            asyncio.create_task(channel.send(f"🏁 Race finished! Winner: {winner_name}"))
    else:
        channel = guild.get_channel(race["channel_id"])
        if channel:
            asyncio.create_task(channel.send("🏁 Race finished! No finishers to award."))
    for uid in race["runners"].keys():
        increment_participation(uid, race["randomizer"])
    race["live_finished"] = True
    save_races()
    start_cleanup_timer(channel_id)

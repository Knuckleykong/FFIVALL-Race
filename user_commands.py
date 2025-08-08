import discord
from discord import app_commands
import json
import os

from race_manager import users, ensure_user_exists, save_users, races, save_races
from bot_config import PRESET_FILES
from utils.seeds import load_presets_for

def register(bot):
    @bot.tree.command(name="wager", description="Wager crystal shards on yourself")
    @app_commands.describe(amount="How many shards to wager")
    async def wager(interaction: discord.Interaction, amount: int):
        channel_id = str(interaction.channel.id)
        race = races.get(channel_id)
        user_id = str(interaction.user.id)
        ensure_user_exists(user_id)
        user_data = users[user_id]

        # === Validate wager amount ===
        if amount <= 0:
            await interaction.response.send_message("❌ Invalid wager amount.", ephemeral=True)
            return

        # === Validate race existence ===
        if not race:
            await interaction.response.send_message("❌ No active race found in this channel.", ephemeral=True)
            return

        # === Check race state (cutoff logic) ===
        if race.get("race_type") == "live" and race.get("started", False):
            await interaction.response.send_message(
                "❌ Wagering is closed because the live race has started.",
                ephemeral=True
            )
            return
        if race.get("race_type") == "async" and race.get("async_finished", False):
            await interaction.response.send_message(
                "❌ Wagering is closed because the async race has finished.",
                ephemeral=True
            )
            return

        # === Participation check (creator or joined) ===
        creator_id = str(race.get("creator_id", ""))
        joined_users = [str(uid) for uid in race.get("joined_users", [])]
        if user_id != creator_id and user_id not in joined_users:
            await interaction.response.send_message(
                "❌ You are not part of this race (must be race creator or have joined).",
                ephemeral=True
            )
            return

        # === Ensure wagers dict exists ===
        race.setdefault("wagers", {})

        # === Add to existing wager ===
        current_wager = race["wagers"].get(user_id, 0)
        total_new_wager = current_wager + amount

        # === Check shard balance ===
        available_shards = user_data.get("crystal_shards", 0)
        if total_new_wager > available_shards + current_wager:
            await interaction.response.send_message(
                f"❌ Not enough shards (Available: {available_shards}).",
                ephemeral=True
            )
            return

        # === Deduct and record wager ===
        user_data["crystal_shards"] = available_shards - amount
        race["wagers"][user_id] = total_new_wager

        # === Calculate total pot ===
        total_pot = sum(race["wagers"].values())

        save_races()
        save_users()

        await interaction.response.send_message(
            f"💎 {interaction.user.mention} wagered **{amount}** shards "
            f"(Total wager: **{total_new_wager}**, Pot: **{total_pot}**)!"
        )



    @bot.tree.command(name="userdetails", description="Check user race stats and shards")
    @app_commands.describe(user="User to check (blank = yourself)")
    async def userdetails(interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        user_id = str(target.id)
        ensure_user_exists(user_id)
        data = users.get(user_id, {"shards": 0, "races_joined": {}, "races_won": {}})
        lines = [f"📊 Stats for **{target.display_name}**", f"💎 Shards: `{data['crystal_shards']}`"]
        if not data["races_joined"]:
            lines.append("No race history.")
        else:
            lines.append("🏁 Races by Randomizer:")
            for rando in sorted(set(list(data["races_joined"].keys()) + list(data["races_won"].keys()))):
                lines.append(f"• **{rando}**: {data['races_joined'].get(rando, 0)} joined, {data['races_won'].get(rando, 0)} won")
        await interaction.response.send_message("\n".join(lines), ephemeral=False)

    @bot.tree.command(name="addpreset", description="Add a preset to a randomizer")
    @app_commands.describe(randomizer="Randomizer to store this preset under", name="Preset name", flags="Flagstring")
    @app_commands.choices(randomizer=[
        app_commands.Choice(name="FF4FE", value="FF4FE"),
        app_commands.Choice(name="FF6WC", value="FF6WC"),
        app_commands.Choice(name="FF1R", value="FF1R"),
        app_commands.Choice(name="FF5CD", value="FF5CD"),
        app_commands.Choice(name="FFMQR", value="FFMQR")
    ])
    async def addpreset(interaction: discord.Interaction, randomizer: app_commands.Choice[str], name: str, flags: str):
        file_path = PRESET_FILES.get(randomizer.value)
        if not file_path:
            await interaction.response.send_message("❌ Preset file path missing.", ephemeral=True)
            return
        presets = {}
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                presets = json.load(f)
        presets[name] = flags
        with open(file_path, "w") as f:
            json.dump(presets, f, indent=2)
        await interaction.response.send_message(f"✅ Preset `{name}` added to {randomizer.name}.", ephemeral=True)

    @bot.tree.command(name="listpresets", description="List all presets for a randomizer")
    @app_commands.describe(randomizer="Select randomizer")
    @app_commands.choices(randomizer=[
        app_commands.Choice(name="FF4FE", value="FF4FE"),
        app_commands.Choice(name="FF6WC", value="FF6WC"),
        app_commands.Choice(name="FF1R", value="FF1R"),
        app_commands.Choice(name="FF5CD", value="FF5CD"),
        app_commands.Choice(name="FFMQR", value="FFMQR")
    ])
    async def listpresets(interaction: discord.Interaction, randomizer: app_commands.Choice[str]):
        presets = load_presets_for(randomizer.value)
        if not presets:
            await interaction.response.send_message(f"❌ No presets for {randomizer.name}.", ephemeral=True)
            return
        preset_list = "\n".join(f"- `{name}`" for name in presets.keys())
        await interaction.response.send_message(f"📋 Presets for **{randomizer.name}**:\n{preset_list}", ephemeral=True)

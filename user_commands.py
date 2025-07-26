import discord
from discord import app_commands
import os
import json
from race_manager import users, ensure_user_exists, save_users
from bot_config import PRESET_FILES

def register(bot):
    # === USER DETAILS ===
    @bot.tree.command(name="userdetails", description="Check your or another user's race stats and shards")
    @app_commands.describe(user="The user to check (leave blank to view your own)")
    async def userdetails(interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        user_id = str(target.id)
        ensure_user_exists(user_id)
        data = users.get(user_id, {})
        shards = data.get("shards", 0)
        joined = data.get("races_joined", {})
        won = data.get("races_won", {})
        lines = [f"📊 Stats for **{target.display_name}**", f"💎 Crystal Shards: `{shards}`"]
        if not joined:
            lines.append("No race history.")
        else:
            lines.append("🏁 Races by Randomizer:")
            for rando in sorted(set(list(joined.keys()) + list(won.keys()))):
                races_joined = joined.get(rando, 0)
                races_won = won.get(rando, 0)
                lines.append(f"• **{rando}**: {races_joined} joined, {races_won} won")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # === WAGER ===
    @bot.tree.command(name="wager", description="Wager crystal shards on yourself in this race")
    @app_commands.describe(amount="How many shards to wager")
    async def wager(interaction: discord.Interaction, amount: int):
        channel_id = str(interaction.channel.id)
        from race_manager import races  # Imported here to avoid circular import
        race = races.get(channel_id)
        user_id = str(interaction.user.id)

        if not race or user_id not in race["joined_users"]:
            await interaction.response.send_message("❌ You are not part of this race.", ephemeral=True)
            return

        ensure_user_exists(user_id)
        user_data = users.get(user_id)
        current_shards = user_data.get("shards", 0)

        if amount <= 0:
            await interaction.response.send_message("❌ You must wager at least 1 shard.", ephemeral=True)
            return
        if amount > current_shards:
            await interaction.response.send_message("❌ You don't have enough shards to make this wager.", ephemeral=True)
            return

        race.setdefault("wagers", {})
        if user_id in race["wagers"]:
            await interaction.response.send_message("⚠️ You've already placed a wager for this race.", ephemeral=True)
            return

        # Deduct and record wager
        users[user_id]["shards"] -= amount
        race["wagers"][user_id] = amount
        save_users()

        await interaction.response.send_message(f"💰 You wagered **{amount}** shards on yourself. Good luck!", ephemeral=True)

    # === ADD PRESET ===
    @bot.tree.command(name="addpreset", description="Add a new preset to a specific randomizer")
    @app_commands.describe(
        randomizer="The randomizer to store this preset under",
        name="Name of the preset",
        flags="Flagstring or seed config"
    )
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
            await interaction.response.send_message("❌ Could not find preset file for this randomizer.", ephemeral=True)
            return
        if not os.path.exists(file_path):
            presets = {}
        else:
            with open(file_path, "r") as f:
                presets = json.load(f)
        presets[name] = flags
        with open(file_path, "w") as f:
            json.dump(presets, f, indent=2)
        await interaction.response.send_message(f"✅ Preset `{name}` added to {randomizer.name}.", ephemeral=True)

    # === LIST PRESETS ===
    @bot.tree.command(name="listpresets", description="List all presets for a given randomizer")
    @app_commands.describe(randomizer="Select the randomizer to view presets for")
    @app_commands.choices(randomizer=[
        app_commands.Choice(name="FF4FE", value="FF4FE"),
        app_commands.Choice(name="FF6WC", value="FF6WC"),
        app_commands.Choice(name="FF1R", value="FF1R"),
        app_commands.Choice(name="FF5CD", value="FF5CD"),
        app_commands.Choice(name="FFMQR", value="FFMQR")
    ])
    async def listpresets(interaction: discord.Interaction, randomizer: app_commands.Choice[str]):
        file_path = PRESET_FILES.get(randomizer.value)
        if not file_path or not os.path.exists(file_path):
            await interaction.response.send_message(f"❌ No presets found for {randomizer.name}.", ephemeral=True)
            return
        with open(file_path, "r") as f:
            presets = json.load(f)
        if not presets:
            await interaction.response.send_message(f"❌ No presets found for {randomizer.name}.", ephemeral=True)
            return
        preset_list = "\n".join(f"- `{name}`" for name in presets.keys())
        await interaction.response.send_message(f"📋 Presets for **{randomizer.name}**:\n{preset_list}", ephemeral=True)

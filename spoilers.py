import discord
from race_manager import save_races

async def get_or_create_spoiler_room(guild, race):
    # Check if a spoiler room is already linked
    existing_id = race.get("spoilers_channel_id")
    if existing_id:
        existing_channel = guild.get_channel(existing_id)
        if existing_channel:
            return existing_channel

    # Fallback: search by name
    spoiler_channel_name = f"{race['race_name']}-spoilers"
    existing_channel = discord.utils.get(guild.channels, name=spoiler_channel_name)
    if existing_channel:
        race["spoilers_channel_id"] = existing_channel.id
        save_races()
        return existing_channel

    # Create new spoiler channel
    parent_category = guild.get_channel(race["category_id"])
    spoiler_channel = await guild.create_text_channel(spoiler_channel_name, category=parent_category)
    await spoiler_channel.send("🔒 **Spoiler room opened.** Only finished/forfeit runners can view.")
    race["spoilers_channel_id"] = spoiler_channel.id
    save_races()
    return spoiler_channel

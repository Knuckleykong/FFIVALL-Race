import discord
from race_manager import save_races

async def get_or_create_spoiler_room(guild, race):
    """
    Create or get the spoiler room for a race.
    Locked by default, then grants access to all runners who already finished or forfeited.
    """

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

    # === Create new spoiler channel locked to everyone by default ===
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False)
    }
    parent_category = guild.get_channel(race["category_id"])
    spoiler_channel = await guild.create_text_channel(
        spoiler_channel_name,
        category=parent_category,
        overwrites=overwrites
    )

    # Grant access to all runners who already finished or forfeited
    runners_data = race.get("runners", {})
    for user_id, data in runners_data.items():
        if data.get("status") in ["done", "ff"]:
            member = guild.get_member(int(user_id))
            if member:
                await spoiler_channel.set_permissions(member, view_channel=True)

    # Save the spoiler channel id
    race["spoilers_channel_id"] = spoiler_channel.id
    save_races()

    return spoiler_channel

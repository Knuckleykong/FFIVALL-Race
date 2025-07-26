# bot_commands/__init__.py

from .race_commands import register as register_race_commands

def register(bot):
    """Register all slash commands with the provided bot instance."""
    register_race_commands(bot)

import json
import os
import aiohttp
import logging
from bot_config import (
    FF4FE_PRESETS_FILE,
    FF1R_PRESETS_FILE,
    FF5CD_PRESETS_FILE,
    FFMQR_PRESETS_FILE,
    FF6WC_PRESETS_FILE,
    FF4FE_API_KEY
)

# === Logging Setup ===
logger = logging.getLogger("SeedGenerator")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('[%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# === Load Presets From JSON Files ===
def load_presets_for(randomizer: str):
    """Load presets for the given randomizer from its JSON file defined in .env."""
    file_map = {
        "FF4FE": FF4FE_PRESETS_FILE,
        "FF1R": FF1R_PRESETS_FILE,
        "FF5CD": FF5CD_PRESETS_FILE,
        "FFMQR": FFMQR_PRESETS_FILE,
        "FF6WC": FF6WC_PRESETS_FILE
    }
    file_path = file_map.get(randomizer)
    if not file_path or not os.path.exists(file_path):
        logger.warning(f"No preset file found for {randomizer}. Returning empty presets.")
        return {}

    try:
        with open(file_path, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            else:
                logger.error(f"Preset file for {randomizer} is not a valid JSON object.")
                return {}
    except Exception as e:
        logger.error(f"Failed to load presets for {randomizer}: {e}")
        return {}

def get_preset_names(randomizer: str):
    """Return only preset names for autocomplete."""
    presets = load_presets_for(randomizer)
    return list(presets.keys())

# === Async FF4FE Seed Generation ===
async def generate_ff4fe_seed(preset_or_flags: str, ff4fe_api_key: str = None) -> str:
    """Generate a seed from the FF4FE API using a preset name or full flagstring."""
    base_url = "https://ff4fe.com/api/seed"
    headers = {"Authorization": ff4fe_api_key} if ff4fe_api_key else {}

    # Check if it's a preset name
    custom_presets = load_presets_for("FF4FE")
    flags = custom_presets.get(preset_or_flags)
    payload = {"preset": preset_or_flags} if flags else {"flags": preset_or_flags}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(base_url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    logger.error(f"FF4FE seed generation failed: HTTP {resp.status}")
                    return None
                data = await resp.json()
                return data.get("url") or data.get("seed_url")
        except Exception as e:
            logger.error(f"Error generating FF4FE seed: {e}")
            return None

# === Placeholder Async Generators for Other Randomizers ===
async def generate_ff1r_seed(preset_or_flags: str) -> str:
    logger.info("FF1R seed generation placeholder hit.")
    return None

async def generate_ff5cd_seed(preset_or_flags: str) -> str:
    logger.info("FF5CD seed generation placeholder hit.")
    return None

async def generate_ffmqr_seed(preset_or_flags: str) -> str:
    logger.info("FFMQR seed generation placeholder hit.")
    return None

async def generate_ff6wc_seed(preset_or_flags: str) -> str:
    logger.info("FF6WC seed generation placeholder hit.")
    return None

# === Main Dispatcher ===
async def generate_seed(randomizer: str, preset_or_flags: str, ff4fe_api_key: str = FF4FE_API_KEY):
    """Dispatch to the appropriate seed generation function based on randomizer."""
    if randomizer == "FF4FE":
        return await generate_ff4fe_seed(preset_or_flags, ff4fe_api_key)
    elif randomizer == "FF1R":
        return await generate_ff1r_seed(preset_or_flags)
    elif randomizer == "FF5CD":
        return await generate_ff5cd_seed(preset_or_flags)
    elif randomizer == "FFMQR":
        return await generate_ffmqr_seed(preset_or_flags)
    elif randomizer == "FF6WC":
        return await generate_ff6wc_seed(preset_or_flags)
    logger.warning(f"Unknown randomizer requested: {randomizer}")
    return None

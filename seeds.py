import json
import os
import random
import requests
import time
from bot_config import (
    FF4FE_PRESETS_FILE,
    FF1R_PRESETS_FILE,
    FF5CD_PRESETS_FILE,
    FFMQR_PRESETS_FILE,
    FF6WC_PRESETS_FILE,
    FF4FE_API_KEY,
    FF6WC_API_KEY
)

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
        return {}
    with open(file_path, "r") as f:
        return json.load(f)


# === FF4FE Seed Generation (Legacy API) ===
def generate_ff4fe_seed(preset_or_flags: str) -> str:
    custom_presets = load_presets_for("FF4FE")
    flags = custom_presets.get(preset_or_flags, preset_or_flags or random.choice(list(custom_presets.values())))
    try:
        gen_resp = requests.post(
            f"https://ff4fe.galeswift.com/api/generate?key={FF4FE_API_KEY}",
            json={"flags": flags}, headers={"User-Agent": "DiscordBot"}, timeout=10
        )
        gen_data = gen_resp.json()

        if gen_data.get("status") == "ok":
            task_id = gen_data.get("task_id")
            for _ in range(20):
                time.sleep(1.5)
                task_data = requests.get(
                    f"https://ff4fe.galeswift.com/api/task?key={FF4FE_API_KEY}&id={task_id}",
                    headers={"User-Agent": "DiscordBot"}
                ).json()
                if task_data.get("status") == "done":
                    seed_id = task_data.get("seed_id")
                    return requests.get(
                        f"https://ff4fe.galeswift.com/api/seed?key={FF4FE_API_KEY}&id={seed_id}",
                        headers={"User-Agent": "DiscordBot"}
                    ).json().get("url")

        elif gen_data.get("status") == "exists":
            seed_id = gen_data.get("seed_id")
            return requests.get(
                f"https://ff4fe.galeswift.com/api/seed?key={FF4FE_API_KEY}&id={seed_id}",
                headers={"User-Agent": "DiscordBot"}
            ).json().get("url")

    except Exception as e:
        print(f"❌ FF4FE seed error: {e}")
    return None


# === FF6WC Seed Generation (Legacy Placeholder) ===
def generate_ff6wc_seed(preset_or_flags: str) -> str:
    custom_presets = load_presets_for("FF6WC")
    flags = custom_presets.get(preset_or_flags, preset_or_flags or random.choice(list(custom_presets.values())))
    try:
        gen_resp = requests.post(
            f"https://ff6worldscollide.com/api/seed/create?key={FF6WC_API_KEY}",
            json={"flags": flags}, headers={"User-Agent": "DiscordBot"}, timeout=10
        )
        gen_data = gen_resp.json()
        if gen_data.get("status") == "ok":
            seed_id = gen_data.get("seed_id")
            for _ in range(20):
                time.sleep(1.5)
                status_data = requests.get(
                    f"https://ff6worldscollide.com/api/seed/status?key={FF6WC_API_KEY}&id={seed_id}",
                    headers={"User-Agent": "DiscordBot"}
                ).json()
                if status_data.get("status") == "done":
                    return f"https://ff6worldscollide.com/seed/{seed_id}"
        elif gen_data.get("status") == "exists":
            return f"https://ff6worldscollide.com/seed/{gen_data.get('seed_id')}"
    except Exception as e:
        print(f"❌ FF6WC seed error: {e}")
    return None


# === Generic Seed URL Builder (for static flag-based randomizers) ===
def generate_url_seed(randomizer: str, preset_name: str, base_url: str) -> str:
    presets = load_presets_for(randomizer)
    flagset = presets.get(preset_name)
    if not flagset:
        return None
    seed_hash = ''.join(random.choices("0123456789ABCDEF", k=8))
    return f"{base_url}?s={seed_hash}&f={flagset}"


# === Dispatcher (Main entry point) ===
def generate_seed(randomizer: str, preset_or_flags: str) -> str:
    if randomizer == "FF4FE":
        return generate_ff4fe_seed(preset_or_flags)
    elif randomizer == "FF6WC":
        return generate_ff6wc_seed(preset_or_flags)
    elif randomizer == "FFMQR":
        return generate_url_seed("FFMQR", preset_or_flags, "https://www.ffmqrando.net/")
    elif randomizer == "FF1R":
        return generate_url_seed("FF1R", preset_or_flags, "https://4-8-6.finalfantasyrandomizer.com/")
    elif randomizer == "FF5CD":
        # FF5CD is manually handled (user uploads zip)
        return None
    return f"https://placeholder.seed.url/{randomizer}/{preset_or_flags}"

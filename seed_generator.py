import json
import os
import random
import time
import requests
from bot_config import PRESET_FILES, API_KEY, FF6WC_API_KEY

def load_presets_for(randomizer):
    """Load preset flagsets for a given randomizer."""
    file_path = PRESET_FILES.get(randomizer)
    if file_path and os.path.isfile(file_path):
        with open(file_path, "r") as f:
            return json.load(f)
    return {}

def generate_manual_seed(randomizer):
    """Placeholder for randomizers that don't have API endpoints."""
    return f"https://placeholder.seed.url/{randomizer}/" + ''.join(random.choices('0123456789ABCDEF', k=8))

def generate_ff4fe_seed(preset_or_flags):
    presets = load_presets_for("FF4FE")
    flags = presets.get(preset_or_flags, preset_or_flags or random.choice(list(presets.values())))
    try:
        gen_resp = requests.post(
            f"https://ff4fe.galeswift.com/api/generate?key={API_KEY}",
            json={"flags": flags}, headers={"User-Agent": "DiscordBot"}, timeout=10
        )
        gen_data = gen_resp.json()
        if gen_data.get("status") == "ok":
            task_id = gen_data.get("task_id")
            for _ in range(20):
                time.sleep(1.5)
                task_data = requests.get(
                    f"https://ff4fe.galeswift.com/api/task?key={API_KEY}&id={task_id}",
                    headers={"User-Agent": "DiscordBot"}
                ).json()
                if task_data.get("status") == "done":
                    seed_id = task_data.get("seed_id")
                    return requests.get(
                        f"https://ff4fe.galeswift.com/api/seed?key={API_KEY}&id={seed_id}",
                        headers={"User-Agent": "DiscordBot"}
                    ).json().get("url")
        elif gen_data.get("status") == "exists":
            seed_id = gen_data.get("seed_id")
            return requests.get(
                f"https://ff4fe.galeswift.com/api/seed?key={API_KEY}&id={seed_id}",
                headers={"User-Agent": "DiscordBot"}
            ).json().get("url")
    except Exception as e:
        print(f"❌ FF4FE seed error: {e}")
    return None

def generate_ff6wc_seed(preset_or_flags):
    presets = load_presets_for("FF6WC")
    flags = presets.get(preset_or_flags, preset_or_flags or random.choice(list(presets.values())))
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

def generate_url_seed(randomizer, preset_name, base_url):
    presets = load_presets_for(randomizer)
    flagset = presets.get(preset_name)
    if not flagset:
        return None
    seed_hash = ''.join(random.choices("0123456789ABCDEF", k=8))
    return f"{base_url}?s={seed_hash}&f={flagset}"

def generate_seed(randomizer, preset_or_flags):
    """Route seed generation to the correct handler based on randomizer."""
    if randomizer == "FF4FE":
        return generate_ff4fe_seed(preset_or_flags)
    elif randomizer == "FF6WC":
        return generate_ff6wc_seed(preset_or_flags)
    elif randomizer == "FFMQR":
        return generate_url_seed("FFMQR", preset_or_flags, "https://www.ffmqrando.net/")
    elif randomizer == "FF1R":
        return generate_url_seed("FF1R", preset_or_flags, "https://4-8-6.finalfantasyrandomizer.com/")
    elif randomizer == "FF5CD":
        return generate_manual_seed(randomizer)
    return f"https://placeholder.seed.url/{randomizer}/{preset_or_flags}"

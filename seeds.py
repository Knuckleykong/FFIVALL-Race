import requests
import random
import asyncio

# === Import your preset loader ===
from .presets import load_presets_for

# === FF4FE Seed Generation (async) ===
async def generate_ff4fe_seed(preset_or_flags, api_key):
    """Generate an FF4FE seed using the API."""
    custom_presets = load_presets_for("FF4FE")
    flags = custom_presets.get(preset_or_flags, preset_or_flags or random.choice(list(custom_presets.values())))

    try:
        # Request seed generation
        gen_resp = requests.post(
            f"https://ff4fe.galeswift.com/api/generate?key={api_key}",
            json={"flags": flags},
            headers={"User-Agent": "DiscordBot"},
            timeout=10
        )
        gen_data = gen_resp.json()

        if gen_data.get("status") == "ok":
            task_id = gen_data.get("task_id")
            # Poll for seed generation completion
            for _ in range(20):
                await asyncio.sleep(1.5)
                task_data = requests.get(
                    f"https://ff4fe.galeswift.com/api/task?key={api_key}&id={task_id}",
                    headers={"User-Agent": "DiscordBot"}
                ).json()
                if task_data.get("status") == "done":
                    seed_id = task_data.get("seed_id")
                    return requests.get(
                        f"https://ff4fe.galeswift.com/api/seed?key={api_key}&id={seed_id}",
                        headers={"User-Agent": "DiscordBot"}
                    ).json().get("url")
        elif gen_data.get("status") == "exists":
            seed_id = gen_data.get("seed_id")
            return requests.get(
                f"https://ff4fe.galeswift.com/api/seed?key={api_key}&id={seed_id}",
                headers={"User-Agent": "DiscordBot"}
            ).json().get("url")

    except Exception as e:
        print(f"❌ FF4FE seed generation error: {e}")
    return None

# === FF6WC Seed Generation (placeholder) ===
def generate_ff6wc_seed(preset_or_flags):
    """Placeholder for FF6WC seed generation logic."""
    # Add real API handling here when available
    return "https://placeholder.ff6wc.seed.url"

# === FF1R Seed Generation (placeholder) ===
def generate_ff1r_seed(preset_or_flags):
    """Placeholder for FF1R seed generation logic."""
    return "https://placeholder.ff1r.seed.url"

# === FF5CD Seed Generation (placeholder) ===
def generate_ff5cd_seed(preset_or_flags):
    """FF5CD uses manual seed uploads; this is intentionally disabled."""
    return None  # will be blocked at /rollseed

# === FFMQR Seed Generation (placeholder) ===
def generate_ffmqr_seed(preset_or_flags):
    """Placeholder for FFMQR seed generation logic."""
    return "https://placeholder.ffmqr.seed.url"

# === Dispatcher ===
async def generate_seed(randomizer, preset_or_flags, ff4fe_api_key=None):
    """Dispatch seed generation based on selected randomizer."""
    if randomizer == "FF4FE":
        return await generate_ff4fe_seed(preset_or_flags, ff4fe_api_key)
    elif randomizer == "FF6WC":
        return generate_ff6wc_seed(preset_or_flags)
    elif randomizer == "FF1R":
        return generate_ff1r_seed(preset_or_flags)
    elif randomizer == "FF5CD":
        return generate_ff5cd_seed(preset_or_flags)
    elif randomizer == "FFMQR":
        return generate_ffmqr_seed(preset_or_flags)
    else:
        print(f"⚠️ Unknown randomizer '{randomizer}' requested.")
        return None

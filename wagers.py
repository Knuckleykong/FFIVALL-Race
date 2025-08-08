from race_manager import ensure_user_exists, save_users

def handle_wager_payout(race, winner_id, users):
    """
    Pays out the full pot of wagers to the race winner.
    Ensures all user accounts exist before updating shards.
    """
    wagers = race.get("wagers", {})
    if not wagers or not winner_id:
        print("[DEBUG] No wagers to pay out or winner not defined.")
        return

    # Ensure winner record exists
    ensure_user_exists(winner_id)

    total_pot = 0
    print(f"[DEBUG] Processing wagers for race in channel {race.get('channel_id')}")
    for uid, wager in wagers.items():
        ensure_user_exists(uid)  # Prevent KeyError for wagerers
        print(f"[DEBUG] Wagerer {uid} wagered {wager} shards.")
        total_pot += wager

    users[winner_id]["crystal_shards"] += total_pot
    print(f"[DEBUG] Winner {winner_id} awarded total pot {total_pot} shards. "
          f"New total: {users[winner_id]['crystal_shards']} shards.")
    
    save_users()
    print(f"💰 Paid {total_pot} shards (full pot) to winner {winner_id}.")

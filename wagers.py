from race_manager import ensure_user_exists, save_users

def handle_wager_payout(race, winner_id, users):
    wagers = race.get("wagers", {})
    if not wagers or not winner_id:
        return
    total_pot = sum(wagers.values())
    ensure_user_exists(winner_id)
    users[winner_id]["shards"] += total_pot
    save_users()
    print(f"💰 Paid {total_pot} shards (full pot) to winner {winner_id}.")

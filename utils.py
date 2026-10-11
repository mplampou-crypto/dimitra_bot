import random
import string

# Τα νέα όρια πόντων
LEVEL_THRESHOLDS = [
    (0, 200, "Πρωτάρης🐣🔞", 0),
    (201, 400, "Τολμηρός💋🔞", 1),
    (401, 650, "Ορεξάτος👀🔥🔞", 2),
    (651, 900, "Αφέντης💋👑🔞", 3),
    (901, float('inf'), "Ultimate VIP❤️🔞", 4)
]

def get_user_level_info(points: int):
    """Επιστρέφει τον τίτλο και το νούμερο του level (0-4) βάσει των πόντων."""
    for min_p, max_p, title, level_num in LEVEL_THRESHOLDS:
        if min_p <= points <= max_p:
            return title, level_num
    return "Πρωτάρης🐣🔞", 0

def get_level_progress(points: int):
    """Υπολογίζει την μπάρα προόδου, το % και τους υπολειπόμενους πόντους."""
    current_title, current_level_num = get_user_level_info(points)
    
    if current_level_num == 4: # Είναι ήδη στο μέγιστο Level
        return "🟥🟥🟥🟥🟥🟥🟥🟥🟥🟥 100%", "🎉 Έφτασες στο μέγιστο Level (Ultimate VIP)!", 100
        
    # Βρίσκουμε τα όρια του τρέχοντος level
    min_p, max_p = 0, 200
    next_level_title = ""
    for i, (mn, mx, title, lvl) in enumerate(LEVEL_THRESHOLDS):
        if lvl == current_level_num:
            min_p = mn
            max_p = mx
            next_level_title = LEVEL_THRESHOLDS[i+1][2]
            break

    # Υπολογισμός ποσοστού % ΜΕΣΑ στο τρέχον level (από 0 έως 100%)
    level_range = max_p - min_p
    points_in_level = points - min_p
    progress_percent = int((points_in_level / level_range) * 100)
    
    # Γραφική μπάρα
    filled_blocks = progress_percent // 10
    empty_blocks = 10 - filled_blocks
    bar = "🟥" * filled_blocks + "⬜" * empty_blocks
    
    points_needed = (max_p + 1) - points
    next_level_string = f"Χρειάζεσαι {points_needed} πόντους για ξεκλείδωμα του {next_level_title}!"
    
    return f"{bar} {progress_percent}%", next_level_string, progress_percent

def generate_order_code():
    """Δημιουργεί τον τυχαίο κωδικό για τα Custom προϊόντα."""
    return "KB-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

async def has_pending_request(user_id: int, db_pool) -> bool:
    """Ελέγχει αν ο χρήστης έχει ήδη ενεργό αίτημα κατάθεσης."""
    async with db_pool.acquire() as conn:
        pending_ps = await conn.fetchval("SELECT 1 FROM paysafe_requests WHERE telegram_id = $1 AND status = 'pending';", user_id)
        pending_mp = await conn.fetchval("SELECT 1 FROM moonpay_requests WHERE telegram_id = $1 AND status = 'pending';", user_id)
        return bool(pending_ps or pending_mp)
from aiogram.fsm.state import StatesGroup, State

class UserOnboarding(StatesGroup):
    waiting_for_ig = State()
    waiting_for_tiktok = State()

class UploadMedia(StatesGroup):
    waiting_for_files = State()
    waiting_for_placement = State()      # Νέο: Ρωτάει αν είναι για Κατάλογο ή Gallery
    waiting_for_catalog_cat = State()    # Νέο: Αν Κατάλογος -> Συνδρομή, VIP, Φωτό ή Βίντεο;
    waiting_for_gallery_level = State()  # Νέο: Αν Gallery -> Για ποιο Level;
    waiting_for_gallery_perc = State()   # Νέο: Αν Gallery -> Ποιο % του Level πρέπει να έχει;
    waiting_for_description = State()
    waiting_for_price = State()          # Ή διάρκεια αν είναι συνδρομή

class UploadCustom(StatesGroup):
    waiting_for_description = State()
    waiting_for_price = State()

class PaySafeTopUp(StatesGroup):
    waiting_for_amount = State()
    waiting_for_code = State()

class CardTopUp(StatesGroup):
    waiting_for_amount = State()
    waiting_for_screenshot = State()

class CartPromo(StatesGroup):
    waiting_for_promo = State()
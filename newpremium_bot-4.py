import sys
import subprocess
import importlib

# ============================================================
# Auto-install missing packages (so you never run pip yourself)
# ============================================================
def _ensure(pip_name, import_name=None):
    import_name = import_name or pip_name
    try:
        importlib.import_module(import_name)
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", pip_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

_ensure("pyTelegramBotAPI", "telebot")
_ensure("emoji")

import telebot
from telebot import types
from telebot.types import MessageEntity, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import random
import emoji
import time
import os
import json
import re
import logging
from datetime import datetime

# ============================================================
# Bot Credentials
# Set BOT_TOKEN as an environment variable, or paste it directly below.
# ============================================================
TOKEN = os.environ.get("BOT_TOKEN", "8813958938:AAH_f6HZCX1eRuDxPLeLAtWDUWQbAGnGS7g")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")  # e.g. "MyEmojiBot" (no @) — used to build referral links; set this env var, or referral links show a placeholder instead
ADMIN_IDS = [8441839325, 8832905555]

if TOKEN in ("PASTE_YOUR_NEW_BOT_TOKEN_HERE", "", None):
    raise RuntimeError("Set your bot token: either export BOT_TOKEN=... or paste it into the TOKEN line above.")

bot = telebot.TeleBot(TOKEN)

# ============================================================
# Logging — silenced from console, still written to bot.log for debugging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log")]
)
logger = logging.getLogger(__name__)

# Redirect any stray print()/traceback output away from the console too
_devnull = open(os.devnull, "w")
sys.stdout = _devnull
sys.stderr = _devnull

bot_active = True
user_db_file = "users.json"
usernames_db_file = "usernames.json"

def load_users():
    if os.path.exists(user_db_file):
        with open(user_db_file, "r") as f:
            return set(json.load(f))
    return set()

def save_users(users: set):
    with open(user_db_file, "w") as f:
        json.dump(list(users), f)

def load_usernames():
    if os.path.exists(usernames_db_file):
        try:
            with open(usernames_db_file, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_usernames(data: dict):
    with open(usernames_db_file, "w") as f:
        json.dump(data, f)

all_users: set = load_users()
# user_usernames: {"<user_id>": "username_without_at" or "" if none}
user_usernames: dict = load_usernames()

# ============================================================
# EMOJI PACKS — admin-added packs, each emoji reachable by a #code
# ============================================================
emoji_packs_file = "emoji_packs.json"
custom_packs_file = "custom_packs.json"

def load_json_file(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# --------------------------------------------------------
# Collision-safe unique ID helper.
#
# Several features generate IDs as f"{prefix}{int(time.time()*1000)}"
# (millisecond timestamp). Two calls within the same millisecond -
# which does happen, e.g. an admin script issuing back-to-back
# commands, or two people tapping "save" in the same instant - collide
# and the second silently overwrites the first under the identical
# key. This wraps the same millisecond-timestamp format (so existing
# IDs already saved to disk keep working and sort the same way) but
# guarantees uniqueness within a single process by tracking the last
# timestamp handed out and bumping by 1ms on a collision.
# --------------------------------------------------------
_last_issued_ms = {"value": 0}

def make_unique_id(prefix: str) -> str:
    now_ms = int(time.time() * 1000)
    if now_ms <= _last_issued_ms["value"]:
        now_ms = _last_issued_ms["value"] + 1
    _last_issued_ms["value"] = now_ms
    return f"{prefix}{now_ms}"

# code_to_emoji: {"#4411": "6035051267087143217", ...}
code_to_emoji: dict = load_json_file(emoji_packs_file, {})
# custom_packs: {"<user_id>": {"link": "...", "codes": {"#4411": "id", ...}}}
custom_packs: dict = load_json_file(custom_packs_file, {})

def save_emoji_packs():
    save_json_file(emoji_packs_file, code_to_emoji)

def save_custom_packs():
    save_json_file(custom_packs_file, custom_packs)

def next_pack_code() -> str:
    """Finds the next free sequential #code, starting at #4411."""
    n = 4411
    while f"#{n}" in code_to_emoji:
        n += 1
    return f"#{n}"

def add_emoji_to_pack(emoji_id: str) -> str:
    """Adds a single premium emoji id to the pack list under a new code, returns the code."""
    code = next_pack_code()
    code_to_emoji[code] = emoji_id
    save_emoji_packs()
    return code

def extract_custom_emoji_ids(message) -> list:
    """Pulls all unique custom_emoji_id values out of a message's entities."""
    ids = []
    for ent in (message.entities or []) + (message.caption_entities or []):
        if ent.type == "custom_emoji" and ent.custom_emoji_id not in ids:
            ids.append(ent.custom_emoji_id)
    return ids

def extract_pack_link(text: str):
    """Finds a t.me emoji-pack link in text, e.g. https://t.me/addemoji/PackName"""
    if not text:
        return None
    m = re.search(r"(?:https?://)?t\.me/addemoji/([A-Za-z0-9_]+)", text)
    if m:
        return m.group(1)
    return None

def extract_emoji_ids_from_link(text: str) -> list:
    """Resolves a t.me/addemoji/<slug> link to its custom_emoji_ids via getStickerSet.
    Returns [] if no link is found or the pack can't be resolved."""
    slug = extract_pack_link(text)
    if not slug:
        return []
    try:
        sticker_set = bot.get_sticker_set(slug)
        ids = []
        for sticker in sticker_set.stickers:
            eid = getattr(sticker, "custom_emoji_id", None)
            if eid and eid not in ids:
                ids.append(eid)
        return ids
    except Exception as e:
        logger.error(f"Failed to resolve emoji pack link '{slug}': {e}")
        return []

def get_user_custom_codes(uid: int) -> dict:
    return custom_packs.get(str(uid), {}).get("codes", {})

def set_user_custom_pack(uid: int, link: str, codes: dict):
    custom_packs[str(uid)] = {"link": link, "codes": codes}
    save_custom_packs()

# ============================================================
# EMOJI MAPPING - Normal Emoji -> Related Premium Emoji ID
# ============================================================
EMOJI_MAPPING = {
    # Check marks / Verification
    "✅": ["6246537187614005254", "6246782404476803545", "6010060634803148161", "6010498532488778300"],
    "✔️": ["6246871001062185760", "6010264538375525668", "6010487760710800947"],
    "☑️": ["6246537187614005254", "6010097953773983121"],
    
    # Eyes / Vision
    "👁️": ["6035338338406242050", "6035051267087143217", "6034945975963881533", "6034845323405299835"],
    "👁": ["6035338338406242050", "6035051267087143217"],
    "👀": ["6035225389356290238", "6035081585261287115", "6035243995154616907", "6035173858338672933"],
    
    # Fire / Hot / Trending
    "🔥": ["4956222745814762495", "4956606007221421405", "4956429969396859866", "6086954744268460848"],
    "💥": ["6032673796530377389", "4958479549265347295"],
    "⚡": ["5791970059597386804", "6087079590377820415", "6095843123252957701"],
    
    # Hearts / Love
    "❤️": ["5783157259152397008", "5801084710343938087", "6010280773351904888"],
    "💙": ["5780496071645991525", "6104780447684757396"],
    "💚": ["5888789252493283486"],
    "💛": ["5840261097719148872"],
    "🧡": ["5840263144212529797"],
    "💜": ["5840265018655703965"],
    "🖤": ["5840266939932994956"],
    
    # Stars / Rating
    "⭐": ["6244496562752331516", "5904618938578243567", "6010193314932855525"],
    "🌟": ["6010156854955480259", "6086924086791902713"],
    "✨": ["6010338729640596556", "6010086134023985536", "5801044672658805468"],
    
    # Vampire / Monster
    "🧛": ["6034871295072539452", "6035251193519805118", "6032673796530377389"],
    "🧛‍♂️": ["6034871295072539452", "6035251193519805118"],
    "👹": ["6034962795055812935"],
    "👺": ["6034962795055812935"],
    "👻": ["6035070298087231243"],
    "👿": ["6035242444671421879", "6032985916098750553"],
    "😈": ["6035136809950778133", "6032695825417638128", "6032739101508113500"],
    
    # Crown / King
    "👑": ["5794422335599546668", "6089003761496232797", "6247039939305808563"],
    
    # Money / Wealth
    "💰": ["6089104607328342288", "6086730718774300509", "6086664791026307819"],
    "💵": ["6089140105233044310"],
    "💎": ["6086778246882399112", "5791697221799907788"],
    
    # Thumbs up/down
    "👍": ["6089313931149448495", "4958626617535497157", "4956582500865410174"],
    "👎": ["6088789257285988672"],
    
    # Clapping
    "👏": ["6093744967304352336", "4956582500865410174"],
    
    # Smileys
    "😀": ["6093864814071780526", "6093922327978840798"],
    "😁": ["6035060329468137931"],
    "😂": ["5782741660936966676", "5782746664573867142"],
    "😃": ["6035337951859184840"],
    "😄": ["5782942227319756256"],
    "😅": ["5782670102486848559"],
    "😆": ["5782670102486848559"],
    "😉": ["6089024570612781324"],
    "😊": ["5780690182692935276"],
    "😍": ["6010179687001625256"],
    "🥰": ["6044369013952222465", "6044359320211034681"],
    "😘": ["6044373012566774137"],
    "😎": ["6032853480782172520", "6044373012566774137"],
    "😢": ["5780793884678296697"],
    "😭": ["5783024321324651865"],
    "😤": ["6034865170449175739", "6034855438053282213"],
    "😠": ["6035355642829475999", "6034843326245508065"],
    "😡": ["6035355642829475999"],
    "🤔": ["5782756916660802905", "5783034045130610245", "6093666528316625608"],
}

# ============================================================
# COUNTRY FLAG MAPPING - Flag Emoji -> Premium Flag ID
# ============================================================
FLAG_MAPPING = {
    "🇺🇸": "5433865586356531140", "🇬🇧": "5433827537241258614", "🇫🇷": "5433636707549331311",
    "🇩🇪": "5433845881046578644", "🇮🇳": "5433601609076586221", "🇯🇵": "5434147542369579483",
    "🇨🇳": "5435996255207567113", "🇷🇺": "5433674924168328689", "🇧🇷": "5433825269498525925",
    "🇮🇹": "5433627189901801019", "🇨🇦": "5433979415874779870", "🇦🇺": "5434067655977874913",
    "🇰🇷": "5434142701941437163", "🇪🇸": "5434026158003862063", "🇲🇽": "5434131139889478358",
    "🇮🇩": "5431739800883312139", "🇳🇱": "5431656358258685474", "🇹🇷": "5433792911214917126",
    "🇸🇦": "5433991338703991663", "🇦🇪": "5434013938821902926", "🇿🇦": "5431489619038320862",
    "🇵🇰": "5434064563601421981", "🇧🇩": "5433854239052935880",
    "🇱🇰": "5433609855413794108", "🇳🇵": "5433852744404317916", "🇲🇾": "5431620340662940910",
    "🇸🇬": "5433884376838454074", "🇵🇭": "5434119663736862995", "🇻🇳": "5431676201007592926",
    "🇹🇭": "5433814347396692144", "🇪🇬": "5433643519367461444", "🇳🇬": "5433982207603520017",
    "🇰🇪": "5433845881046578644", "🇦🇷": "5433845881046578644", "🇨🇱": "5433827537241258614",
    "🇵🇪": "5433827537241258614", "🇨🇴": "5433825269498525925", "🇻🇪": "5433767976937585990",
    "🇵🇹": "5433598722858562967", "🇸🇪": "5433628435442316429", "🇳🇴": "5434098446598419585",
    "🇩🇰": "5434129692485498098", "🇫🇮": "5434115081006756195", "🇮🇪": "5434012796360604182",
    "🇨🇭": "5433902785068283672", "🇦🇹": "5434027579638035690", "🇧🇪": "5431755073787016798",
    "🇬🇷": "5433972762970437003", "🇨🇿": "5434115081006756195", "🇭🇺": "5434001565021123877",
    "🇵🇱": "5433833485770964033", "🇷🇴": "5434132406904830055", "🇺🇦": "5434132406904830055",
}

# ============================================================
# PRIMARY EMOJIS FROM Reobashd pack (70 emojis)
# ============================================================
PRIMARY_EMOJIS = [
    "6035051267087143217", "6034945975963881533", "6034845323405299835", "6035169816774446606",
    "6035085583875837709", "6032965553658794901", "6035158121578501544", "6035208832257364215",
    "6035067476293718178", "6033130342964007608", "6035179291472302298", "6034986056598688136",
    "6032765485492214347", "6032660275973330342", "6034916516783198293", "6034904439335162652",
    "6034928023000585140", "6035372904303038740", "6035137110598492010", "6035338338406242050",
    "6035225389356290238", "6035081585261287115", "6035243995154616907", "6034865170449175739",
    "6035173858338672933", "6035210301136182368", "6035265083444042235", "6034871295072539452",
    "6035251193519805118", "6035136809950778133", "6032695825417638128", "6032739101508113500",
    "6032985916098750553", "6035374291577475270", "6035355642829475999", "6035337951859184840",
    "6035072209347678547", "6035060329468137931", "6033077437556855182", "6032823763903452409",
    "6034853694296560978", "6035015146412183834", "6035372401791864953", "6034955549445984368",
    "6032673796530377389", "6032916496542339992", "6034855438053282213", "6034962795055812935",
    "6034832094906028632", "6035087164423802534", "6035343380697846690", "6032737138708059114",
    "6035194237958493530", "6035317340311129897", "6035070298087231243", "6035242444671421879",
    "6034957847253487695", "6034925781027656042", "6033067975743902590", "6032975015471747801",
    "6034926000070988470", "6034843326245508065", "6032853480782172520", "6044373012566774137",
    "6044369013952222465", "6044359320211034681", "6044290806892729376", "6044238120528908813",
    "5791970059597386804", "5794422335599546668",
]

# ============================================================
# ALL PREMIUM EMOJIS (Complete pool for fallback)
# ============================================================
ALL_PREMIUM_EMOJIS = PRIMARY_EMOJIS + [
    "6246537187614005254", "6246610665914505571", "6244496562752331516", "6246782404476803545",
    "6247039939305808563", "6246774261218810895", "6246871001062185760",
    "5780840497958360623", "5780413823022273797", "5782940582347281850", "5783091623462180025",
    "5783151611270403662", "5783124312458270318", "5782741660936966676", "5782753386197685582",
    "5783029694328738752", "5782671841948603573", "5780425243340313827", "5783170625090622777",
    "5782858359493366808", "5783016603268420398", "5782914709464289647", "5782897082918507949",
    "5783023329187206172", "5782731481864475831", "5782734256413349701", "5783133172975801184",
    "5783157259152397008", "5783175250770399822", "5782876166427775766", "5782804668107199927",
    "5783176625159935132", "5782829832320586664", "5782670102486848559", "5782901906166780625",
    "6084695058894819673", "6086730718774300509", "6086664791026307819", "6089003761496232797",
    "6298332994260175589", "6296140830067395531", "6298821774423361023", "6136464120779638846",
    "4956222745814762495", "4958617898751886363", "4958479549265347295", "4958624886663678191",
]

ALL_PREMIUM_EMOJIS = list(set(ALL_PREMIUM_EMOJIS))

DEFAULT_EMOJI_ID = "6035338338406242050"
PLACEHOLDER = "🌟"
temp_data = {}

# ============================================================
# EMOJI CONVERSION FUNCTION (SMART MAPPING)
# ============================================================
_emoji_id_cache: dict = {}
EMOJI_CACHE_TTL = 1800

def _normalize_emoji(e: str) -> str:
    normalized = e.replace('\ufe0f', '').replace('\ufe0e', '').replace('\u200d', '')
    return normalized if normalized else e

def get_premium_emoji_for_normal_emoji(normal_emoji: str) -> str:
    now = time.time()
    cached = _emoji_id_cache.get(normal_emoji)
    if cached and (now - cached[1]) < EMOJI_CACHE_TTL:
        return cached[0]
    key = normal_emoji
    if key not in EMOJI_MAPPING and key not in FLAG_MAPPING:
        key = _normalize_emoji(normal_emoji)
    if key in EMOJI_MAPPING:
        chosen = random.choice(EMOJI_MAPPING[key])
    elif key in FLAG_MAPPING:
        chosen = FLAG_MAPPING[key]
    else:
        chosen = random.choice(ALL_PREMIUM_EMOJIS)
    _emoji_id_cache[normal_emoji] = (chosen, now)
    return chosen

def get_random_primary_emoji() -> str:
    return random.choice(PRIMARY_EMOJIS)

# ============================================================
# BUTTON CREATION
# ============================================================

def _extract_first_emoji(text: str):
    import unicodedata
    chars = list(text)
    i = 0
    while i < len(chars):
        ch = chars[i]
        if (i + 1 < len(chars)
                and '\U0001F1E0' <= ch <= '\U0001F1FF'
                and '\U0001F1E0' <= chars[i + 1] <= '\U0001F1FF'):
            seq = ch + chars[i + 1]
            cleaned = "".join(chars[:i] + chars[i + 2:]).strip()
            return seq, cleaned
        if emoji.is_emoji(ch):
            seq = ch
            j = i + 1
            while j < len(chars) and (
                chars[j] in ('\u200d', '\ufe0f', '\ufe0e')
                or unicodedata.category(chars[j]) in ('Mn', 'Mc')
                or '\U0001F3FB' <= chars[j] <= '\U0001F3FF'
            ):
                seq += chars[j]
                j += 1
            cleaned = "".join(chars[:i] + chars[j:]).strip()
            return seq, cleaned
        i += 1
    return None, text

def _make_btn(text: str, style: str = None, icon_id: str = None, **kwargs) -> InlineKeyboardButton:
    if icon_id and style:
        try:
            return InlineKeyboardButton(text=text, icon_custom_emoji_id=icon_id, style=style, **kwargs)
        except TypeError:
            pass
    if icon_id:
        try:
            return InlineKeyboardButton(text=text, icon_custom_emoji_id=icon_id, **kwargs)
        except TypeError:
            pass
    if style:
        try:
            return InlineKeyboardButton(text=text, style=style, **kwargs)
        except TypeError:
            pass
    return InlineKeyboardButton(text=text, **kwargs)

def make_button(text: str, style: str = None, **kwargs) -> InlineKeyboardButton:
    """Create button for user's custom buttons - NO auto emoji conversion"""
    return _make_btn(text, style=style, **kwargs)

def make_button_with_icon(text: str, style: str = None, **kwargs) -> InlineKeyboardButton:
    """Create button WITH premium emoji icon for bot's own buttons"""
    first_emoji, _ = _extract_first_emoji(text)
    if first_emoji:
        premium_id = get_premium_emoji_for_normal_emoji(first_emoji)
    else:
        premium_id = get_random_primary_emoji()
    return _make_btn(text, style=style, icon_id=premium_id, **kwargs)

def make_styled_row(buttons_config: list) -> list:
    row = []
    for cfg in buttons_config:
        cfg_copy = cfg.copy()
        text = cfg_copy.pop("text")
        style = cfg_copy.pop("style", None)
        row.append(make_button_with_icon(text, style=style, **cfg_copy))
    return row

# ============================================================
# FORCE JOIN CHANNELS
# ============================================================
REQUIRED_CHANNELS = [
    {"id": "-1003788328311", "name": "💀⃤ 𝐃𝐄𝐕 𝐖𝐎𝐑𝐋𝐃 ⚡", "link": "https://t.me/DEVWORLDOFFICIALCHANNEL"},
    {"id": "-1003728347874", "name": "💀⃤ 𝐈𝐌𝐀𝐗 𝐁𝐀𝐂𝐊𝐔𝐏", "link": "https://t.me/imaxbackup"},
    {"id": "-1003901804566", "name": "💀⃤ 𝐈𝐌𝐀𝐗 𝐗 𝐍𝐎𝐕𝐀", "link": "https://t.me/imaxXnova"},
]

def _utf16_len(ch: str) -> int:
    return len(ch.encode("utf-16-le")) // 2

def _utf16_len_str(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2

def _build_pe_entities(text: str, use_primary: bool = True):
    entities = []
    utf16_offset = 0
    total_utf16 = _utf16_len_str(text)
    
    if total_utf16 > 0:
        entities.append(MessageEntity(type="bold", offset=0, length=total_utf16))
    
    for ch in text:
        ch_len = _utf16_len(ch)
        if ch == PLACEHOLDER:
            eid = random.choice(PRIMARY_EMOJIS) if use_primary else random.choice(ALL_PREMIUM_EMOJIS)
            entities.append(MessageEntity(
                type="custom_emoji",
                offset=utf16_offset,
                length=ch_len,
                custom_emoji_id=eid
            ))
        utf16_offset += ch_len
    
    return entities

def _send_pe(chat_id, text: str, use_primary: bool = True, reply_markup=None):
    entities = _build_pe_entities(text, use_primary)
    return bot.send_message(chat_id, text, entities=entities, reply_markup=reply_markup, parse_mode=None)

def _send_pe_return(chat_id, text: str, use_primary: bool = True, reply_markup=None):
    entities = _build_pe_entities(text, use_primary)
    return bot.send_message(chat_id, text, entities=entities, reply_markup=reply_markup, parse_mode=None)

def build_code_list_message(header: str, footer: str, code_items: list):
    """Builds a message listing 'code  emoji' lines, where each code is wrapped
    as a tap-to-copy 'code' entity and each PLACEHOLDER resolves to the real emoji.
    code_items: list of (code, emoji_id) tuples."""
    lines_text = ""
    line_specs = []
    for code, eid in code_items:
        lines_text += f"{code}  {PLACEHOLDER}\n"
        line_specs.append((code, eid))

    full_text = header + lines_text + footer
    entities = _build_pe_entities(full_text)

    search_from = 0
    for code, eid in line_specs:
        marker = f"{code}  {PLACEHOLDER}"
        pos = full_text.find(marker, search_from)
        if pos == -1:
            continue
        code_offset = _utf16_len_str(full_text[:pos])
        code_len = _utf16_len_str(code)
        entities.append(MessageEntity(type="code", offset=code_offset, length=code_len))

        ph_offset = _utf16_len_str(full_text[:pos + len(f"{code}  ")])
        for ent in entities:
            if ent.type == "custom_emoji" and ent.offset == ph_offset:
                ent.custom_emoji_id = eid
                break
        search_from = pos + len(marker)

    return full_text, entities

def process_text_and_entities(text: str, original_entities: list, uid: int = None):
    final_text = ""
    new_entities = []
    offset_map = {}
    old_off = 0
    new_off = 0

    user_codes = get_user_custom_codes(uid) if uid is not None else {}

    # Find all "#1234" or "#C12"-style codes in the text so we can treat each as one token
    code_spans = []  # (start_index, end_index_exclusive, code_str)
    for m in re.finditer(r"#C?\d+", text):
        code_spans.append((m.start(), m.end(), m.group()))

    i = 0
    n = len(text)
    span_idx = 0
    while i < n:
        # Check if a recognized #code starts here
        matched_span = None
        if span_idx < len(code_spans) and code_spans[span_idx][0] == i:
            matched_span = code_spans[span_idx]
            span_idx += 1

        if matched_span:
            start, end, code = matched_span
            emoji_id = user_codes.get(code) or code_to_emoji.get(code)
            if emoji_id:
                for k in range(start, end):
                    offset_map[old_off] = new_off
                    old_off += _utf16_len(text[k])
                ph_len = _utf16_len(PLACEHOLDER)
                new_entities.append(MessageEntity(
                    type="custom_emoji",
                    offset=new_off,
                    length=ph_len,
                    custom_emoji_id=emoji_id
                ))
                final_text += PLACEHOLDER
                new_off += ph_len
                i = end
                continue
            else:
                # Unknown code — leave as literal text
                for k in range(start, end):
                    offset_map[old_off] = new_off
                    ch_len = _utf16_len(text[k])
                    final_text += text[k]
                    old_off += ch_len
                    new_off += ch_len
                i = end
                continue

        char = text[i]
        offset_map[old_off] = new_off
        old_ch_len = _utf16_len(char)

        if emoji.is_emoji(char):
            premium_id = get_premium_emoji_for_normal_emoji(char)
            ph_len = _utf16_len(PLACEHOLDER)
            new_entities.append(MessageEntity(
                type="custom_emoji",
                offset=new_off,
                length=ph_len,
                custom_emoji_id=premium_id
            ))
            final_text += PLACEHOLDER
            old_off += old_ch_len
            new_off += ph_len
        else:
            final_text += char
            old_off += old_ch_len
            new_off += old_ch_len
        i += 1

    offset_map[old_off] = new_off
    
    for ent in (original_entities or []):
        if ent.type == "custom_emoji":
            continue
        ns = offset_map.get(ent.offset)
        ne = offset_map.get(ent.offset + ent.length)
        if ns is not None and ne is not None and ne > ns:
            new_entities.append(MessageEntity(
                type=ent.type,
                offset=ns,
                length=ne - ns,
                url=ent.url,
                user=ent.user,
                language=ent.language,
                custom_emoji_id=ent.custom_emoji_id
            ))
    
    if final_text:
        total_len = _utf16_len_str(final_text)
        new_entities.append(MessageEntity(type="bold", offset=0, length=total_len))
    
    return final_text, new_entities

def is_admin(user_id):
    return user_id in ADMIN_IDS

def check_joined(uid: int) -> list:
    if is_admin(uid):
        return []
    
    not_joined = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = bot.get_chat_member(ch["id"], uid)
            if member.status in ("left", "kicked", "banned"):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined

def send_join_notice(chat_id: int, not_joined: list):
    joined_count = len(REQUIRED_CHANNELS) - len(not_joined)
    total_count = len(REQUIRED_CHANNELS)
    
    keyboard = []
    for ch in not_joined:
        keyboard.append([InlineKeyboardButton(text=f"📢 {ch['name']}", url=ch["link"])])
    keyboard.append([make_button_with_icon(text="✅ 𝐈 𝐇𝐀𝐕𝐄 𝐉𝐎𝐈𝐍𝐄𝐃", style="success", callback_data="check_join")])
    markup = InlineKeyboardMarkup(keyboard)
    
    status_text = ""
    for ch in REQUIRED_CHANNELS:
        if ch in not_joined:
            status_text += f"📢  ❌ {ch['name']}\n"
        else:
            status_text += f"📢  ✅ {ch['name']}\n"
    
    text = f"""
{PLACEHOLDER}═══《 🔒 𝐀𝐂𝐂𝐄𝐒𝐒 𝐃𝐄𝐍𝐈𝐄𝐃! 》═══{PLACEHOLDER}

🚫 𝐀𝐂𝐂𝐄𝐒𝐒 𝐃𝐄𝐍𝐈𝐄𝐃!

📊 𝐏𝐑𝐎𝐆𝐑𝐄𝐒𝐒: {joined_count}/{total_count} 𝐉𝐎𝐈𝐍𝐄𝐃

⚠️ 𝐓𝐎 𝐔𝐒𝐄 𝐓𝐇𝐈𝐒 𝐁𝐎𝐓, 𝐘𝐎𝐔 𝐌𝐔𝐒𝐓 𝐉𝐎𝐈𝐍 𝐀𝐋𝐋 𝐂𝐇𝐀𝐍𝐍𝐄𝐋𝐒 𝐅𝐈𝐑𝐒𝐓!

{status_text}
👇 𝐂𝐋𝐈𝐂𝐊 𝐓𝐇𝐄 𝐁𝐔𝐓𝐓𝐎𝐍𝐒 𝐁𝐄𝐋𝐎𝐖 𝐓𝐎 𝐉𝐎𝐈𝐍: 👇

{PLACEHOLDER}═══════════════════════{PLACEHOLDER}
"""
    _send_pe(chat_id, text, reply_markup=markup)

def register_user(uid: int, username: str = None):
    if uid not in all_users:
        all_users.add(uid)
        save_users(all_users)
    if username is not None:
        key = str(uid)
        if user_usernames.get(key) != username:
            user_usernames[key] = username
            save_usernames(user_usernames)

# ============================================================
# MAIN MENU - WITH EMOJI BUTTONS
# ============================================================
def get_menu(user_id):
    """Returns keyboard menu with emoji buttons"""
    is_admin_user = is_admin(user_id)
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    
    if is_admin_user:
        markup.row(
            KeyboardButton("🔴 𝐁𝐎𝐓 𝐎𝐅𝐅"),
            KeyboardButton("🟢 𝐁𝐎𝐓 𝐎𝐍")
        )
        markup.row(
            KeyboardButton("🌿 𝐌𝐀𝐊𝐄 𝐏𝐎𝐒𝐓"),
            KeyboardButton("💢 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓")
        )
        markup.row(
            KeyboardButton("👾 𝐔𝐒𝐄𝐑𝐒 𝐋𝐈𝐒𝐓"),
            KeyboardButton("🍁 𝐁𝐎𝐓 𝐒𝐓𝐀𝐓𝐒")
        )
        markup.row(
            KeyboardButton("🧩 𝐄𝐌𝐎𝐉𝐈 𝐋𝐈𝐒𝐓"),
            KeyboardButton("➕ 𝐀𝐃𝐃 𝐄𝐌𝐎𝐉𝐈 𝐏𝐀𝐂𝐊")
        )
        markup.row(
            KeyboardButton("🎨 𝐒𝐄𝐓 𝐂𝐔𝐒𝐓𝐎𝐌 𝐏𝐀𝐂𝐊"),
        )
        markup.row(
            KeyboardButton("🍂 𝐇𝐄𝐋𝐏"),
            KeyboardButton("🍀 𝐀𝐁𝐎𝐔𝐓 𝐁𝐎𝐓")
        )
        markup.row(
            KeyboardButton("🔎 𝐒𝐄𝐀𝐑𝐂𝐇 𝐄𝐌𝐎𝐉𝐈"),
            KeyboardButton("📂 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒")
        )
        markup.row(
            KeyboardButton("⭐ 𝐌𝐘 𝐅𝐀𝐕𝐎𝐑𝐈𝐓𝐄𝐒"),
            KeyboardButton("🗂 𝐌𝐀𝐍𝐀𝐆𝐄 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒")
        )
        markup.row(
            KeyboardButton("📝 𝐌𝐘 𝐃𝐑𝐀𝐅𝐓𝐒"),
            KeyboardButton("📑 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄𝐒")
        )
        markup.row(
            KeyboardButton("👤 𝐌𝐘 𝐏𝐑𝐎𝐅𝐈𝐋𝐄"),
            KeyboardButton("👥 𝐑𝐄𝐅𝐄𝐑𝐑𝐀𝐋𝐒")
        )
        markup.row(KeyboardButton("🛍 𝐄𝐌𝐎𝐉𝐈 𝐒𝐓𝐎𝐑𝐄"))
        markup.row(KeyboardButton("📈 𝐃𝐄𝐓𝐀𝐈𝐋𝐄𝐃 𝐀𝐍𝐀𝐋𝐘𝐓𝐈𝐂𝐒"))
    else:
        markup.row(KeyboardButton("🌿 𝐌𝐀𝐊𝐄 𝐏𝐎𝐒𝐓"))
        markup.row(
            KeyboardButton("🧩 𝐄𝐌𝐎𝐉𝐈 𝐋𝐈𝐒𝐓"),
            KeyboardButton("🎨 𝐒𝐄𝐓 𝐂𝐔𝐒𝐓𝐎𝐌 𝐏𝐀𝐂𝐊")
        )
        markup.row(
            KeyboardButton("🔎 𝐒𝐄𝐀𝐑𝐂𝐇 𝐄𝐌𝐎𝐉𝐈"),
            KeyboardButton("📂 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒")
        )
        markup.row(KeyboardButton("⭐ 𝐌𝐘 𝐅𝐀𝐕𝐎𝐑𝐈𝐓𝐄𝐒"))
        markup.row(
            KeyboardButton("📝 𝐌𝐘 𝐃𝐑𝐀𝐅𝐓𝐒"),
            KeyboardButton("📑 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄𝐒")
        )
        markup.row(
            KeyboardButton("👤 𝐌𝐘 𝐏𝐑𝐎𝐅𝐈𝐋𝐄"),
            KeyboardButton("👥 𝐑𝐄𝐅𝐄𝐑𝐑𝐀𝐋𝐒")
        )
        markup.row(KeyboardButton("🛍 𝐄𝐌𝐎𝐉𝐈 𝐒𝐓𝐎𝐑𝐄"))
        markup.row(
            KeyboardButton("🍂 𝐇𝐄𝐋𝐏"),
            KeyboardButton("🍀 𝐀𝐁𝐎𝐔𝐓 𝐁𝐎𝐓")
        )
        markup.row(KeyboardButton("🪾 𝐒𝐓𝐀𝐓𝐒"))
    
    return markup

def send_welcome_message(user_name: str, user_id: int):
    status = "𝐀𝐃𝐌𝐈𝐍" if is_admin(user_id) else "𝐔𝐒𝐄𝐑"
    
    text = f"""
{PLACEHOLDER}═══《 {PLACEHOLDER} 𝐆𝐨𝐨𝐝 𝐀𝐟𝐭𝐞𝐫𝐧𝐨𝐨𝐧! {PLACEHOLDER} 》═══{PLACEHOLDER}

{PLACEHOLDER} 𝐔𝐬𝐞𝐫: {user_name}
{PLACEHOLDER} 𝐔𝐬𝐞𝐫 𝐈𝐃: {user_id}
{PLACEHOLDER} 𝐒𝐭𝐚𝐭𝐮𝐬: {status}

╰═══════《 {PLACEHOLDER} 》═══════{PLACEHOLDER}

𝐖𝐞𝐥𝐜𝐨𝐦𝐞 𝐭𝐨 𝙄𝙈𝘼𝙓 𝙋𝙍𝙀𝙈𝙄𝙐𝙈 𝙀𝙈𝙊𝙅𝙄

{PLACEHOLDER} 𝐀𝐛𝐨𝐮𝐭 𝐓𝐡𝐢𝐬 𝐁𝐨𝐭:
• {PLACEHOLDER} 𝐏𝐫𝐞𝐦𝐢𝐮𝐦 𝐀𝐧𝐢𝐦𝐚𝐭𝐞𝐝 𝐄𝐦𝐨𝐣𝐢 𝐂𝐨𝐧𝐯𝐞𝐫𝐭𝐞𝐫
• {PLACEHOLDER} 𝐂𝐨𝐧𝐯𝐞𝐫𝐭 𝐚𝐧𝐲 𝐞𝐦𝐨𝐣𝐢 𝐭𝐨 𝐩𝐫𝐞𝐦𝐢𝐮𝐦
• {PLACEHOLDER} 𝐏𝐫𝐞𝐬𝐞𝐫𝐯𝐞𝐬 𝐚𝐥𝐥 𝐟𝐨𝐫𝐦𝐚𝐭𝐭𝐢𝐧𝐠
• {PLACEHOLDER} 𝐒𝐮𝐩𝐩𝐨𝐫𝐭𝐬 𝐭𝐞𝐱𝐭, 𝐩𝐡𝐨𝐭𝐨, 𝐯𝐢𝐝𝐞𝐨, 𝐝𝐨𝐜𝐮𝐦𝐞𝐧𝐭
• {PLACEHOLDER} 𝐀𝐝𝐝 𝐦𝐮𝐥𝐭𝐢𝐩𝐥𝐞 𝐢𝐧𝐥𝐢𝐧𝐞 𝐛𝐮𝐭𝐭𝐨𝐧𝐬

━━━━━━━━━━━━━━━━━━━━━━

{PLACEHOLDER} 𝐀𝐜𝐜𝐞𝐬𝐬 𝐆𝐫𝐚𝐧𝐭𝐞𝐝!
𝐘𝐨𝐮 𝐡𝐚𝐯𝐞 𝐬𝐮𝐜𝐜𝐞𝐬𝐬𝐟𝐮𝐥𝐥𝐲 𝐣𝐨𝐢𝐧𝐞𝐝 𝐚𝐥𝐥 𝐜𝐡𝐚𝐧𝐧𝐞𝐥𝐬.

{PLACEHOLDER} 𝐐𝐮𝐢𝐜𝐤 𝐆𝐮𝐢𝐝𝐞:
• 𝐔𝐬𝐞 𝐦𝐞𝐧𝐮 𝐛𝐮𝐭𝐭𝐨𝐧𝐬 𝐭𝐨 𝐧𝐚𝐯𝐢𝐠𝐚𝐭𝐞
• /𝐡𝐞𝐥𝐩 𝐟𝐨𝐫 𝐦𝐨𝐫𝐞 𝐢𝐧𝐟𝐨𝐫𝐦𝐚𝐭𝐢𝐨𝐧
• 𝐌𝐀𝐊𝐄 𝐏𝐎𝐒𝐓 𝐭𝐨 𝐜𝐫𝐞𝐚𝐭𝐞

⚠️ 𝐍𝐨𝐭𝐞: 𝐈𝐟 𝐲𝐨𝐮 𝐥𝐞𝐚𝐯𝐞 𝐚𝐧𝐲 𝐜𝐡𝐚𝐧𝐧𝐞𝐥, 𝐲𝐨𝐮 𝐰𝐢𝐥𝐥 𝐥𝐨𝐬𝐞 𝐚𝐜𝐜𝐞𝐬𝐬!

{PLACEHOLDER}━━━━━━━━━━━━━━━━━━━━━━{PLACEHOLDER}
"""
    return text

# Helper function to match button text with or without emoji prefix
def button_matches(text: str, *targets) -> bool:
    """Check if button text matches any target, ignoring emoji prefix"""
    if not text:
        return False
    # Strip any emoji prefix (like 🌿, 🔴, 🟢, etc.)
    cleaned = text.strip()
    # Remove first emoji if present
    first_char = cleaned[0] if cleaned else ""
    if emoji.is_emoji(first_char):
        cleaned = cleaned[1:].strip()
    for target in targets:
        if cleaned == target or text == target:
            return True
    return False

# ============================================================
# BOT COMMAND HANDLERS - UPDATED WITH EMOJI SUPPORT
# ============================================================

@bot.message_handler(commands=["start"])
def welcome(message):
    uid = message.from_user.id
    
    if not is_admin(uid):
        not_joined = check_joined(uid)
        if not_joined:
            send_join_notice(message.chat.id, not_joined)
            return
    
    register_user(uid, message.from_user.username)
    
    if not bot_active and not is_admin(uid):
        text = f"""
{PLACEHOLDER}═══《 🔴 𝐁𝐎𝐓 𝐎𝐅𝐅𝐋𝐈𝐍𝐄 》═══{PLACEHOLDER}

𝐁𝐎𝐓 𝐈𝐒 𝐍𝐎𝐖 𝐎𝐅𝐅𝐋𝐈𝐍𝐄.

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
        _send_pe(message.chat.id, text)
        return
    
    name = message.from_user.first_name or "Friend"
    kb = get_menu(uid)
    text = send_welcome_message(name, uid)
    _send_pe(message.chat.id, text, reply_markup=kb)

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐇𝐄𝐋𝐏"))
def help_msg(message):
    uid = message.from_user.id
    kb = get_menu(uid)
    
    text = f"""
{PLACEHOLDER}═══《 {PLACEHOLDER} 𝐇𝐎𝐖 𝐓𝐎 𝐔𝐒𝐄 》═══{PLACEHOLDER}

𝐒𝐓𝐄𝐏 𝟏: 𝐓𝐀𝐏 𝐌𝐀𝐊𝐄 𝐏𝐎𝐒𝐓

𝐒𝐓𝐄𝐏 𝟐: 𝐒𝐄𝐍𝐃 𝐘𝐎𝐔𝐑 𝐓𝐄𝐗𝐓 𝐌𝐄𝐒𝐒𝐀𝐆𝐄

𝐒𝐓𝐄𝐏 𝟑: 𝐂𝐇𝐎𝐎𝐒𝐄 𝐌𝐄𝐃𝐈𝐀 (𝐨𝐫 𝐬𝐤𝐢𝐩)

𝐒𝐓𝐄𝐏 𝟒: 𝐀𝐃𝐃 𝐁𝐔𝐓𝐓𝐎𝐍𝐒 (𝐮𝐩 𝐭𝐨 𝟏𝟎, 𝐨𝐩𝐭𝐢𝐨𝐧𝐚𝐥)

𝐒𝐓𝐄𝐏 𝟓: 𝐏𝐑𝐄𝐕𝐈𝐄𝐖, 𝐑𝐄𝐅𝐑𝐄𝐒𝐇, 𝐎𝐑 𝐒𝐀𝐕𝐄 𝐀𝐒 𝐃𝐑𝐀𝐅𝐓/𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄

𝐒𝐓𝐄𝐏 𝟔: 𝐃𝐎𝐍𝐄 𝐎𝐑 𝐅𝐎𝐑𝐖𝐀𝐑𝐃!

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    _send_pe(message.chat.id, text, reply_markup=kb)

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐀𝐁𝐎𝐔𝐓 𝐁𝐎𝐓"))
def about_bot(message):
    uid = message.from_user.id
    kb = get_menu(uid)
    total = len(all_users)
    
    text = f"""
{PLACEHOLDER}═══《 🔥 𝐀𝐁𝐎𝐔𝐓 𝐁𝐎𝐓 》═══{PLACEHOLDER}

𝐓𝐇𝐈𝐒 𝐁𝐎𝐓 𝐂𝐎𝐍𝐕𝐄𝐑𝐓𝐒 𝐍𝐎𝐑𝐌𝐀𝐋 𝐄𝐌𝐎𝐉𝐈𝐒 𝐓𝐎
𝐓𝐄𝐋𝐄𝐆𝐑𝐀𝐌 𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐀𝐍𝐈𝐌𝐀𝐓𝐄𝐃 𝐄𝐌𝐎𝐉𝐈𝐒

📊 𝐒𝐓𝐀𝐓𝐈𝐒𝐓𝐈𝐂𝐒:
• 𝐓𝐎𝐓𝐀𝐋 𝐔𝐒𝐄𝐑𝐒: {total}
• 𝐄𝐌𝐎𝐉𝐈 𝐏𝐎𝐎𝐋: {len(ALL_PREMIUM_EMOJIS)}
• 𝐅𝐋𝐀𝐆𝐒: {len(FLAG_MAPPING)}
• 𝐕𝐄𝐑𝐒𝐈𝐎𝐍: 7.0

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    _send_pe(message.chat.id, text, reply_markup=kb)

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐒𝐓𝐀𝐓𝐒", "𝐁𝐎𝐓 𝐒𝐓𝐀𝐓𝐒"))
def stats_msg(message):
    uid = message.from_user.id
    kb = get_menu(uid)
    total = len(all_users)
    status = "🟢 𝐎𝐍𝐋𝐈𝐍𝐄" if bot_active else "🔴 𝐎𝐅𝐅𝐋𝐈𝐍𝐄"
    
    text = f"""
{PLACEHOLDER}═══《 📊 𝐒𝐓𝐀𝐓𝐈𝐒𝐓𝐈𝐂𝐒 》═══{PLACEHOLDER}

𝐁𝐎𝐓 𝐒𝐓𝐀𝐓𝐔𝐒: {status}
𝐓𝐎𝐓𝐀𝐋 𝐔𝐒𝐄𝐑𝐒: {total}
𝐄𝐌𝐎𝐉𝐈 𝐏𝐎𝐎𝐋: {len(ALL_PREMIUM_EMOJIS)}
𝐅𝐋𝐀𝐆𝐒: {len(FLAG_MAPPING)}

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    _send_pe(message.chat.id, text, reply_markup=kb)

# ============================================================
# MAKE POST HANDLERS
# ============================================================
@bot.message_handler(func=lambda m: button_matches(m.text, "𝐌𝐀𝐊𝐄 𝐏𝐎𝐒𝐓"))
def start_post(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    
    if not bot_active and not is_admin(uid):
        text = f"{PLACEHOLDER} 𝐁𝐎𝐓 𝐈𝐒 𝐎𝐅𝐅𝐋𝐈𝐍𝐄."
        _send_pe(message.chat.id, text)
        return
    
    temp_data[uid] = {
        "original_text": "",
        "original_entities": [],
        "media_type": None,
        "media_id": None,
        "media_name": None,
        "buttons": [],
        "refresh_count": 0,
        "processed_text": "",
        "processed_entities": [],
        "preview_msg_id": None,
        "action_msg_id": None,
    }
    
    text = f"""
{PLACEHOLDER}═══《 ✍️ 𝐂𝐑𝐄𝐀𝐓𝐄 𝐏𝐎𝐒𝐓 》═══{PLACEHOLDER}

𝐏𝐋𝐄𝐀𝐒𝐄 𝐒𝐄𝐍𝐃 𝐘𝐎𝐔𝐑 𝐓𝐄𝐗𝐓 𝐌𝐄𝐒𝐒𝐀𝐆𝐄 𝐍𝐎𝐖.

𝐒𝐔𝐏𝐏𝐎𝐑𝐓𝐒: 𝐁𝐎𝐋𝐃, 𝐈𝐓𝐀𝐋𝐈𝐂, 𝐋𝐈𝐍𝐊𝐒
🏳️ 𝐅𝐋𝐀𝐆𝐒 & 𝐄𝐌𝐎𝐉𝐈𝐒 𝐖𝐈𝐋𝐋 𝐁𝐄 𝐂𝐎𝐍𝐕𝐄𝐑𝐓𝐄𝐃!

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    sent = _send_pe_return(message.chat.id, text)
    bot.register_next_step_handler(sent, process_post_text)

def process_post_text(message):
    uid = message.from_user.id
    
    if message.text and message.text.strip() == "/cancel":
        kb = get_menu(uid)
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=kb)
        temp_data.pop(uid, None)
        return
    
    if uid not in temp_data:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐒𝐄𝐒𝐒𝐈𝐎𝐍 𝐄𝐗𝐏𝐈𝐑𝐄𝐃! 𝐏𝐋𝐄𝐀𝐒𝐄 /𝐒𝐓𝐀𝐑𝐓 𝐀𝐆𝐀𝐈𝐍.")
        return
    
    temp_data[uid]["original_text"] = message.text or ""
    temp_data[uid]["original_entities"] = message.entities or []
    
    ask_media_type(message.chat.id, uid)

def ask_media_type(chat_id: int, uid: int):
    text = f"""
{PLACEHOLDER}═══《 {PLACEHOLDER} 𝐀𝐃𝐃 𝐌𝐄𝐃𝐈𝐀 》═══{PLACEHOLDER}

𝐃𝐎 𝐘𝐎𝐔 𝐖𝐀𝐍𝐓 𝐓𝐎 𝐀𝐃𝐃 𝐌𝐄𝐃𝐈𝐀?

{PLACEHOLDER}═══════════════════{PLACEHOLDER}
"""
    keyboard = [
        make_styled_row([
            {"text": "𝐕𝐈𝐃𝐄𝐎", "style": "primary", "callback_data": f"media_video_{uid}"},
            {"text": "𝐈𝐌𝐀𝐆𝐄", "style": "primary", "callback_data": f"media_image_{uid}"},
        ]),
        make_styled_row([
            {"text": "𝐃𝐎𝐂𝐔𝐌𝐄𝐍𝐓", "style": "primary", "callback_data": f"media_doc_{uid}"},
            {"text": "𝐒𝐊𝐈𝐏", "style": "danger", "callback_data": f"media_skip_{uid}"},
        ]),
    ]
    markup = InlineKeyboardMarkup(keyboard)
    _send_pe(chat_id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("media_"))
def handle_media_selection(call):
    parts = call.data.split("_")
    action = parts[1]
    uid = int(parts[2]) if len(parts) > 2 else call.from_user.id
    chat_id = call.message.chat.id
    
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass
    
    if uid not in temp_data:
        bot.answer_callback_query(call.id, "Session expired!")
        return
    
    if action == "skip":
        temp_data[uid]["media_type"] = None
        button_editor_hub(chat_id, uid)
    
    elif action in ["video", "image", "doc"]:
        temp_data[uid]["media_type"] = action
        media_text = {"video": "𝐕𝐈𝐃𝐄𝐎", "image": "𝐈𝐌𝐀𝐆𝐄", "doc": "𝐃𝐎𝐂𝐔𝐌𝐄𝐍𝐓"}[action]
        
        text = f"""
{PLACEHOLDER}═══《 📤 𝐒𝐄𝐍𝐃 {media_text} 》═══{PLACEHOLDER}

𝐏𝐋𝐄𝐀𝐒𝐄 𝐒𝐄𝐍𝐃 𝐘𝐎𝐔𝐑 {media_text} 𝐍𝐎𝐖.

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
        
        sent = _send_pe_return(chat_id, text)
        bot.register_next_step_handler(sent, receive_media, action)
    
    bot.answer_callback_query(call.id)

def receive_media(message, media_type):
    uid = message.from_user.id
    
    if message.text and message.text.strip() == "/cancel":
        kb = get_menu(uid)
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=kb)
        temp_data.pop(uid, None)
        return
    
    if uid not in temp_data:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐒𝐄𝐒𝐒𝐈𝐎𝐍 𝐄𝐗𝐏𝐈𝐑𝐄𝐃!")
        return
    
    if media_type == "video" and message.video:
        temp_data[uid]["media_id"] = message.video.file_id
        if message.caption:
            temp_data[uid]["original_text"] = message.caption
            temp_data[uid]["original_entities"] = message.caption_entities or []
    elif media_type == "image" and message.photo:
        temp_data[uid]["media_id"] = message.photo[-1].file_id
        if message.caption:
            temp_data[uid]["original_text"] = message.caption
            temp_data[uid]["original_entities"] = message.caption_entities or []
    elif media_type == "doc" and message.document:
        temp_data[uid]["media_id"] = message.document.file_id
        if message.caption:
            temp_data[uid]["original_text"] = message.caption
            temp_data[uid]["original_entities"] = message.caption_entities or []
    else:
        text = f"{PLACEHOLDER} 𝐏𝐋𝐄𝐀𝐒𝐄 𝐒𝐄𝐍𝐃 𝐀 𝐕𝐀𝐋𝐈𝐃 {media_type.upper()}!"
        sent = _send_pe_return(message.chat.id, text)
        bot.register_next_step_handler(sent, receive_media, media_type)
        return
    
    button_editor_hub(message.chat.id, uid)

# ============================================================
# BUTTON BUILDER 2.0
# ============================================================
# Replaces the old fixed "pick 0-4 buttons upfront, then walk through
# name/URL/color per slot with no way back" flow.
#
# temp_data[uid]["buttons"] is a list of dicts:
#   {"name": str, "url": str, "color": "primary"|"danger"|"success"|None, "row": int}
# "row" groups buttons onto the same keyboard row (0-indexed). Buttons
# with the same row number render side by side, left to right in the
# order they appear in the list.
#
# Hub screen (button_editor_hub) is the home base: shows the buttons
# as a live keyboard preview plus management actions. Every action
# returns to the hub except Done, which moves on to post preview.
# ============================================================

MAX_BUTTONS = 10
MAX_BUTTONS_PER_ROW = 3   # Telegram inline rows get cramped past ~3-4 short buttons

def _next_free_row(buttons: list) -> int:
    """New buttons default to the last row if it has room, else a new row."""
    if not buttons:
        return 0
    counts = {}
    for b in buttons:
        counts[b.get("row", 0)] = counts.get(b.get("row", 0), 0) + 1
    last_row = max(counts.keys())
    if counts[last_row] < MAX_BUTTONS_PER_ROW:
        return last_row
    return last_row + 1

def validate_button_url(url: str):
    """Returns (is_valid, normalized_url_or_error_message)."""
    url = (url or "").strip()
    if not url:
        return False, "𝐔𝐑𝐋 𝐂𝐀𝐍𝐍𝐎𝐓 𝐁𝐄 𝐄𝐌𝐏𝐓𝐘."
    if " " in url or "\n" in url or "\t" in url:
        return False, "𝐔𝐑𝐋 𝐂𝐀𝐍𝐍𝐎𝐓 𝐂𝐎𝐍𝐓𝐀𝐈𝐍 𝐒𝐏𝐀𝐂𝐄𝐒."
    valid_schemes = ("http://", "https://", "tg://")
    if not url.startswith(valid_schemes):
        return False, "𝐔𝐑𝐋 𝐌𝐔𝐒𝐓 𝐒𝐓𝐀𝐑𝐓 𝐖𝐈𝐓𝐇 https:// (𝐨𝐫 tg://)."
    if url.startswith(("http://", "https://")):
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if not parsed.netloc or "." not in parsed.netloc:
                return False, "𝐓𝐇𝐀𝐓 𝐃𝐎𝐄𝐒𝐍'𝐓 𝐋𝐎𝐎𝐊 𝐋𝐈𝐊𝐄 𝐀 𝐕𝐀𝐋𝐈𝐃 𝐖𝐄𝐁 𝐔𝐑𝐋."
        except Exception:
            return False, "𝐂𝐎𝐔𝐋𝐃𝐍'𝐓 𝐏𝐀𝐑𝐒𝐄 𝐓𝐇𝐀𝐓 𝐔𝐑𝐋."
    if len(url) > 512:
        return False, "𝐔𝐑𝐋 𝐈𝐒 𝐓𝐎𝐎 𝐋𝐎𝐍𝐆."
    return True, url

def build_buttons_markup(buttons: list):
    """Groups a flat button list into keyboard rows by their 'row' field."""
    if not buttons:
        return None
    rows = {}
    for btn in buttons:
        rows.setdefault(btn.get("row", 0), []).append(btn)
    keyboard = []
    for row_num in sorted(rows.keys()):
        keyboard.append([
            make_button(text=b["name"], style=b.get("color"), url=b["url"])
            for b in rows[row_num]
        ])
    return InlineKeyboardMarkup(keyboard)

def button_editor_hub(chat_id, uid):
    if uid not in temp_data:
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐒𝐒𝐈𝐎𝐍 𝐄𝐗𝐏𝐈𝐑𝐄𝐃!")
        return

    buttons = temp_data[uid].get("buttons", [])
    count = len(buttons)

    text = f"""
{PLACEHOLDER}═══《 🔘 𝐁𝐔𝐓𝐓𝐎𝐍 𝐁𝐔𝐈𝐋𝐃𝐄𝐑 》═══{PLACEHOLDER}

𝐁𝐔𝐓𝐓𝐎𝐍𝐒: {count}/{MAX_BUTTONS}

"""
    if not buttons:
        text += "𝐍𝐎 𝐁𝐔𝐓𝐓𝐎𝐍𝐒 𝐘𝐄𝐓. 𝐓𝐀𝐏 ➕ 𝐀𝐃𝐃 𝐓𝐎 𝐂𝐑𝐄𝐀𝐓𝐄 𝐎𝐍𝐄.\n\n"
    else:
        text += "𝐘𝐎𝐔𝐑 𝐁𝐔𝐓𝐓𝐎𝐍𝐒 (𝐩𝐫𝐞𝐯𝐢𝐞𝐰𝐞𝐝 𝐛𝐞𝐥𝐨𝐰):\n\n"
    text += f"{PLACEHOLDER}═════════════════════{PLACEHOLDER}\n"

    keyboard = []

    # Live preview of the buttons as they'll actually appear
    preview_markup = build_buttons_markup(buttons)
    if preview_markup:
        keyboard.extend(preview_markup.keyboard)

    # Management row(s)
    management = []
    if count < MAX_BUTTONS:
        management.append({"text": "➕ 𝐀𝐃𝐃", "style": "success", "callback_data": f"bbe_add_{uid}"})
    if buttons:
        management.append({"text": "✏️ 𝐄𝐃𝐈𝐓", "style": "primary", "callback_data": f"bbe_editpick_{uid}"})
    if management:
        keyboard.append(make_styled_row(management))

    management2 = []
    if buttons:
        management2.append({"text": "🗑 𝐃𝐄𝐋𝐄𝐓𝐄", "style": "danger", "callback_data": f"bbe_delpick_{uid}"})
    if len(buttons) >= 2:
        management2.append({"text": "🔀 𝐌𝐎𝐕𝐄 𝐑𝐎𝐖", "style": "secondary", "callback_data": f"bbe_movepick_{uid}"})
    if management2:
        keyboard.append(make_styled_row(management2))

    if buttons and count < MAX_BUTTONS:
        keyboard.append([make_button_with_icon(text="📋 𝐃𝐔𝐏𝐋𝐈𝐂𝐀𝐓𝐄 𝐋𝐀𝐒𝐓", style="secondary",
                                                callback_data=f"bbe_duplicate_{uid}")])

    keyboard.append(make_styled_row([
        {"text": "✅ 𝐃𝐎𝐍𝐄", "style": "success", "callback_data": f"bbe_done_{uid}"},
    ]))

    markup = InlineKeyboardMarkup(keyboard)
    _send_pe(chat_id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("bbe_"))
def handle_button_editor(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data

    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass

    if uid not in temp_data:
        bot.answer_callback_query(call.id, "Session expired!")
        return

    if data.startswith("bbe_add_"):
        ask_new_button_name(chat_id, uid)

    elif data.startswith("bbe_done_"):
        create_preview(chat_id, uid)

    elif data.startswith("bbe_duplicate_"):
        buttons = temp_data[uid]["buttons"]
        if buttons and len(buttons) < MAX_BUTTONS:
            last = dict(buttons[-1])
            last["row"] = _next_free_row(buttons)
            buttons.append(last)
            bot.answer_callback_query(call.id, "Duplicated!")
        else:
            bot.answer_callback_query(call.id, "Can't duplicate (limit reached or no buttons).")
        button_editor_hub(chat_id, uid)
        return  # already answered

    elif data.startswith("bbe_editpick_"):
        send_button_picker(chat_id, uid, "edit", "𝐒𝐄𝐋𝐄𝐂𝐓 𝐀 𝐁𝐔𝐓𝐓𝐎𝐍 𝐓𝐎 𝐄𝐃𝐈𝐓")

    elif data.startswith("bbe_delpick_"):
        send_button_picker(chat_id, uid, "del", "𝐒𝐄𝐋𝐄𝐂𝐓 𝐀 𝐁𝐔𝐓𝐓𝐎𝐍 𝐓𝐎 𝐃𝐄𝐋𝐄𝐓𝐄")

    elif data.startswith("bbe_movepick_"):
        send_button_picker(chat_id, uid, "move", "𝐒𝐄𝐋𝐄𝐂𝐓 𝐀 𝐁𝐔𝐓𝐓𝐎𝐍 𝐓𝐎 𝐌𝐎𝐕𝐄")

    bot.answer_callback_query(call.id)

def send_button_picker(chat_id, uid, step, prompt):
    buttons = temp_data[uid]["buttons"]
    keyboard = []
    for i, btn in enumerate(buttons):
        label = btn["name"][:20] if btn["name"] else f"Button {i+1}"
        keyboard.append([make_button(text=f"{i+1}. {label}", callback_data=f"bbi_{step}_{i}_{uid}")])
    keyboard.append([make_button_with_icon(text="🔙 𝐁𝐀𝐂𝐊", style="secondary", callback_data=f"bbe_back_{uid}")])
    _send_pe(chat_id, f"{PLACEHOLDER} {prompt}", reply_markup=InlineKeyboardMarkup(keyboard))

@bot.callback_query_handler(func=lambda c: c.data.startswith("bbe_back_"))
def handle_button_editor_back(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    if uid in temp_data:
        button_editor_hub(chat_id, uid)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("bbi_"))
def handle_button_index_action(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass

    if uid not in temp_data:
        bot.answer_callback_query(call.id, "Session expired!")
        return

    try:
        _, step, idx_str, _uid_str = call.data.split("_", 3)
        idx = int(idx_str)
    except Exception:
        bot.answer_callback_query(call.id)
        return

    buttons = temp_data[uid]["buttons"]
    if idx < 0 or idx >= len(buttons):
        bot.answer_callback_query(call.id, "That button no longer exists.")
        button_editor_hub(chat_id, uid)
        return

    if step == "del":
        removed = buttons.pop(idx)
        bot.answer_callback_query(call.id, f"Deleted \"{removed['name']}\".")
        button_editor_hub(chat_id, uid)

    elif step == "edit":
        send_edit_field_picker(chat_id, uid, idx)
        bot.answer_callback_query(call.id)

    elif step == "move":
        send_row_target_picker(chat_id, uid, idx)
        bot.answer_callback_query(call.id)

def send_edit_field_picker(chat_id, uid, idx):
    keyboard = [
        make_styled_row([
            {"text": "✏️ 𝐍𝐀𝐌𝐄", "style": "primary", "callback_data": f"bef_name_{idx}_{uid}"},
            {"text": "🔗 𝐔𝐑𝐋", "style": "primary", "callback_data": f"bef_url_{idx}_{uid}"},
        ]),
        [make_button_with_icon(text="🎨 𝐂𝐎𝐋𝐎𝐑", style="secondary", callback_data=f"bef_color_{idx}_{uid}")],
        [make_button_with_icon(text="🔙 𝐁𝐀𝐂𝐊", style="secondary", callback_data=f"bbe_back_{uid}")],
    ]
    _send_pe(chat_id, f"{PLACEHOLDER} 𝐖𝐇𝐀𝐓 𝐃𝐎 𝐘𝐎𝐔 𝐖𝐀𝐍𝐓 𝐓𝐎 𝐄𝐃𝐈𝐓?", reply_markup=InlineKeyboardMarkup(keyboard))

@bot.callback_query_handler(func=lambda c: c.data.startswith("bef_"))
def handle_edit_field_pick(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass

    if uid not in temp_data:
        bot.answer_callback_query(call.id, "Session expired!")
        return

    try:
        _, field, idx_str, _uid_str = call.data.split("_", 3)
        idx = int(idx_str)
    except Exception:
        bot.answer_callback_query(call.id)
        return

    buttons = temp_data[uid]["buttons"]
    if idx < 0 or idx >= len(buttons):
        bot.answer_callback_query(call.id, "That button no longer exists.")
        button_editor_hub(chat_id, uid)
        return

    if field == "name":
        sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐓𝐇𝐄 𝐍𝐄𝐖 𝐍𝐀𝐌𝐄, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_edit_button_name, idx)
    elif field == "url":
        sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐓𝐇𝐄 𝐍𝐄𝐖 𝐔𝐑𝐋, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_edit_button_url, idx)
    elif field == "color":
        send_color_picker(chat_id, uid, idx, mode="edit")

    bot.answer_callback_query(call.id)

def receive_edit_button_name(message, idx):
    uid = message.from_user.id
    chat_id = message.chat.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        return
    if uid not in temp_data or idx >= len(temp_data[uid]["buttons"]):
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐒𝐒𝐈𝐎𝐍 𝐄𝐗𝐏𝐈𝐑𝐄𝐃!")
        return
    name = (message.text or "").strip()[:30]
    if not name:
        sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐍𝐀𝐌𝐄 𝐂𝐀𝐍𝐍𝐎𝐓 𝐁𝐄 𝐄𝐌𝐏𝐓𝐘. 𝐓𝐑𝐘 𝐀𝐆𝐀𝐈𝐍, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_edit_button_name, idx)
        return
    temp_data[uid]["buttons"][idx]["name"] = name
    button_editor_hub(chat_id, uid)

def receive_edit_button_url(message, idx):
    uid = message.from_user.id
    chat_id = message.chat.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        return
    if uid not in temp_data or idx >= len(temp_data[uid]["buttons"]):
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐒𝐒𝐈𝐎𝐍 𝐄𝐗𝐏𝐈𝐑𝐄𝐃!")
        return
    ok, result = validate_button_url(message.text or "")
    if not ok:
        sent = _send_pe_return(chat_id, f"{PLACEHOLDER} {result} 𝐓𝐑𝐘 𝐀𝐆𝐀𝐈𝐍, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_edit_button_url, idx)
        return
    temp_data[uid]["buttons"][idx]["url"] = result
    button_editor_hub(chat_id, uid)

def send_color_picker(chat_id, uid, idx, mode):
    keyboard = [
        [
            make_button_with_icon(text="𝐏𝐑𝐈𝐌𝐀𝐑𝐘", style="primary", callback_data=f"bec_{mode}_primary_{idx}_{uid}"),
            make_button_with_icon(text="𝐃𝐀𝐍𝐆𝐄𝐑", style="danger", callback_data=f"bec_{mode}_danger_{idx}_{uid}"),
        ],
        [
            make_button_with_icon(text="𝐒𝐔𝐂𝐂𝐄𝐒𝐒", style="success", callback_data=f"bec_{mode}_success_{idx}_{uid}"),
            make_button_with_icon(text="𝐃𝐄𝐅𝐀𝐔𝐋𝐓", callback_data=f"bec_{mode}_default_{idx}_{uid}"),
        ],
    ]
    _send_pe(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐋𝐄𝐂𝐓 𝐀 𝐂𝐎𝐋𝐎𝐑:", reply_markup=InlineKeyboardMarkup(keyboard))

@bot.callback_query_handler(func=lambda c: c.data.startswith("bec_"))
def handle_color_pick(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass

    if uid not in temp_data:
        bot.answer_callback_query(call.id, "Session expired!")
        return

    try:
        _, mode, color, idx_str, _uid_str = call.data.split("_", 4)
        idx = int(idx_str)
    except Exception:
        bot.answer_callback_query(call.id)
        return

    buttons = temp_data[uid]["buttons"]
    if idx < 0 or idx >= len(buttons):
        bot.answer_callback_query(call.id, "That button no longer exists.")
        button_editor_hub(chat_id, uid)
        return

    buttons[idx]["color"] = None if color == "default" else color
    bot.answer_callback_query(call.id, "Color updated!")

    if mode == "new":
        # Part of the "add new button" flow — color is the last step,
        # so return to the hub once it's set.
        button_editor_hub(chat_id, uid)
    else:
        button_editor_hub(chat_id, uid)

def send_row_target_picker(chat_id, uid, idx):
    buttons = temp_data[uid]["buttons"]
    existing_rows = sorted(set(b.get("row", 0) for b in buttons))
    max_row = max(existing_rows) if existing_rows else 0
    keyboard = []
    row_btns = []
    for r in existing_rows:
        row_btns.append(make_button(text=f"Row {r+1}", callback_data=f"bem_{idx}_{r}_{uid}"))
    row_btns.append(make_button(text=f"New Row ({max_row+2})", callback_data=f"bem_{idx}_{max_row+1}_{uid}"))
    for i in range(0, len(row_btns), 3):
        keyboard.append(row_btns[i:i+3])
    keyboard.append([make_button_with_icon(text="🔙 𝐁𝐀𝐂𝐊", style="secondary", callback_data=f"bbe_back_{uid}")])
    _send_pe(chat_id, f"{PLACEHOLDER} 𝐌𝐎𝐕𝐄 𝐓𝐇𝐈𝐒 𝐁𝐔𝐓𝐓𝐎𝐍 𝐓𝐎 𝐖𝐇𝐈𝐂𝐇 𝐑𝐎𝐖?",
              reply_markup=InlineKeyboardMarkup(keyboard))

@bot.callback_query_handler(func=lambda c: c.data.startswith("bem_"))
def handle_row_move(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass

    if uid not in temp_data:
        bot.answer_callback_query(call.id, "Session expired!")
        return

    try:
        _, idx_str, row_str, _uid_str = call.data.split("_", 3)
        idx = int(idx_str)
        new_row = int(row_str)
    except Exception:
        bot.answer_callback_query(call.id)
        return

    buttons = temp_data[uid]["buttons"]
    if idx < 0 or idx >= len(buttons):
        bot.answer_callback_query(call.id, "That button no longer exists.")
        button_editor_hub(chat_id, uid)
        return

    if len([b for b in buttons if b.get("row", 0) == new_row]) >= MAX_BUTTONS_PER_ROW:
        bot.answer_callback_query(call.id, f"Row full (max {MAX_BUTTONS_PER_ROW} per row).", show_alert=True)
        button_editor_hub(chat_id, uid)
        return

    buttons[idx]["row"] = new_row
    bot.answer_callback_query(call.id, "Moved!")
    button_editor_hub(chat_id, uid)

# ---- Add-new-button flow (name -> URL -> color -> back to hub) ----

def ask_new_button_name(chat_id, uid):
    text = f"""
{PLACEHOLDER}═══《 🔘 𝐍𝐄𝐖 𝐁𝐔𝐓𝐓𝐎𝐍 - 𝐍𝐀𝐌𝐄 》═══{PLACEHOLDER}

𝐄𝐍𝐓𝐄𝐑 𝐓𝐇𝐄 𝐁𝐔𝐓𝐓𝐎𝐍 𝐍𝐀𝐌𝐄/𝐋𝐀𝐁𝐄𝐋
(𝐘𝐎𝐔 𝐂𝐀𝐍 𝐔𝐒𝐄 𝐄𝐌𝐎𝐉𝐈𝐒), 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    sent = _send_pe_return(chat_id, text)
    bot.register_next_step_handler(sent, save_new_button_name)

def save_new_button_name(message):
    uid = message.from_user.id
    chat_id = message.chat.id

    if message.text and message.text.strip() == "/cancel":
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        return
    if uid not in temp_data:
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐒𝐒𝐈𝐎𝐍 𝐄𝐗𝐏𝐈𝐑𝐄𝐃!")
        return

    name = (message.text or "").strip()[:30]
    if not name:
        sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐁𝐔𝐓𝐓𝐎𝐍 𝐍𝐀𝐌𝐄 𝐂𝐀𝐍𝐍𝐎𝐓 𝐁𝐄 𝐄𝐌𝐏𝐓𝐘! 𝐓𝐑𝐘 𝐀𝐆𝐀𝐈𝐍, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, save_new_button_name)
        return

    buttons = temp_data[uid]["buttons"]
    if len(buttons) >= MAX_BUTTONS:
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐁𝐔𝐓𝐓𝐎𝐍 𝐋𝐈𝐌𝐈𝐓 𝐑𝐄𝐀𝐂𝐇𝐄𝐃 ({MAX_BUTTONS}).")
        button_editor_hub(chat_id, uid)
        return

    new_row = _next_free_row(buttons)
    buttons.append({"name": name, "url": "", "color": None, "row": new_row})
    ask_new_button_url(chat_id, uid, len(buttons) - 1)

def ask_new_button_url(chat_id, uid, idx):
    text = f"""
{PLACEHOLDER}═══《 🔗 𝐍𝐄𝐖 𝐁𝐔𝐓𝐓𝐎𝐍 - 𝐔𝐑𝐋 》═══{PLACEHOLDER}

𝐄𝐍𝐓𝐄𝐑 𝐓𝐇𝐄 𝐁𝐔𝐓𝐓𝐎𝐍 𝐔𝐑𝐋/𝐋𝐈𝐍𝐊
(𝐌𝐔𝐒𝐓 𝐒𝐓𝐀𝐑𝐓 𝐖𝐈𝐓𝐇 https://), 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    sent = _send_pe_return(chat_id, text)
    bot.register_next_step_handler(sent, save_new_button_url, idx)

def save_new_button_url(message, idx):
    uid = message.from_user.id
    chat_id = message.chat.id

    if message.text and message.text.strip() == "/cancel":
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        # Undo the half-created button so the list doesn't keep a stub entry
        if uid in temp_data and idx < len(temp_data[uid]["buttons"]):
            temp_data[uid]["buttons"].pop(idx)
        return
    if uid not in temp_data or idx >= len(temp_data[uid]["buttons"]):
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐒𝐒𝐈𝐎𝐍 𝐄𝐗𝐏𝐈𝐑𝐄𝐃!")
        return

    ok, result = validate_button_url(message.text or "")
    if not ok:
        sent = _send_pe_return(chat_id, f"{PLACEHOLDER} {result} 𝐓𝐑𝐘 𝐀𝐆𝐀𝐈𝐍, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, save_new_button_url, idx)
        return

    temp_data[uid]["buttons"][idx]["url"] = result
    send_color_picker(chat_id, uid, idx, mode="new")

def create_preview(chat_id, uid):
    data = temp_data.get(uid)
    if not data:
        return
    
    processed_text, processed_entities = process_text_and_entities(
        data["original_text"],
        data["original_entities"],
        uid
    )
    data["processed_text"] = processed_text
    data["processed_entities"] = processed_entities
    
    reply_markup = build_buttons_markup(data["buttons"])
    
    try:
        if data["media_type"] == "image" and data.get("media_id"):
            preview = bot.send_photo(chat_id, data["media_id"], caption=processed_text or None, caption_entities=processed_entities or None, reply_markup=reply_markup)
        elif data["media_type"] == "video" and data.get("media_id"):
            preview = bot.send_video(chat_id, data["media_id"], caption=processed_text or None, caption_entities=processed_entities or None, reply_markup=reply_markup)
        elif data["media_type"] == "doc" and data.get("media_id"):
            preview = bot.send_document(chat_id, data["media_id"], caption=processed_text or None, caption_entities=processed_entities or None, reply_markup=reply_markup)
        else:
            preview = bot.send_message(chat_id, processed_text if processed_text else f"{PLACEHOLDER} 𝐘𝐨𝐮𝐫 𝐩𝐨𝐬𝐭", entities=processed_entities or None, reply_markup=reply_markup)
        
        data["preview_msg_id"] = preview.message_id
        
    except Exception as e:
        logger.error(f"Failed to send preview for uid={uid}: {e}")
        _send_pe(chat_id, f"{PLACEHOLDER} ❌ 𝐒𝐨𝐦𝐞𝐭𝐡𝐢𝐧𝐠 𝐰𝐞𝐧𝐭 𝐰𝐫𝐨𝐧𝐠 𝐛𝐮𝐢𝐥𝐝𝐢𝐧𝐠 𝐭𝐡𝐞 𝐩𝐫𝐞𝐯𝐢𝐞𝐰. 𝐏𝐥𝐞𝐚𝐬𝐞 𝐭𝐫𝐲 𝐚𝐠𝐚𝐢𝐧.")
        return
    
    action_keyboard = [
        make_styled_row([
            {"text": "𝐑𝐄𝐅𝐑𝐄𝐒𝐇", "style": "primary", "callback_data": f"refresh_{uid}"},
            {"text": "𝐃𝐄𝐋𝐄𝐓𝐄", "style": "danger", "callback_data": f"delete_{uid}"},
        ]),
        make_styled_row([
            {"text": "𝐃𝐎𝐍𝐄", "style": "success", "callback_data": f"done_{uid}"},
            {"text": "𝐅𝐎𝐑𝐖𝐀𝐑𝐃", "style": "primary", "callback_data": f"forward_{uid}"},
        ]),
    ]
    action_markup = InlineKeyboardMarkup(action_keyboard)
    
    action_text = f"""
{PLACEHOLDER}═══《 ✨ 𝐏𝐑𝐄𝐕𝐈𝐄𝐖 𝐑𝐄𝐀𝐃𝐘 》═══{PLACEHOLDER}

𝐘𝐨𝐮𝐫 𝐩𝐨𝐬𝐭 𝐢𝐬 𝐫𝐞𝐚𝐝𝐲!

{PLACEHOLDER} 𝐑𝐄𝐅𝐑𝐄𝐒𝐇 - 𝐍𝐞𝐰 𝐞𝐦𝐨𝐣𝐢𝐬
{PLACEHOLDER} 𝐃𝐄𝐋𝐄𝐓𝐄 - 𝐑𝐞𝐦𝐨𝐯𝐞 𝐩𝐫𝐞𝐯𝐢𝐞𝐰
{PLACEHOLDER} 𝐃𝐎𝐍𝐄 - 𝐅𝐢𝐧𝐢𝐬𝐡
{PLACEHOLDER} 𝐅𝐎𝐑𝐖𝐀𝐑𝐃 - 𝐁𝐫𝐨𝐚𝐝𝐜𝐚𝐬𝐭 𝐰𝐢𝐭𝐡 𝐛𝐮𝐭𝐭𝐨𝐧𝐬

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    
    entities = _build_pe_entities(action_text)
    action_msg = bot.send_message(chat_id, action_text, entities=entities, reply_markup=action_markup, parse_mode=None)
    data["action_msg_id"] = action_msg.message_id
    temp_data[uid] = data

@bot.callback_query_handler(func=lambda c: c.data.startswith(("refresh_", "delete_", "done_", "forward_")))
def handle_preview_actions(call):
    parts = call.data.split("_")
    action = parts[0]
    uid = int(parts[1]) if len(parts) > 1 else call.from_user.id
    chat_id = call.message.chat.id

    if uid not in temp_data:
        bot.answer_callback_query(call.id, "Session expired!")
        return

    data = temp_data[uid]

    if action == "refresh":
        bot.answer_callback_query(call.id, "🔄 Refreshing emojis...")
        if data.get("preview_msg_id"):
            try: bot.delete_message(chat_id, data["preview_msg_id"])
            except: pass
        if data.get("action_msg_id"):
            try: bot.delete_message(chat_id, data["action_msg_id"])
            except: pass
        processed_text, processed_entities = process_text_and_entities(data["original_text"], data["original_entities"], uid)
        data["processed_text"] = processed_text
        data["processed_entities"] = processed_entities
        create_preview(chat_id, uid)

    elif action == "delete":
        bot.answer_callback_query(call.id, "Deleted!")
        if data.get("preview_msg_id"):
            try: bot.delete_message(chat_id, data["preview_msg_id"])
            except: pass
        if data.get("action_msg_id"):
            try: bot.delete_message(chat_id, data["action_msg_id"])
            except: pass
        temp_data.pop(uid, None)

    elif action == "done":
        bot.answer_callback_query(call.id, "Post created!")
        if data.get("action_msg_id"):
            try: bot.delete_message(chat_id, data["action_msg_id"])
            except: pass
        temp_data.pop(uid, None)
        kb = get_menu(uid)
        text = f"""
{PLACEHOLDER}═══《 ✅ 𝐏𝐎𝐒𝐓 𝐂𝐑𝐄𝐀𝐓𝐄𝐃! 》═══{PLACEHOLDER}

𝐘𝐨𝐮𝐫 𝐩𝐫𝐞𝐦𝐢𝐮𝐦 𝐞𝐦𝐨𝐣𝐢 𝐩𝐨𝐬𝐭 𝐢𝐬 𝐫𝐞𝐚𝐝𝐲!

𝐓𝐚𝐩 𝐌𝐀𝐊𝐄 𝐏𝐎𝐒𝐓 𝐭𝐨 𝐜𝐫𝐞𝐚𝐭𝐞 𝐚𝐧𝐨𝐭𝐡𝐞𝐫

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
        _send_pe(chat_id, text, reply_markup=kb)

    elif action == "forward":
        preview_msg_id = data.get("preview_msg_id")
        if not preview_msg_id:
            bot.answer_callback_query(call.id, "No preview to forward!", show_alert=True)
            return
        bot.answer_callback_query(call.id, "📤 Broadcasting with buttons...")

        reply_markup = build_buttons_markup(data.get("buttons", []))

        success = 0
        failed = 0
        total = len(all_users)

        status_text = f"{PLACEHOLDER} 📢 𝐁𝐫𝐨𝐚𝐝𝐜𝐚𝐬𝐭𝐢𝐧𝐠 𝐭𝐨 {total} 𝐮𝐬𝐞𝐫𝐬..."
        status_msg = bot.send_message(chat_id, status_text)

        processed_text = data.get("processed_text", "")
        processed_entities = data.get("processed_entities", [])
        media_type = data.get("media_type")
        media_id = data.get("media_id")

        for target_uid in list(all_users):
            try:
                if media_type == "image" and media_id:
                    bot.send_photo(target_uid, media_id, caption=processed_text or None,
                                   caption_entities=processed_entities or None, reply_markup=reply_markup)
                elif media_type == "video" and media_id:
                    bot.send_video(target_uid, media_id, caption=processed_text or None,
                                   caption_entities=processed_entities or None, reply_markup=reply_markup)
                elif media_type == "doc" and media_id:
                    bot.send_document(target_uid, media_id, caption=processed_text or None,
                                      caption_entities=processed_entities or None, reply_markup=reply_markup)
                else:
                    bot.send_message(target_uid, processed_text if processed_text else f"{PLACEHOLDER} 𝐏𝐨𝐬𝐭",
                                     entities=processed_entities or None, reply_markup=reply_markup, parse_mode=None)
                success += 1
                time.sleep(0.05)
            except Exception:
                failed += 1

        try: bot.delete_message(chat_id, status_msg.message_id)
        except: pass

        result_text = f"""
{PLACEHOLDER}═══《 ✅ 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓 𝐃𝐎𝐍𝐄! 》═══{PLACEHOLDER}

𝐒𝐔𝐂𝐂𝐄𝐒𝐒: {success} ✅
𝐅𝐀𝐈𝐋𝐄𝐃: {failed} ❌
𝐁𝐔𝐓𝐓𝐎𝐍𝐒: 𝐈𝐍𝐂𝐋𝐔𝐃𝐄𝐃 ✅

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
        _send_pe(chat_id, result_text, reply_markup=get_menu(uid))

@bot.callback_query_handler(func=lambda c: c.data == "check_join")
def handle_check_join(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    
    not_joined = check_joined(uid)
    
    if not_joined:
        try: bot.delete_message(chat_id, call.message.message_id)
        except: pass
        send_join_notice(chat_id, not_joined)
    else:
        try: bot.delete_message(chat_id, call.message.message_id)
        except: pass
        register_user(uid, call.from_user.username)
        name = call.from_user.first_name or "Friend"
        kb = get_menu(uid)
        text = send_welcome_message(name, uid)
        _send_pe(chat_id, text, reply_markup=kb)
    
    bot.answer_callback_query(call.id)

# ============================================================
# ADMIN COMMANDS - UPDATED WITH EMOJI SUPPORT
# ============================================================
@bot.message_handler(func=lambda m: button_matches(m.text, "𝐁𝐎𝐓 𝐎𝐅𝐅") and has_permission(m.from_user.id, PERM_BOT_CONTROL))
def bot_off(message):
    global bot_active
    bot_active = False
    log_action(message.from_user.id, "bot_off")
    text = f"""
{PLACEHOLDER}═══《 🔴 𝐁𝐎𝐓 𝐎𝐅𝐅𝐋𝐈𝐍𝐄 》═══{PLACEHOLDER}

𝐁𝐎𝐓 𝐈𝐒 𝐍𝐎𝐖 𝐎𝐅𝐅𝐋𝐈𝐍𝐄.

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    _send_pe(message.chat.id, text, reply_markup=get_menu(message.from_user.id))

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐁𝐎𝐓 𝐎𝐍") and has_permission(m.from_user.id, PERM_BOT_CONTROL))
def bot_on(message):
    global bot_active
    bot_active = True
    log_action(message.from_user.id, "bot_on")
    text = f"""
{PLACEHOLDER}═══《 🟢 𝐁𝐎𝐓 𝐎𝐍𝐋𝐈𝐍𝐄 》═══{PLACEHOLDER}

𝐁𝐎𝐓 𝐈𝐒 𝐍𝐎𝐖 𝐎𝐍𝐋𝐈𝐍𝐄!

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    _send_pe(message.chat.id, text, reply_markup=get_menu(message.from_user.id))

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐔𝐒𝐄𝐑𝐒 𝐋𝐈𝐒𝐓") and has_permission(m.from_user.id, PERM_MANAGE_USERS))
def users_list(message):
    total = len(all_users)
    user_list = list(all_users)
    
    text = f"""
{PLACEHOLDER}═══《 👥 𝐑𝐄𝐆𝐈𝐒𝐓𝐄𝐑𝐄𝐃 𝐔𝐒𝐄𝐑𝐒 》═══{PLACEHOLDER}

𝐓𝐎𝐓𝐀𝐋: {total} 𝐔𝐒𝐄𝐑𝐒

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    _send_pe(message.chat.id, text, reply_markup=get_menu(message.from_user.id))
    
    if user_list:
        for i in range(0, len(user_list), 50):
            batch = user_list[i:i+50]
            lines = []
            for uid in batch:
                uname = user_usernames.get(str(uid))
                if uname:
                    lines.append(f"• {uid} — @{uname}")
                else:
                    lines.append(f"• {uid} — (no username)")
            batch_text = "\n".join(lines)
            bot.send_message(message.chat.id, batch_text)

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓") and has_permission(m.from_user.id, PERM_BROADCAST))
def broadcast_start(message):
    text = f"""
{PLACEHOLDER}═══《 📢 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓 》═══{PLACEHOLDER}

𝐒𝐄𝐍𝐃 𝐘𝐎𝐔𝐑 𝐌𝐄𝐒𝐒𝐀𝐆𝐄 𝐍𝐎𝐖.

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    sent = _send_pe_return(message.chat.id, text)
    bot.register_next_step_handler(sent, do_broadcast)

def do_broadcast(message):
    if message.text and message.text.strip() == "/cancel":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(message.from_user.id))
        return
    
    success = 0
    failed = 0
    total = len(all_users)
    status_msg = bot.send_message(message.chat.id, f"📢 Broadcasting to {total} users...")
    
    for uid in list(all_users):
        try:
            bot.copy_message(uid, message.chat.id, message.message_id)
            success += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
    
    try:
        bot.delete_message(message.chat.id, status_msg.message_id)
    except:
        pass
    
    text = f"""
{PLACEHOLDER}═══《 ✅ 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓 𝐃𝐎𝐍𝐄! 》═══{PLACEHOLDER}

𝐒𝐔𝐂𝐂𝐄𝐒𝐒: {success} ✅
𝐅𝐀𝐈𝐋𝐄𝐃: {failed} ❌

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    _send_pe(message.chat.id, text, reply_markup=get_menu(message.from_user.id))

# ============================================================
# ADMIN: ADD EMOJI PACK
# ============================================================
@bot.message_handler(func=lambda m: button_matches(m.text, "𝐀𝐃𝐃 𝐄𝐌𝐎𝐉𝐈 𝐏𝐀𝐂𝐊") and has_permission(m.from_user.id, PERM_MANAGE_PACKS))
def add_emoji_pack_start(message):
    text = f"""
{PLACEHOLDER}═══《 ➕ 𝐀𝐃𝐃 𝐄𝐌𝐎𝐉𝐈 𝐏𝐀𝐂𝐊 》═══{PLACEHOLDER}

𝐒𝐄𝐍𝐃 𝐀 𝐏𝐀𝐂𝐊 𝐋𝐈𝐍𝐊 (𝐞.𝐠. https://t.me/addemoji/PackName)
𝐎𝐑 𝐅𝐎𝐑𝐖𝐀𝐑𝐃/𝐒𝐄𝐍𝐃 𝐀 𝐌𝐄𝐒𝐒𝐀𝐆𝐄 𝐂𝐎𝐍𝐓𝐀𝐈𝐍𝐈𝐍𝐆
𝐓𝐇𝐄 𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐄𝐌𝐎𝐉𝐈𝐒 𝐘𝐎𝐔 𝐖𝐀𝐍𝐓 𝐓𝐎 𝐀𝐃𝐃.

𝐄𝐀𝐂𝐇 𝐍𝐄𝐖 𝐄𝐌𝐎𝐉𝐈 𝐖𝐈𝐋𝐋 𝐆𝐄𝐓 𝐈𝐓𝐒 𝐎𝐖𝐍 #𝐂𝐎𝐃𝐄.

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    sent = _send_pe_return(message.chat.id, text)
    bot.register_next_step_handler(sent, receive_emoji_pack)

def receive_emoji_pack(message):
    if message.text and message.text.strip() == "/cancel":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(message.from_user.id))
        return

    raw_text = message.text or message.caption or ""
    ids = extract_emoji_ids_from_link(raw_text)
    used_link = bool(ids)
    if not ids:
        ids = extract_custom_emoji_ids(message)

    if not ids:
        text = f"{PLACEHOLDER} 𝐍𝐎 𝐄𝐌𝐎𝐉𝐈𝐒 𝐅𝐎𝐔𝐍𝐃! 𝐒𝐄𝐍𝐃 𝐀 𝐏𝐀𝐂𝐊 𝐋𝐈𝐍𝐊 𝐎𝐑 𝐀 𝐌𝐄𝐒𝐒𝐀𝐆𝐄 𝐖𝐈𝐓𝐇 𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐄𝐌𝐎𝐉𝐈𝐒, 𝐎𝐑 /𝐜𝐚𝐧𝐜𝐞𝐥."
        sent = _send_pe_return(message.chat.id, text)
        bot.register_next_step_handler(sent, receive_emoji_pack)
        return

    added_codes = []
    for eid in ids:
        # Skip if this emoji id is already registered under an existing code
        if eid in code_to_emoji.values():
            continue
        code = add_emoji_to_pack(eid)
        added_codes.append((code, eid))

    if not added_codes:
        text = f"{PLACEHOLDER} 𝐀𝐋𝐋 𝐎𝐅 𝐓𝐇𝐎𝐒𝐄 𝐄𝐌𝐎𝐉𝐈𝐒 𝐖𝐄𝐑𝐄 𝐀𝐋𝐑𝐄𝐀𝐃𝐘 𝐈𝐍 𝐓𝐇𝐄 𝐏𝐀𝐂𝐊."
        _send_pe(message.chat.id, text, reply_markup=get_menu(message.from_user.id))
        return

    header = f"""
{PLACEHOLDER}═══《 ✅ 𝐏𝐀𝐂𝐊 𝐔𝐏𝐃𝐀𝐓𝐄𝐃! 》═══{PLACEHOLDER}

𝐀𝐃𝐃𝐄𝐃 {len(added_codes)} 𝐍𝐄𝐖 𝐄𝐌𝐎𝐉𝐈(𝐒) — 𝐓𝐀𝐏 𝐀 𝐂𝐎𝐃𝐄 𝐓𝐎 𝐂𝐎𝐏𝐘:

"""
    footer = f"\n{PLACEHOLDER}═════════════════════{PLACEHOLDER}\n"
    entities_text, entities = build_code_list_message(header, footer, added_codes)
    bot.send_message(message.chat.id, entities_text, entities=entities, reply_markup=get_menu(message.from_user.id), parse_mode=None)

# ============================================================
# EMOJI LIST — viewable by everyone, paginated
# ============================================================
EMOJI_LIST_PAGE_SIZE = 20

def build_emoji_list_page(page: int):
    codes = list(code_to_emoji.items())
    total = len(codes)
    total_pages = max(1, (total + EMOJI_LIST_PAGE_SIZE - 1) // EMOJI_LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * EMOJI_LIST_PAGE_SIZE
    page_items = codes[start:start + EMOJI_LIST_PAGE_SIZE]

    header = f"""
{PLACEHOLDER}═══《 🧩 𝐄𝐌𝐎𝐉𝐈 𝐋𝐈𝐒𝐓 》═══{PLACEHOLDER}

𝐓𝐎𝐓𝐀𝐋 𝐄𝐌𝐎𝐉𝐈𝐒: {total}
𝐓𝐀𝐏 𝐀 𝐂𝐎𝐃𝐄 𝐓𝐎 𝐂𝐎𝐏𝐘, 𝐓𝐇𝐄𝐍 𝐏𝐀𝐒𝐓𝐄 𝐈𝐓 𝐈𝐍 𝐘𝐎𝐔𝐑 𝐏𝐎𝐒𝐓 𝐓𝐄𝐗𝐓.

"""
    if not page_items:
        header += "𝐍𝐎 𝐄𝐌𝐎𝐉𝐈𝐒 𝐀𝐃𝐃𝐄𝐃 𝐘𝐄𝐓.\n\n"

    footer = f"\n{PLACEHOLDER}═══ 𝐏𝐀𝐆𝐄 {page + 1}/{total_pages} ═══{PLACEHOLDER}\n"

    full_text, entities = build_code_list_message(header, footer, page_items)

    keyboard = []
    nav_row = []
    if page > 0:
        nav_row.append(make_button_with_icon(text="◀ 𝐏𝐑𝐄𝐕", style="secondary", callback_data=f"emojilist_{page-1}"))
    if total_pages > 1:
        nav_row.append(make_button_with_icon(text=f"📄 {page+1}/{total_pages}", style="secondary", callback_data=f"emojilist_{page}"))
    if page < total_pages - 1:
        nav_row.append(make_button_with_icon(text="𝐍𝐄𝐗𝐓 ▶", style="secondary", callback_data=f"emojilist_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)
    markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    return full_text, entities, markup

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐄𝐌𝐎𝐉𝐈 𝐋𝐈𝐒𝐓"))
def emoji_list_msg(message):
    register_user(message.from_user.id, message.from_user.username)
    text, entities, markup = build_emoji_list_page(0)
    bot.send_message(message.chat.id, text, entities=entities, reply_markup=markup, parse_mode=None)

@bot.callback_query_handler(func=lambda c: c.data.startswith("emojilist_"))
def handle_emoji_list_page(call):
    page = int(call.data.split("_")[1])
    text, entities, markup = build_emoji_list_page(page)
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            entities=entities,
            reply_markup=markup,
            parse_mode=None
        )
    except Exception:
        pass
    bot.answer_callback_query(call.id)

# ============================================================
# USER: SET CUSTOM PACK — user supplies a link, bot pulls emojis from it
# ============================================================
@bot.message_handler(func=lambda m: button_matches(m.text, "𝐒𝐄𝐓 𝐂𝐔𝐒𝐓𝐎𝐌 𝐏𝐀𝐂𝐊"))
def set_custom_pack_start(message):
    register_user(message.from_user.id, message.from_user.username)
    text = f"""
{PLACEHOLDER}═══《 🎨 𝐒𝐄𝐓 𝐂𝐔𝐒𝐓𝐎𝐌 𝐏𝐀𝐂𝐊 》═══{PLACEHOLDER}

𝐒𝐄𝐍𝐃 𝐘𝐎𝐔𝐑 𝐏𝐀𝐂𝐊'𝐒 𝐋𝐈𝐍𝐊
(𝐞.𝐠. https://t.me/addemoji/yourpack)

𝐎𝐑 𝐅𝐎𝐑𝐖𝐀𝐑𝐃/𝐒𝐄𝐍𝐃 𝐀 𝐌𝐄𝐒𝐒𝐀𝐆𝐄 𝐂𝐎𝐍𝐓𝐀𝐈𝐍𝐈𝐍𝐆
𝐓𝐇𝐄 𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐄𝐌𝐎𝐉𝐈𝐒 𝐈𝐅 𝐘𝐎𝐔 𝐃𝐎𝐍'𝐓 𝐇𝐀𝐕𝐄 𝐀 𝐋𝐈𝐍𝐊.

𝐘𝐎𝐔'𝐋𝐋 𝐆𝐄𝐓 𝐁𝐀𝐂𝐊 #𝐂𝐎𝐃𝐄𝐒 𝐎𝐍𝐋𝐘 𝐘𝐎𝐔 𝐂𝐀𝐍 𝐔𝐒𝐄.

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    sent = _send_pe_return(message.chat.id, text)
    bot.register_next_step_handler(sent, receive_custom_pack)

def receive_custom_pack(message):
    uid = message.from_user.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        return

    raw_text = message.text or message.caption or ""
    ids = extract_emoji_ids_from_link(raw_text)
    if not ids:
        ids = extract_custom_emoji_ids(message)

    if not ids:
        text = f"{PLACEHOLDER} 𝐍𝐎 𝐄𝐌𝐎𝐉𝐈𝐒 𝐅𝐎𝐔𝐍𝐃! 𝐒𝐄𝐍𝐃 𝐀 𝐕𝐀𝐋𝐈𝐃 𝐏𝐀𝐂𝐊 𝐋𝐈𝐍𝐊 𝐎𝐑 𝐀 𝐌𝐄𝐒𝐒𝐀𝐆𝐄 𝐖𝐈𝐓𝐇 𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐄𝐌𝐎𝐉𝐈𝐒, 𝐎𝐑 /𝐜𝐚𝐧𝐜𝐞𝐥."
        sent = _send_pe_return(message.chat.id, text)
        bot.register_next_step_handler(sent, receive_custom_pack)
        return

    link_match = extract_pack_link(raw_text)
    link_display = f"https://t.me/addemoji/{link_match}" if link_match else custom_packs.get(str(uid), {}).get("link", "")

    # Assign fresh codes scoped to this user only (own numbering space, prefixed to avoid clashing with admin pack)
    existing = get_user_custom_codes(uid)
    used_numbers = [int(c[2:]) for c in existing.keys() if c.startswith("#C")]
    next_num = (max(used_numbers) + 1) if used_numbers else 1

    new_codes = dict(existing)
    added = []
    for eid in ids:
        if eid in existing.values():
            continue
        code = f"#C{next_num}"
        next_num += 1
        new_codes[code] = eid
        added.append((code, eid))

    set_user_custom_pack(uid, link_display, new_codes)

    if not added:
        text = f"{PLACEHOLDER} 𝐍𝐎 𝐍𝐄𝐖 𝐄𝐌𝐎𝐉𝐈𝐒 𝐖𝐄𝐑𝐄 𝐀𝐃𝐃𝐄𝐃 (𝐀𝐋𝐑𝐄𝐀𝐃𝐘 𝐈𝐍 𝐘𝐎𝐔𝐑 𝐏𝐀𝐂𝐊)."
        _send_pe(message.chat.id, text, reply_markup=get_menu(uid))
        return

    header = f"""
{PLACEHOLDER}═══《 ✅ 𝐂𝐔𝐒𝐓𝐎𝐌 𝐏𝐀𝐂𝐊 𝐒𝐀𝐕𝐄𝐃! 》═══{PLACEHOLDER}

𝐘𝐎𝐔𝐑 𝐍𝐄𝐖 𝐂𝐎𝐃𝐄𝐒 — 𝐓𝐀𝐏 𝐎𝐍𝐄 𝐓𝐎 𝐂𝐎𝐏𝐘:

"""
    footer = f"\n𝐔𝐒𝐄 𝐓𝐇𝐄𝐒𝐄 𝐂𝐎𝐃𝐄𝐒 𝐈𝐍 𝐘𝐎𝐔𝐑 𝐏𝐎𝐒𝐓 𝐓𝐄𝐗𝐓 𝐀𝐍𝐃 𝐓𝐇𝐄𝐘'𝐋𝐋\n𝐁𝐄 𝐑𝐄𝐏𝐋𝐀𝐂𝐄𝐃 𝐖𝐈𝐓𝐇 𝐘𝐎𝐔𝐑 𝐄𝐌𝐎𝐉𝐈.\n\n{PLACEHOLDER}═════════════════════{PLACEHOLDER}\n"
    full_text, entities = build_code_list_message(header, footer, added)

    bot.send_message(message.chat.id, full_text, entities=entities, reply_markup=get_menu(uid), parse_mode=None)

@bot.message_handler(commands=["cancel"])
def cancel_step(message):
    uid = message.from_user.id
    temp_data.pop(uid, None)
    kb = get_menu(uid)
    _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=kb)

# ============================================================
# DEMO COMMAND
# ============================================================
@bot.message_handler(commands=["buttons"])
def demo_buttons(message):
    text = f"""
{PLACEHOLDER}═══《 🎨 𝐁𝐔𝐓𝐓𝐎𝐍 𝐒𝐓𝐘𝐋𝐄𝐒 𝐃𝐄𝐌𝐎 》═══{PLACEHOLDER}

🔵 𝐩𝐫𝐢𝐦𝐚𝐫𝐲 = 𝐁𝐋𝐔𝐄
🔴 𝐝𝐚𝐧𝐠𝐞𝐫 = 𝐑𝐄𝐃
🟢 𝐬𝐮𝐜𝐜𝐞𝐬𝐬 = 𝐆𝐑𝐄𝐄𝐍
⚪ 𝐝𝐞𝐟𝐚𝐮𝐥𝐭 = 𝐆𝐑𝐀𝐘/𝐖𝐇𝐈𝐓𝐄

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    keyboard = [
        [
            make_button_with_icon(text="𝐏𝐑𝐈𝐌𝐀𝐑𝐘", style="primary", callback_data="demo_primary"),
            make_button_with_icon(text="𝐃𝐀𝐍𝐆𝐄𝐑", style="danger", callback_data="demo_danger"),
        ],
        [
            make_button_with_icon(text="𝐒𝐔𝐂𝐂𝐄𝐒𝐒", style="success", callback_data="demo_success"),
            make_button_with_icon(text="𝐃𝐄𝐅𝐀𝐔𝐋𝐓", callback_data="demo_default"),
        ],
        [
            make_button_with_icon(text="Google", style="primary", url="https://google.com"),
            make_button_with_icon(text="YouTube", style="danger", url="https://youtube.com"),
        ],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    _send_pe(message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("demo_"))
def handle_demo_buttons(call):
    style_name = call.data.replace("demo_", "").upper()
    bot.answer_callback_query(call.id, f"You pressed the {style_name} style button!")

@bot.message_handler(func=lambda m: True)
def fallback(message):
    uid = message.from_user.id
    
    if not is_admin(uid):
        not_joined = check_joined(uid)
        if not_joined:
            send_join_notice(message.chat.id, not_joined)
            return
    
    register_user(uid, message.from_user.username)
    
    if not bot_active and not is_admin(uid):
        text = f"{PLACEHOLDER} 𝐁𝐎𝐓 𝐈𝐒 𝐎𝐅𝐅𝐋𝐈𝐍𝐄."
        _send_pe(message.chat.id, text)
        return
    
    kb = get_menu(uid)
    text = f"""
{PLACEHOLDER}═══《 {PLACEHOLDER} 𝐌𝐄𝐍𝐔 》═══{PLACEHOLDER}

𝐏𝐋𝐄𝐀𝐒𝐄 𝐂𝐇𝐎𝐎𝐒𝐄 𝐅𝐑𝐎𝐌 𝐓𝐇𝐄 𝐌𝐄𝐍𝐔:

🌿 𝐌𝐀𝐊𝐄 𝐏𝐎𝐒𝐓
🍂 𝐇𝐄𝐋𝐏
🍀 𝐀𝐁𝐎𝐔𝐓 𝐁𝐎𝐓
🪾 𝐒𝐓𝐀𝐓𝐒

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    _send_pe(message.chat.id, text, reply_markup=kb)

# ============================================================
# PHASE 1 — EMOJI SEARCH, CATEGORIES, FAVORITES
# ============================================================
# New JSON files created by this section (kept separate from the
# original users.json / usernames.json / emoji_packs.json /
# custom_packs.json, none of which this section touches):
#   emoji_categories.json  - 18 default categories, admin-editable
#   favorites.json         - per-user favorite #code lists
#   emoji_names.json       - optional per-code name/keywords, set via
#                            /nameemoji (admin only), used by search
#
# New menu buttons (added inside get_menu() above):
#   SEARCH EMOJI, CATEGORIES, MY FAVORITES (everyone)
#   MANAGE CATEGORIES (admin only)
# New commands: /search <query>, /favorites, /nameemoji (admin only)
# ============================================================

EMOJI_NAMES_FILE = "emoji_names.json"       # {"#4411": {"name": "Fire", "keywords": ["fire","hot"]}}
CATEGORIES_FILE = "emoji_categories.json"   # {"cat_id": {"name": "...", "icon": "...", "codes": ["#4411", ...]}}
FAVORITES_FILE = "favorites.json"           # {"<user_id>": ["#4411", "#C1", ...]}

DEFAULT_FAVORITE_LIMIT = 50
SEARCH_PAGE_SIZE = 8
CATEGORY_LIST_PAGE_SIZE = 10
FAVORITES_PAGE_SIZE = 10

DEFAULT_CATEGORIES = [
    ("faces", "😀 Faces", "😀"),
    ("love", "❤️ Love", "❤️"),
    ("fire", "🔥 Fire", "🔥"),
    ("gaming", "🎮 Gaming", "🎮"),
    ("stars", "⭐ Stars", "⭐"),
    ("funny", "😂 Funny", "😂"),
    ("cool", "😎 Cool", "😎"),
    ("premium", "💎 Premium", "💎"),
    ("vip", "👑 VIP", "👑"),
    ("energy", "⚡ Energy", "⚡"),
    ("celebration", "🎉 Celebration", "🎉"),
    ("money", "💰 Money", "💰"),
    ("notification", "🔔 Notification", "🔔"),
    ("status", "✅ Status", "✅"),
    ("errors", "❌ Errors", "❌"),
    ("nature", "🌈 Nature", "🌈"),
    ("technology", "🚀 Technology", "🚀"),
    ("achievement", "🏆 Achievement", "🏆"),
]

# --------------------------------------------------------
# STORAGE
# --------------------------------------------------------
emoji_names: dict = load_json_file(EMOJI_NAMES_FILE, {})
categories: dict = load_json_file(CATEGORIES_FILE, {})
favorites: dict = load_json_file(FAVORITES_FILE, {})

if not categories:
    for cat_id, label, icon in DEFAULT_CATEGORIES:
        categories[cat_id] = {"name": label, "icon": icon, "codes": []}
    save_json_file(CATEGORIES_FILE, categories)

def save_names():
    save_json_file(EMOJI_NAMES_FILE, emoji_names)

def save_categories():
    save_json_file(CATEGORIES_FILE, categories)

def save_favorites():
    save_json_file(FAVORITES_FILE, favorites)

# --------------------------------------------------------
# HELPERS
# --------------------------------------------------------
def resolve_code_to_id(code: str, uid: int = None):
    """A code may live in the shared admin pack OR in a user's
    own custom pack (which is scoped per-user, per the existing
    bot-4 design) — check both, user pack first."""
    if uid is not None:
        uid_codes = get_user_custom_codes(uid)
        if code in uid_codes:
            return uid_codes[code]
    return code_to_emoji.get(code)

def all_codes_for_user(uid: int) -> dict:
    """Every code this user is allowed to search/browse:
    the shared admin pack + their own custom pack."""
    merged = dict(code_to_emoji)
    merged.update(get_user_custom_codes(uid))
    return merged

def display_name_for(code: str) -> str:
    meta = emoji_names.get(code)
    if meta and meta.get("name"):
        return meta["name"]
    return "Unnamed"

def is_pack_name_match(code: str, uid: int, query: str) -> bool:
    if code.startswith("#C") and str(uid) in custom_packs:
        pack_link = custom_packs[str(uid)].get("link", "")
        return query in pack_link.lower()
    return False

def search_codes(uid: int, query: str) -> list:
    """Case-insensitive search across code, name, keywords, and
    (for the user's own custom pack) pack name. Returns a list
    of (code, emoji_id) tuples, admin pack first then user pack,
    de-duplicated."""
    query = query.strip().lower()
    if not query:
        return []
    codes = all_codes_for_user(uid)
    results = []
    seen = set()

    for code, eid in codes.items():
        if code in seen:
            continue
        code_l = code.lower()
        meta = emoji_names.get(code, {})
        name_l = (meta.get("name") or "").lower()
        keywords = [k.lower() for k in meta.get("keywords", [])]

        match = (
            query in code_l
            or query.lstrip("#") == code_l.lstrip("#")
            or (name_l and query in name_l)
            or any(query in kw or kw in query for kw in keywords)
            or is_pack_name_match(code, uid, query)
        )
        if match:
            results.append((code, eid))
            seen.add(code)

    return results

# --------------------------------------------------------
# SEARCH — message entry points
# --------------------------------------------------------
def build_search_results_page(uid: int, query: str, page: int):
    results = search_codes(uid, query)
    total = len(results)
    total_pages = max(1, (total + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * SEARCH_PAGE_SIZE
    page_items = results[start:start + SEARCH_PAGE_SIZE]

    header = f"""
{PLACEHOLDER}═══《 🔎 𝐒𝐄𝐀𝐑𝐂𝐇 𝐑𝐄𝐒𝐔𝐋𝐓𝐒 》═══{PLACEHOLDER}

𝐐𝐔𝐄𝐑𝐘: "{query}"
𝐅𝐎𝐔𝐍𝐃: {total} 𝐌𝐀𝐓𝐂𝐇(𝐄𝐒)

"""
    if not page_items:
        header += "𝐍𝐎 𝐄𝐌𝐎𝐉𝐈𝐒 𝐌𝐀𝐓𝐂𝐇𝐄𝐃 𝐓𝐇𝐀𝐓 𝐐𝐔𝐄𝐑𝐘.\n\n"

    lines_text = ""
    for code, eid in page_items:
        name = display_name_for(code)
        lines_text += f"{code}  {PLACEHOLDER}  —  {name}\n"

    footer = f"\n{PLACEHOLDER}═══ 𝐏𝐀𝐆𝐄 {page + 1}/{total_pages} ═══{PLACEHOLDER}\n"
    full_text, entities = build_code_list_message(header, footer, page_items)

    keyboard = []
    fav_row = []
    for code, eid in page_items:
        short = code if len(code) <= 8 else code[:8]
        fav_row.append(
            make_button_with_icon(
                text=f"⭐ {short}",
                style="secondary",
                callback_data=f"favadd_{code}",
            )
        )
        if len(fav_row) == 2:
            keyboard.append(fav_row)
            fav_row = []
    if fav_row:
        keyboard.append(fav_row)

    nav_row = []
    safe_query = query[:40]
    if page > 0:
        nav_row.append(
            make_button_with_icon(text="◀ 𝐏𝐑𝐄𝐕", style="secondary",
                                   callback_data=f"srch_{page-1}_{safe_query}")
        )
    if total_pages > 1:
        nav_row.append(
            make_button_with_icon(text=f"📄 {page+1}/{total_pages}", style="secondary",
                                   callback_data=f"srch_{page}_{safe_query}")
        )
    if page < total_pages - 1:
        nav_row.append(
            make_button_with_icon(text="𝐍𝐄𝐗𝐓 ▶", style="secondary",
                                   callback_data=f"srch_{page+1}_{safe_query}")
        )
    if nav_row:
        keyboard.append(nav_row)

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    return full_text, entities, markup

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐒𝐄𝐀𝐑𝐂𝐇 𝐄𝐌𝐎𝐉𝐈"))
def search_emoji_start(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    text = f"""
{PLACEHOLDER}═══《 🔎 𝐒𝐄𝐀𝐑𝐂𝐇 𝐄𝐌𝐎𝐉𝐈 》═══{PLACEHOLDER}

𝐒𝐄𝐍𝐃 𝐀 𝐍𝐀𝐌𝐄, 𝐊𝐄𝐘𝐖𝐎𝐑𝐃, 𝐎𝐑 #𝐂𝐎𝐃𝐄 𝐓𝐎 𝐒𝐄𝐀𝐑𝐂𝐇.

𝐄𝐗𝐀𝐌𝐏𝐋𝐄𝐒: fire, love, gaming, #4411

𝐎𝐑 𝐭𝐲𝐩𝐞 /𝐜𝐚𝐧𝐜𝐞𝐥 𝐭𝐨 𝐬𝐭𝐨𝐩.

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    sent = _send_pe_return(message.chat.id, text)
    bot.register_next_step_handler(sent, receive_search_query)

def receive_search_query(message):
    uid = message.from_user.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        return
    query = (message.text or "").strip()
    if not query:
        sent = _send_pe_return(message.chat.id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐀 𝐕𝐀𝐋𝐈𝐃 𝐒𝐄𝐀𝐑𝐂𝐇 𝐓𝐄𝐑𝐌, 𝐎𝐑 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_search_query)
        return
    text, entities, markup = build_search_results_page(uid, query, 0)
    bot.send_message(message.chat.id, text, entities=entities, reply_markup=markup, parse_mode=None)

@bot.message_handler(commands=["search"])
def search_command(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        search_emoji_start(message)
        return
    query = parts[1].strip()
    text, entities, markup = build_search_results_page(uid, query, 0)
    bot.send_message(message.chat.id, text, entities=entities, reply_markup=markup, parse_mode=None)

@bot.callback_query_handler(func=lambda c: c.data.startswith("srch_"))
def handle_search_page(call):
    uid = call.from_user.id
    try:
        _, page_str, query = call.data.split("_", 2)
        page = int(page_str)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    text, entities, markup = build_search_results_page(uid, query, page)
    try:
        bot.edit_message_text(
            text, chat_id=call.message.chat.id, message_id=call.message.message_id,
            entities=entities, reply_markup=markup, parse_mode=None,
        )
    except Exception:
        pass
    bot.answer_callback_query(call.id)

# --------------------------------------------------------
# FAVORITES
# --------------------------------------------------------
def get_favorite_limit(uid: int) -> int:
    # Single tier for now — Phase 4 (Premium Membership) is where
    # this becomes plan-dependent. Kept as a function so that
    # later phase can override it without touching call sites.
    return DEFAULT_FAVORITE_LIMIT

def get_user_favorites(uid: int) -> list:
    return favorites.get(str(uid), [])

def add_favorite(uid: int, code: str) -> str:
    """Returns 'added' | 'exists' | 'full' | 'invalid'."""
    if resolve_code_to_id(code, uid) is None:
        return "invalid"
    key = str(uid)
    lst = favorites.get(key, [])
    if code in lst:
        return "exists"
    if len(lst) >= get_favorite_limit(uid):
        return "full"
    lst.append(code)
    favorites[key] = lst
    save_favorites()
    return "added"

def remove_favorite(uid: int, code: str) -> bool:
    key = str(uid)
    lst = favorites.get(key, [])
    if code not in lst:
        return False
    lst.remove(code)
    favorites[key] = lst
    save_favorites()
    return True

def build_favorites_page(uid: int, page: int):
    lst = get_user_favorites(uid)
    total = len(lst)
    total_pages = max(1, (total + FAVORITES_PAGE_SIZE - 1) // FAVORITES_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * FAVORITES_PAGE_SIZE
    page_codes = lst[start:start + FAVORITES_PAGE_SIZE]
    page_items = [(c, resolve_code_to_id(c, uid) or "0") for c in page_codes]

    limit = get_favorite_limit(uid)
    header = f"""
{PLACEHOLDER}═══《 ⭐ 𝐌𝐘 𝐅𝐀𝐕𝐎𝐑𝐈𝐓𝐄𝐒 》═══{PLACEHOLDER}

𝐒𝐀𝐕𝐄𝐃: {total}/{limit}

"""
    if not page_items:
        header += "𝐘𝐎𝐔 𝐇𝐀𝐕𝐄𝐍'𝐓 𝐒𝐀𝐕𝐄𝐃 𝐀𝐍𝐘 𝐅𝐀𝐕𝐎𝐑𝐈𝐓𝐄𝐒 𝐘𝐄𝐓.\n𝐔𝐒𝐄 🔎 𝐒𝐄𝐀𝐑𝐂𝐇 𝐄𝐌𝐎𝐉𝐈 𝐀𝐍𝐃 𝐓𝐀𝐏 ⭐ 𝐓𝐎 𝐀𝐃𝐃 𝐎𝐍𝐄.\n\n"

    footer = f"\n{PLACEHOLDER}═══ 𝐏𝐀𝐆𝐄 {page + 1}/{total_pages} ═══{PLACEHOLDER}\n"
    full_text, entities = build_code_list_message(header, footer, page_items)

    keyboard = []
    remove_row = []
    for code, _eid in page_items:
        short = code if len(code) <= 8 else code[:8]
        remove_row.append(
            make_button_with_icon(text=f"🗑 {short}", style="danger",
                                   callback_data=f"favdel_{code}")
        )
        if len(remove_row) == 2:
            keyboard.append(remove_row)
            remove_row = []
    if remove_row:
        keyboard.append(remove_row)

    nav_row = []
    if page > 0:
        nav_row.append(make_button_with_icon(text="◀ 𝐏𝐑𝐄𝐕", style="secondary",
                                              callback_data=f"favpage_{page-1}"))
    if total_pages > 1:
        nav_row.append(make_button_with_icon(text=f"📄 {page+1}/{total_pages}", style="secondary",
                                              callback_data=f"favpage_{page}"))
    if page < total_pages - 1:
        nav_row.append(make_button_with_icon(text="𝐍𝐄𝐗𝐓 ▶", style="secondary",
                                              callback_data=f"favpage_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    return full_text, entities, markup

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐌𝐘 𝐅𝐀𝐕𝐎𝐑𝐈𝐓𝐄𝐒"))
def favorites_msg(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    text, entities, markup = build_favorites_page(uid, 0)
    bot.send_message(message.chat.id, text, entities=entities, reply_markup=markup, parse_mode=None)

@bot.message_handler(commands=["favorites"])
def favorites_command(message):
    favorites_msg(message)

@bot.callback_query_handler(func=lambda c: c.data.startswith("favpage_"))
def handle_favorites_page(call):
    uid = call.from_user.id
    page = int(call.data.split("_")[1])
    text, entities, markup = build_favorites_page(uid, page)
    try:
        bot.edit_message_text(
            text, chat_id=call.message.chat.id, message_id=call.message.message_id,
            entities=entities, reply_markup=markup, parse_mode=None,
        )
    except Exception:
        pass
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("favadd_"))
def handle_favorite_add(call):
    uid = call.from_user.id
    code = call.data[len("favadd_"):]
    result = add_favorite(uid, code)
    messages = {
        "added": f"⭐ {code} added to favorites!",
        "exists": f"{code} is already in your favorites.",
        "full": f"Favorites limit reached ({get_favorite_limit(uid)}). Remove one first.",
        "invalid": "That emoji code no longer exists.",
    }
    bot.answer_callback_query(call.id, messages.get(result, "Done."))

@bot.callback_query_handler(func=lambda c: c.data.startswith("favdel_"))
def handle_favorite_remove(call):
    uid = call.from_user.id
    code = call.data[len("favdel_"):]
    removed = remove_favorite(uid, code)
    if removed:
        # Re-render the current page (list just got shorter)
        text, entities, markup = build_favorites_page(uid, 0)
        try:
            bot.edit_message_text(
                text, chat_id=call.message.chat.id, message_id=call.message.message_id,
                entities=entities, reply_markup=markup, parse_mode=None,
            )
        except Exception:
            pass
        bot.answer_callback_query(call.id, f"Removed {code}.")
    else:
        bot.answer_callback_query(call.id, "Already removed.")

# --------------------------------------------------------
# CATEGORIES — user browsing
# --------------------------------------------------------
def build_category_list_page(page: int):
    cat_items = list(categories.items())
    total = len(cat_items)
    total_pages = max(1, (total + CATEGORY_LIST_PAGE_SIZE - 1) // CATEGORY_LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * CATEGORY_LIST_PAGE_SIZE
    page_items = cat_items[start:start + CATEGORY_LIST_PAGE_SIZE]

    text = f"""
{PLACEHOLDER}═══《 📂 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒 》═══{PLACEHOLDER}

𝐂𝐇𝐎𝐎𝐒𝐄 𝐀 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐓𝐎 𝐁𝐑𝐎𝐖𝐒𝐄:

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    keyboard = []
    for cat_id, cat in page_items:
        count = len(cat.get("codes", []))
        keyboard.append([
            make_button_with_icon(
                text=f"{cat.get('icon', '📂')} {cat.get('name', cat_id)} ({count})",
                style="secondary",
                callback_data=f"catview_{cat_id}_0",
            )
        ])
    nav_row = []
    if page > 0:
        nav_row.append(make_button_with_icon(text="◀ 𝐏𝐑𝐄𝐕", style="secondary",
                                              callback_data=f"catlist_{page-1}"))
    if total_pages > 1:
        nav_row.append(make_button_with_icon(text=f"📄 {page+1}/{total_pages}", style="secondary",
                                              callback_data=f"catlist_{page}"))
    if page < total_pages - 1:
        nav_row.append(make_button_with_icon(text="𝐍𝐄𝐗𝐓 ▶", style="secondary",
                                              callback_data=f"catlist_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    return text, markup

def build_category_view_page(cat_id: str, page: int):
    cat = categories.get(cat_id)
    if not cat:
        return f"{PLACEHOLDER} 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐍𝐎𝐓 𝐅𝐎𝐔𝐍𝐃.", None, None

    codes_in_cat = cat.get("codes", [])
    items = [(c, code_to_emoji.get(c)) for c in codes_in_cat if c in code_to_emoji]
    total = len(items)
    total_pages = max(1, (total + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * SEARCH_PAGE_SIZE
    page_items = items[start:start + SEARCH_PAGE_SIZE]

    header = f"""
{PLACEHOLDER}═══《 {cat.get('icon', '📂')} {cat.get('name', cat_id)} 》═══{PLACEHOLDER}

𝐓𝐎𝐓𝐀𝐋 𝐄𝐌𝐎𝐉𝐈𝐒: {total}

"""
    if not page_items:
        header += "𝐍𝐎 𝐄𝐌𝐎𝐉𝐈𝐒 𝐈𝐍 𝐓𝐇𝐈𝐒 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐘𝐄𝐓.\n\n"

    footer = f"\n{PLACEHOLDER}═══ 𝐏𝐀𝐆𝐄 {page + 1}/{total_pages} ═══{PLACEHOLDER}\n"
    full_text, entities = build_code_list_message(header, footer, page_items)

    keyboard = []
    fav_row = []
    for code, _eid in page_items:
        short = code if len(code) <= 8 else code[:8]
        fav_row.append(make_button_with_icon(text=f"⭐ {short}", style="secondary",
                                              callback_data=f"favadd_{code}"))
        if len(fav_row) == 2:
            keyboard.append(fav_row)
            fav_row = []
    if fav_row:
        keyboard.append(fav_row)

    nav_row = []
    if page > 0:
        nav_row.append(make_button_with_icon(text="◀ 𝐏𝐑𝐄𝐕", style="secondary",
                                              callback_data=f"catview_{cat_id}_{page-1}"))
    if total_pages > 1:
        nav_row.append(make_button_with_icon(text=f"📄 {page+1}/{total_pages}", style="secondary",
                                              callback_data=f"catview_{cat_id}_{page}"))
    if page < total_pages - 1:
        nav_row.append(make_button_with_icon(text="𝐍𝐄𝐗𝐓 ▶", style="secondary",
                                              callback_data=f"catview_{cat_id}_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([make_button_with_icon(text="🔙 𝐁𝐀𝐂𝐊 𝐓𝐎 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒", style="secondary",
                                            callback_data="catlist_0")])

    markup = InlineKeyboardMarkup(keyboard)
    return full_text, entities, markup

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒"))
def categories_msg(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    text, markup = build_category_list_page(0)
    _send_pe(message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("catlist_"))
def handle_category_list_page(call):
    page = int(call.data.split("_")[1])
    text, markup = build_category_list_page(page)
    entities = _build_pe_entities(text)
    try:
        bot.edit_message_text(
            text, chat_id=call.message.chat.id, message_id=call.message.message_id,
            entities=entities, reply_markup=markup, parse_mode=None,
        )
    except Exception:
        pass
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("catview_"))
def handle_category_view(call):
    try:
        _, cat_id, page_str = call.data.split("_", 2)
        page = int(page_str)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    text, entities, markup = build_category_view_page(cat_id, page)
    try:
        bot.edit_message_text(
            text, chat_id=call.message.chat.id, message_id=call.message.message_id,
            entities=entities, reply_markup=markup, parse_mode=None,
        )
    except Exception:
        pass
    bot.answer_callback_query(call.id)

# --------------------------------------------------------
# CATEGORIES — admin management
# --------------------------------------------------------
admin_cat_state = {}  # uid -> dict, transient step tracker (mirrors temp_data pattern)

def build_admin_category_menu():
    text = f"""
{PLACEHOLDER}═══《 🗂 𝐌𝐀𝐍𝐀𝐆𝐄 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒 》═══{PLACEHOLDER}

𝐂𝐇𝐎𝐎𝐒𝐄 𝐀𝐍 𝐀𝐂𝐓𝐈𝐎𝐍:

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    keyboard = [
        make_styled_row([
            {"text": "➕ 𝐂𝐑𝐄𝐀𝐓𝐄", "style": "primary", "callback_data": "catadm_create"},
            {"text": "✏️ 𝐑𝐄𝐍𝐀𝐌𝐄", "style": "primary", "callback_data": "catadm_rename"},
        ]),
        make_styled_row([
            {"text": "🗑 𝐃𝐄𝐋𝐄𝐓𝐄", "style": "danger", "callback_data": "catadm_delete"},
            {"text": "➕ 𝐀𝐃𝐃 𝐄𝐌𝐎𝐉𝐈", "style": "success", "callback_data": "catadm_addcode"},
        ]),
        make_styled_row([
            {"text": "➖ 𝐑𝐄𝐌𝐎𝐕𝐄 𝐄𝐌𝐎𝐉𝐈", "style": "danger", "callback_data": "catadm_removecode"},
        ]),
    ]
    return text, InlineKeyboardMarkup(keyboard)

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐌𝐀𝐍𝐀𝐆𝐄 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒") and has_permission(m.from_user.id, PERM_MANAGE_EMOJIS))
def manage_categories_start(message):
    text, markup = build_admin_category_menu()
    _send_pe(message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("catadm_") and has_permission(c.from_user.id, PERM_MANAGE_EMOJIS))
def handle_category_admin_action(call):
    uid = call.from_user.id
    action = call.data[len("catadm_"):]
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass

    if action == "create":
        sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐓𝐇𝐄 𝐍𝐄𝐖 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐍𝐀𝐌𝐄 (𝐞.𝐠. 🎯 𝐆𝐎𝐀𝐋𝐒), 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_category_name)
    elif action == "rename":
        if not categories:
            _send_pe(chat_id, f"{PLACEHOLDER} 𝐍𝐎 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒 𝐘𝐄𝐓.", reply_markup=get_menu(uid))
        else:
            send_category_picker(chat_id, "rename", "𝐒𝐄𝐋𝐄𝐂𝐓 𝐀 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐓𝐎 𝐑𝐄𝐍𝐀𝐌𝐄")
    elif action == "delete":
        if not categories:
            _send_pe(chat_id, f"{PLACEHOLDER} 𝐍𝐎 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒 𝐘𝐄𝐓.", reply_markup=get_menu(uid))
        else:
            send_category_picker(chat_id, "delete", "𝐒𝐄𝐋𝐄𝐂𝐓 𝐀 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐓𝐎 𝐃𝐄𝐋𝐄𝐓𝐄")
    elif action == "addcode":
        if not categories:
            _send_pe(chat_id, f"{PLACEHOLDER} 𝐍𝐎 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒 𝐘𝐄𝐓. 𝐂𝐑𝐄𝐀𝐓𝐄 𝐎𝐍𝐄 𝐅𝐈𝐑𝐒𝐓.", reply_markup=get_menu(uid))
        else:
            send_category_picker(chat_id, "addcode", "𝐒𝐄𝐋𝐄𝐂𝐓 𝐀 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐓𝐎 𝐀𝐃𝐃 𝐀𝐍 𝐄𝐌𝐎𝐉𝐈 𝐓𝐎")
    elif action == "removecode":
        if not categories:
            _send_pe(chat_id, f"{PLACEHOLDER} 𝐍𝐎 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐈𝐄𝐒 𝐘𝐄𝐓.", reply_markup=get_menu(uid))
        else:
            send_category_picker(chat_id, "removecode", "𝐒𝐄𝐋𝐄𝐂𝐓 𝐀 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐓𝐎 𝐑𝐄𝐌𝐎𝐕𝐄 𝐀𝐍 𝐄𝐌𝐎𝐉𝐈 𝐅𝐑𝐎𝐌")

    bot.answer_callback_query(call.id)

def send_category_picker(chat_id, step, prompt):
    keyboard = []
    for cat_id, cat in categories.items():
        keyboard.append([make_button_with_icon(
            text=f"{cat.get('icon', '📂')} {cat.get('name', cat_id)}",
            style="secondary",
            callback_data=f"catpick_{step}_{cat_id}",
        )])
    _send_pe(chat_id, f"{PLACEHOLDER} {prompt}", reply_markup=InlineKeyboardMarkup(keyboard))

@bot.callback_query_handler(func=lambda c: c.data.startswith("catpick_") and has_permission(c.from_user.id, PERM_MANAGE_EMOJIS))
def handle_category_pick(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    try:
        _, step, cat_id = call.data.split("_", 2)
    except Exception:
        bot.answer_callback_query(call.id)
        return

    if cat_id not in categories:
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐓𝐇𝐀𝐓 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐍𝐎 𝐋𝐎𝐍𝐆𝐄𝐑 𝐄𝐗𝐈𝐒𝐓𝐒.", reply_markup=get_menu(uid))
        bot.answer_callback_query(call.id)
        return

    cat = categories[cat_id]

    if step == "rename":
        sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐓𝐇𝐄 𝐍𝐄𝐖 𝐍𝐀𝐌𝐄 𝐅𝐎𝐑 \"{cat['name']}\", 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_category_rename, cat_id)
    elif step == "delete":
        categories.pop(cat_id)
        save_categories()
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐃𝐄𝐋𝐄𝐓𝐄𝐃 \"{cat['name']}\".", reply_markup=get_menu(uid))
    elif step == "addcode":
        sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐓𝐇𝐄 #𝐂𝐎𝐃𝐄 𝐓𝐎 𝐀𝐃𝐃 𝐓𝐎 \"{cat['name']}\" (𝐞.𝐠. #4411), 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_category_addcode, cat_id)
    elif step == "removecode":
        codes_in_cat = cat.get("codes", [])
        if not codes_in_cat:
            _send_pe(chat_id, f"{PLACEHOLDER} \"{cat['name']}\" 𝐇𝐀𝐒 𝐍𝐎 𝐄𝐌𝐎𝐉𝐈𝐒 𝐓𝐎 𝐑𝐄𝐌𝐎𝐕𝐄.", reply_markup=get_menu(uid))
        else:
            keyboard = []
            row = []
            for code in codes_in_cat:
                row.append(make_button_with_icon(text=code, style="danger",
                                                  callback_data=f"catrmcode_{cat_id}_{code}"))
                if len(row) == 3:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            _send_pe(chat_id, f"{PLACEHOLDER} 𝐓𝐀𝐏 𝐀 𝐂𝐎𝐃𝐄 𝐓𝐎 𝐑𝐄𝐌𝐎𝐕𝐄 𝐈𝐓 𝐅𝐑𝐎𝐌 \"{cat['name']}\":",
                      reply_markup=InlineKeyboardMarkup(keyboard))

    bot.answer_callback_query(call.id)

def receive_category_name(message):
    uid = message.from_user.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        return
    name = (message.text or "").strip()
    if not name:
        sent = _send_pe_return(message.chat.id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐀 𝐕𝐀𝐋𝐈𝐃 𝐍𝐀𝐌𝐄, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_category_name)
        return
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or f"cat{int(time.time())}"
    base_slug, n = slug, 1
    while slug in categories:
        n += 1
        slug = f"{base_slug}-{n}"
    icon_match = re.match(r"^(\S+)\s", name)
    icon = icon_match.group(1) if icon_match else "📂"
    categories[slug] = {"name": name, "icon": icon, "codes": []}
    save_categories()
    _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 \"{name}\" 𝐂𝐑𝐄𝐀𝐓𝐄𝐃!", reply_markup=get_menu(uid))

def receive_category_rename(message, cat_id):
    uid = message.from_user.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        return
    if cat_id not in categories:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐓𝐇𝐀𝐓 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐍𝐎 𝐋𝐎𝐍𝐆𝐄𝐑 𝐄𝐗𝐈𝐒𝐓𝐒.", reply_markup=get_menu(uid))
        return
    new_name = (message.text or "").strip()
    if not new_name:
        sent = _send_pe_return(message.chat.id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐀 𝐕𝐀𝐋𝐈𝐃 𝐍𝐀𝐌𝐄, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_category_rename, cat_id)
        return
    categories[cat_id]["name"] = new_name
    save_categories()
    _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐑𝐄𝐍𝐀𝐌𝐄𝐃 𝐓𝐎 \"{new_name}\"!", reply_markup=get_menu(uid))

def receive_category_addcode(message, cat_id):
    uid = message.from_user.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        return
    if cat_id not in categories:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐓𝐇𝐀𝐓 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐍𝐎 𝐋𝐎𝐍𝐆𝐄𝐑 𝐄𝐗𝐈𝐒𝐓𝐒.", reply_markup=get_menu(uid))
        return
    code = (message.text or "").strip()
    if not code.startswith("#"):
        code = f"#{code}"
    if code not in code_to_emoji:
        sent = _send_pe_return(message.chat.id, f"{PLACEHOLDER} \"{code}\" 𝐈𝐒𝐍'𝐓 𝐀 𝐊𝐍𝐎𝐖𝐍 𝐄𝐌𝐎𝐉𝐈 𝐂𝐎𝐃𝐄. 𝐒𝐄𝐍𝐃 𝐀𝐍𝐎𝐓𝐇𝐄𝐑, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_category_addcode, cat_id)
        return
    cat_codes = categories[cat_id].setdefault("codes", [])
    if code in cat_codes:
        _send_pe(message.chat.id, f"{PLACEHOLDER} \"{code}\" 𝐈𝐒 𝐀𝐋𝐑𝐄𝐀𝐃𝐘 𝐈𝐍 𝐓𝐇𝐈𝐒 𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘.", reply_markup=get_menu(uid))
        return
    cat_codes.append(code)
    save_categories()
    _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐀𝐃𝐃𝐄𝐃 {code} 𝐓𝐎 \"{categories[cat_id]['name']}\"!", reply_markup=get_menu(uid))

@bot.callback_query_handler(func=lambda c: c.data.startswith("catrmcode_") and has_permission(c.from_user.id, PERM_MANAGE_EMOJIS))
def handle_category_removecode(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    try:
        _, cat_id, code = call.data.split("_", 2)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    if cat_id in categories and code in categories[cat_id].get("codes", []):
        categories[cat_id]["codes"].remove(code)
        save_categories()
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐑𝐄𝐌𝐎𝐕𝐄𝐃 {code}.", reply_markup=get_menu(uid))
    else:
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐀𝐋𝐑𝐄𝐀𝐃𝐘 𝐑𝐄𝐌𝐎𝐕𝐄𝐃.", reply_markup=get_menu(uid))
    bot.answer_callback_query(call.id)

# --------------------------------------------------------
# OPTIONAL: admin naming a code (feeds search relevance)
# --------------------------------------------------------
@bot.message_handler(commands=["nameemoji"])
def name_emoji_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_EMOJIS):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /nameemoji #4411 Fire, hot, trending", reply_markup=get_menu(uid))
        return
    code = parts[1].strip()
    if not code.startswith("#"):
        code = f"#{code}"
    if code not in code_to_emoji:
        _send_pe(message.chat.id, f"{PLACEHOLDER} \"{code}\" 𝐈𝐒𝐍'𝐓 𝐀 𝐊𝐍𝐎𝐖𝐍 𝐂𝐎𝐃𝐄.", reply_markup=get_menu(uid))
        return
    rest = parts[2]
    name_part, _, kw_part = rest.partition(",")
    name = name_part.strip()
    keywords = [k.strip() for k in kw_part.split(",") if k.strip()] if kw_part else []
    emoji_names[code] = {"name": name, "keywords": keywords}
    save_names()
    _send_pe(message.chat.id, f"{PLACEHOLDER} {code} 𝐍𝐀𝐌𝐄𝐃 \"{name}\".", reply_markup=get_menu(uid))

# ============================================================
# PHASE 3 — TEMPLATES & DRAFTS
# ============================================================
# New JSON files (kept separate from every other file — nothing
# below reads or writes users.json, usernames.json, emoji_packs.json,
# custom_packs.json, emoji_categories.json, favorites.json, or
# emoji_names.json):
#   drafts.json     - per-user in-progress posts, auto-saved + manual
#   templates.json  - reusable named posts, per-user + admin "global"
#
# New menu buttons: 📝 MY DRAFTS, 📑 TEMPLATES (everyone)
# New commands: /drafts, /templates
# New preview-screen buttons: 💾 SAVE DRAFT, 📑 SAVE TEMPLATE
# New MAKE POST flow step: if the user has drafts and/or templates,
# they're offered a choice screen before the blank "send your text"
# prompt; skipped entirely if they have neither (identical to the
# original behavior for a first-time user).
#
# A draft/template stores a POST SNAPSHOT — text, entities, media
# type + file_id, and buttons — using the exact same shape as
# temp_data[uid], so resuming one is just temp_data[uid] = snapshot.
# MessageEntity objects aren't JSON-serializable on their own, so
# entities are converted to/from plain dicts at the storage boundary
# only; nothing else in the file ever sees that representation.
# ============================================================

DRAFTS_FILE = "drafts.json"
TEMPLATES_FILE = "templates.json"

MAX_DRAFTS_PER_USER = 10
MAX_TEMPLATES_PER_USER = 20
DRAFT_LIST_PAGE_SIZE = 8
TEMPLATE_LIST_PAGE_SIZE = 8

drafts: dict = load_json_file(DRAFTS_FILE, {})
templates: dict = load_json_file(TEMPLATES_FILE, {})

def save_drafts():
    save_json_file(DRAFTS_FILE, drafts)

def save_templates():
    save_json_file(TEMPLATES_FILE, templates)

# ---- snapshot <-> storage conversion ----

def _entity_to_dict(e) -> dict:
    return {
        "type": getattr(e, "type", None),
        "offset": getattr(e, "offset", 0),
        "length": getattr(e, "length", 0),
        "url": getattr(e, "url", None),
        "custom_emoji_id": getattr(e, "custom_emoji_id", None),
    }

def _dict_to_entity(d: dict) -> MessageEntity:
    kwargs = {"type": d.get("type"), "offset": d.get("offset", 0), "length": d.get("length", 0)}
    if d.get("url"):
        kwargs["url"] = d["url"]
    if d.get("custom_emoji_id"):
        kwargs["custom_emoji_id"] = d["custom_emoji_id"]
    return MessageEntity(**kwargs)

def snapshot_from_temp_data(data: dict) -> dict:
    """temp_data[uid] -> JSON-safe dict for storage."""
    return {
        "original_text": data.get("original_text", ""),
        "original_entities": [_entity_to_dict(e) for e in data.get("original_entities", [])],
        "media_type": data.get("media_type"),
        "media_id": data.get("media_id"),
        "buttons": data.get("buttons", []),
    }

def temp_data_from_snapshot(snapshot: dict) -> dict:
    """Stored snapshot -> a fresh temp_data[uid]-shaped dict, ready to
    drop straight into temp_data and resume from (e.g. the button
    editor hub, or straight to preview)."""
    return {
        "original_text": snapshot.get("original_text", ""),
        "original_entities": [_dict_to_entity(e) for e in snapshot.get("original_entities", [])],
        "media_type": snapshot.get("media_type"),
        "media_id": snapshot.get("media_id"),
        "media_name": None,
        "buttons": snapshot.get("buttons", []),
        "refresh_count": 0,
        "processed_text": "",
        "processed_entities": [],
        "preview_msg_id": None,
        "action_msg_id": None,
    }

def snapshot_preview_line(snapshot: dict) -> str:
    """Short one-line summary of a snapshot's content, for list screens."""
    text = (snapshot.get("original_text") or "").strip().replace("\n", " ")
    if len(text) > 40:
        text = text[:40] + "…"
    if not text:
        text = "(no text)"
    media = snapshot.get("media_type")
    media_tag = {"image": "🖼", "video": "🎥", "doc": "📄"}.get(media, "")
    btn_count = len(snapshot.get("buttons", []))
    btn_tag = f" · 🔘{btn_count}" if btn_count else ""
    return f"{media_tag} {text}{btn_tag}".strip()

# ============================================================
# DRAFTS
# ============================================================

def get_user_drafts(uid: int) -> list:
    return drafts.get(str(uid), [])

def auto_save_draft(uid: int):
    """Called at major post-builder checkpoints (text set, media set,
    buttons done) to silently keep an up-to-date "continue where you
    left off" entry. Auto-saves reuse a single slot per user (marked
    autosave: true) rather than piling up — manual '💾 Save Draft'
    from the preview screen is what creates a separate, named,
    permanent draft."""
    if uid not in temp_data:
        return
    key = str(uid)
    user_drafts = drafts.get(key, [])
    snapshot = snapshot_from_temp_data(temp_data[uid])
    # Nothing worth saving yet (no text, no media) - skip
    if not snapshot["original_text"] and not snapshot["media_type"]:
        return
    existing_autosave = next((d for d in user_drafts if d.get("autosave")), None)
    if existing_autosave:
        existing_autosave["snapshot"] = snapshot
        existing_autosave["updated_at"] = int(time.time())
    else:
        user_drafts.insert(0, {
            "id": f"auto_{uid}",
            "name": "🔄 Continue Editing",
            "autosave": True,
            "snapshot": snapshot,
            "updated_at": int(time.time()),
        })
    drafts[key] = user_drafts
    save_drafts()

def clear_autosave_draft(uid: int):
    key = str(uid)
    user_drafts = drafts.get(key, [])
    remaining = [d for d in user_drafts if not d.get("autosave")]
    if len(remaining) != len(user_drafts):
        drafts[key] = remaining
        save_drafts()

def save_manual_draft(uid: int, name: str) -> str:
    """Returns 'saved' or 'full'."""
    if uid not in temp_data:
        return "invalid"
    key = str(uid)
    user_drafts = drafts.get(key, [])
    non_auto = [d for d in user_drafts if not d.get("autosave")]
    if len(non_auto) >= get_draft_limit(uid):
        return "full"
    snapshot = snapshot_from_temp_data(temp_data[uid])
    user_drafts.append({
        "id": make_unique_id("d"),
        "name": name[:40] or "Untitled Draft",
        "autosave": False,
        "snapshot": snapshot,
        "updated_at": int(time.time()),
    })
    drafts[key] = user_drafts
    save_drafts()
    return "saved"

def delete_draft(uid: int, draft_id: str) -> bool:
    key = str(uid)
    user_drafts = drafts.get(key, [])
    filtered = [d for d in user_drafts if d.get("id") != draft_id]
    if len(filtered) == len(user_drafts):
        return False
    drafts[key] = filtered
    save_drafts()
    return True

def rename_draft(uid: int, draft_id: str, new_name: str) -> bool:
    for d in drafts.get(str(uid), []):
        if d.get("id") == draft_id:
            d["name"] = new_name[:40]
            save_drafts()
            return True
    return False

def build_drafts_list_page(uid: int, page: int):
    user_drafts = get_user_drafts(uid)
    total = len(user_drafts)
    total_pages = max(1, (total + DRAFT_LIST_PAGE_SIZE - 1) // DRAFT_LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * DRAFT_LIST_PAGE_SIZE
    page_items = user_drafts[start:start + DRAFT_LIST_PAGE_SIZE]

    text = f"""
{PLACEHOLDER}═══《 📝 𝐌𝐘 𝐃𝐑𝐀𝐅𝐓𝐒 》═══{PLACEHOLDER}

𝐒𝐀𝐕𝐄𝐃: {total}/{get_draft_limit(uid) + 1} (+1 𝐚𝐮𝐭𝐨𝐬𝐚𝐯𝐞 𝐬𝐥𝐨𝐭)

"""
    if not page_items:
        text += "𝐍𝐎 𝐃𝐑𝐀𝐅𝐓𝐒 𝐘𝐄𝐓. 𝐒𝐓𝐀𝐑𝐓 𝐀 𝐏𝐎𝐒𝐓 𝐖𝐈𝐓𝐇 🌿 𝐌𝐀𝐊𝐄 𝐏𝐎𝐒𝐓 — 𝐈𝐓 𝐀𝐔𝐓𝐎-𝐒𝐀𝐕𝐄𝐒 𝐀𝐒 𝐘𝐎𝐔 𝐆𝐎.\n\n"
    text += f"{PLACEHOLDER}═════════════════════{PLACEHOLDER}\n"

    keyboard = []
    for d in page_items:
        label = d["name"][:28]
        keyboard.append([make_button(text=f"📝 {label}", callback_data=f"drf_open_{d['id']}_{uid}")])

    nav_row = []
    if page > 0:
        nav_row.append(make_button_with_icon(text="◀ 𝐏𝐑𝐄𝐕", style="secondary", callback_data=f"drflist_{page-1}_{uid}"))
    if total_pages > 1:
        nav_row.append(make_button_with_icon(text=f"📄 {page+1}/{total_pages}", style="secondary", callback_data=f"drflist_{page}_{uid}"))
    if page < total_pages - 1:
        nav_row.append(make_button_with_icon(text="𝐍𝐄𝐗𝐓 ▶", style="secondary", callback_data=f"drflist_{page+1}_{uid}"))
    if nav_row:
        keyboard.append(nav_row)

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    return text, markup

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐌𝐘 𝐃𝐑𝐀𝐅𝐓𝐒"))
def drafts_msg(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    text, markup = build_drafts_list_page(uid, 0)
    _send_pe(message.chat.id, text, reply_markup=markup)

@bot.message_handler(commands=["drafts"])
def drafts_command(message):
    drafts_msg(message)

@bot.callback_query_handler(func=lambda c: c.data.startswith("drflist_"))
def handle_drafts_list_page(call):
    uid = call.from_user.id
    page = int(call.data.split("_")[1])
    text, markup = build_drafts_list_page(uid, page)
    entities = _build_pe_entities(text)
    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id,
                              entities=entities, reply_markup=markup, parse_mode=None)
    except Exception:
        pass
    bot.answer_callback_query(call.id)

def build_draft_detail(uid: int, draft_id: str):
    d = next((x for x in get_user_drafts(uid) if x.get("id") == draft_id), None)
    if not d:
        return None, None
    preview_line = snapshot_preview_line(d["snapshot"])
    text = f"""
{PLACEHOLDER}═══《 📝 {d['name']} 》═══{PLACEHOLDER}

{preview_line}

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    keyboard = [
        make_styled_row([
            {"text": "▶️ 𝐑𝐄𝐒𝐔𝐌𝐄", "style": "success", "callback_data": f"drf_resume_{draft_id}_{uid}"},
        ]),
    ]
    if not d.get("autosave"):
        keyboard.append(make_styled_row([
            {"text": "✏️ 𝐑𝐄𝐍𝐀𝐌𝐄", "style": "primary", "callback_data": f"drf_ren_{draft_id}_{uid}"},
            {"text": "🗑 𝐃𝐄𝐋𝐄𝐓𝐄", "style": "danger", "callback_data": f"drf_del_{draft_id}_{uid}"},
        ]))
    else:
        keyboard.append([make_button_with_icon(text="🗑 𝐃𝐄𝐋𝐄𝐓𝐄", style="danger", callback_data=f"drf_del_{draft_id}_{uid}")])
    keyboard.append([make_button_with_icon(text="🔙 𝐁𝐀𝐂𝐊", style="secondary", callback_data=f"drflist_0_{uid}")])
    return text, InlineKeyboardMarkup(keyboard)

@bot.callback_query_handler(func=lambda c: c.data.startswith("drf_open_"))
def handle_draft_open(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        _, _, draft_id, _uid_str = call.data.split("_", 3)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    text, markup = build_draft_detail(uid, draft_id)
    if text is None:
        bot.answer_callback_query(call.id, "That draft no longer exists.")
        return
    entities = _build_pe_entities(text)
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id,
                              entities=entities, reply_markup=markup, parse_mode=None)
    except Exception:
        pass
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("drf_resume_"))
def handle_draft_resume(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    try:
        _, _, draft_id, _uid_str = call.data.split("_", 3)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    d = next((x for x in get_user_drafts(uid) if x.get("id") == draft_id), None)
    if not d:
        bot.answer_callback_query(call.id, "That draft no longer exists.")
        return
    temp_data[uid] = temp_data_from_snapshot(d["snapshot"])
    bot.answer_callback_query(call.id, "Draft resumed!")
    button_editor_hub(chat_id, uid)

@bot.callback_query_handler(func=lambda c: c.data.startswith("drf_del_"))
def handle_draft_delete(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        _, _, draft_id, _uid_str = call.data.split("_", 3)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    removed = delete_draft(uid, draft_id)
    if removed:
        text, markup = build_drafts_list_page(uid, 0)
        entities = _build_pe_entities(text)
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id,
                                  entities=entities, reply_markup=markup, parse_mode=None)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Draft deleted.")
    else:
        bot.answer_callback_query(call.id, "Already removed.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("drf_ren_"))
def handle_draft_rename_prompt(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    try:
        _, _, draft_id, _uid_str = call.data.split("_", 3)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐓𝐇𝐄 𝐍𝐄𝐖 𝐃𝐑𝐀𝐅𝐓 𝐍𝐀𝐌𝐄, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
    bot.register_next_step_handler(sent, receive_draft_rename, draft_id)
    bot.answer_callback_query(call.id)

def receive_draft_rename(message, draft_id):
    uid = message.from_user.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        return
    new_name = (message.text or "").strip()
    if not new_name:
        sent = _send_pe_return(message.chat.id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐀 𝐕𝐀𝐋𝐈𝐃 𝐍𝐀𝐌𝐄, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_draft_rename, draft_id)
        return
    renamed = rename_draft(uid, draft_id, new_name)
    if renamed:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐑𝐄𝐍𝐀𝐌𝐄𝐃 𝐓𝐎 \"{new_name}\"!", reply_markup=get_menu(uid))
    else:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐓𝐇𝐀𝐓 𝐃𝐑𝐀𝐅𝐓 𝐍𝐎 𝐋𝐎𝐍𝐆𝐄𝐑 𝐄𝐗𝐈𝐒𝐓𝐒.", reply_markup=get_menu(uid))

@bot.callback_query_handler(func=lambda c: c.data.startswith("savedraft_"))
def handle_save_draft_prompt(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    if uid not in temp_data:
        bot.answer_callback_query(call.id, "Session expired!")
        return
    non_auto = [d for d in get_user_drafts(uid) if not d.get("autosave")]
    if len(non_auto) >= get_draft_limit(uid):
        bot.answer_callback_query(call.id, f"Draft limit reached ({get_draft_limit(uid)}). Delete one first.", show_alert=True)
        return
    sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐀 𝐍𝐀𝐌𝐄 𝐅𝐎𝐑 𝐓𝐇𝐈𝐒 𝐃𝐑𝐀𝐅𝐓, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
    bot.register_next_step_handler(sent, receive_save_draft_name)
    bot.answer_callback_query(call.id)

def receive_save_draft_name(message):
    uid = message.from_user.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!")
        return
    name = (message.text or "").strip()
    if not name:
        sent = _send_pe_return(message.chat.id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐀 𝐕𝐀𝐋𝐈𝐃 𝐍𝐀𝐌𝐄, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_save_draft_name)
        return
    result = save_manual_draft(uid, name)
    if result == "saved":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐃𝐑𝐀𝐅𝐓 \"{name}\" 𝐒𝐀𝐕𝐄𝐃!")
    elif result == "full":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐃𝐑𝐀𝐅𝐓 𝐋𝐈𝐌𝐈𝐓 𝐑𝐄𝐀𝐂𝐇𝐄𝐃 ({get_draft_limit(uid)}).")
    else:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐒𝐄𝐒𝐒𝐈𝐎𝐍 𝐄𝐗𝐏𝐈𝐑𝐄𝐃!")

# ============================================================
# TEMPLATES
# ============================================================

def get_user_templates(uid: int) -> list:
    """A user's own templates PLUS every global (admin-published)
    template, own templates first."""
    own = templates.get(str(uid), [])
    global_tpls = templates.get("global", [])
    return own + global_tpls

def save_template(uid: int, name: str, make_global: bool = False) -> str:
    """Returns 'saved' or 'full' or 'invalid'."""
    if uid not in temp_data:
        return "invalid"
    key = "global" if make_global else str(uid)
    user_templates = templates.get(key, [])
    if not make_global and len(user_templates) >= get_template_limit(uid):
        return "full"
    snapshot = snapshot_from_temp_data(temp_data[uid])
    user_templates.append({
        "id": make_unique_id("t"),
        "name": name[:40] or "Untitled Template",
        "global": make_global,
        "owner": uid,
        "snapshot": snapshot,
        "created_at": int(time.time()),
    })
    templates[key] = user_templates
    save_templates()
    return "saved"

def delete_template(uid: int, template_id: str) -> bool:
    """Users can delete their own templates; admins can delete any,
    including global ones."""
    for key in (str(uid), "global"):
        user_templates = templates.get(key, [])
        target = next((t for t in user_templates if t.get("id") == template_id), None)
        if target is None:
            continue
        if key == "global" and not has_permission(uid, PERM_MANAGE_TEMPLATES) and target.get("owner") != uid:
            return False
        if key != "global" and target.get("owner") != uid:
            continue
        filtered = [t for t in user_templates if t.get("id") != template_id]
        templates[key] = filtered
        save_templates()
        return True
    return False

def find_template(uid: int, template_id: str):
    for t in get_user_templates(uid):
        if t.get("id") == template_id:
            return t
    return None

def build_templates_list_page(uid: int, page: int):
    user_templates = get_user_templates(uid)
    total = len(user_templates)
    total_pages = max(1, (total + TEMPLATE_LIST_PAGE_SIZE - 1) // TEMPLATE_LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * TEMPLATE_LIST_PAGE_SIZE
    page_items = user_templates[start:start + TEMPLATE_LIST_PAGE_SIZE]

    text = f"""
{PLACEHOLDER}═══《 📑 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄𝐒 》═══{PLACEHOLDER}

𝐀𝐕𝐀𝐈𝐋𝐀𝐁𝐋𝐄: {total}

"""
    if not page_items:
        text += "𝐍𝐎 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄𝐒 𝐘𝐄𝐓. 𝐁𝐔𝐈𝐋𝐃 𝐀 𝐏𝐎𝐒𝐓 𝐀𝐍𝐃 𝐓𝐀𝐏 📑 𝐒𝐀𝐕𝐄 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄 𝐎𝐍 𝐈𝐓𝐒 𝐏𝐑𝐄𝐕𝐈𝐄𝐖.\n\n"
    text += f"{PLACEHOLDER}═════════════════════{PLACEHOLDER}\n"

    keyboard = []
    for t in page_items:
        label = t["name"][:26]
        tag = "🌐 " if t.get("global") else ""
        keyboard.append([make_button(text=f"📑 {tag}{label}", callback_data=f"tpl_open_{t['id']}_{uid}")])

    nav_row = []
    if page > 0:
        nav_row.append(make_button_with_icon(text="◀ 𝐏𝐑𝐄𝐕", style="secondary", callback_data=f"tpllist_{page-1}_{uid}"))
    if total_pages > 1:
        nav_row.append(make_button_with_icon(text=f"📄 {page+1}/{total_pages}", style="secondary", callback_data=f"tpllist_{page}_{uid}"))
    if page < total_pages - 1:
        nav_row.append(make_button_with_icon(text="𝐍𝐄𝐗𝐓 ▶", style="secondary", callback_data=f"tpllist_{page+1}_{uid}"))
    if nav_row:
        keyboard.append(nav_row)

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    return text, markup

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄𝐒"))
def templates_msg(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    text, markup = build_templates_list_page(uid, 0)
    _send_pe(message.chat.id, text, reply_markup=markup)

@bot.message_handler(commands=["templates"])
def templates_command(message):
    templates_msg(message)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tpllist_"))
def handle_templates_list_page(call):
    uid = call.from_user.id
    page = int(call.data.split("_")[1])
    text, markup = build_templates_list_page(uid, page)
    entities = _build_pe_entities(text)
    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id,
                              entities=entities, reply_markup=markup, parse_mode=None)
    except Exception:
        pass
    bot.answer_callback_query(call.id)

def build_template_detail(uid: int, template_id: str):
    t = find_template(uid, template_id)
    if not t:
        return None, None
    preview_line = snapshot_preview_line(t["snapshot"])
    tag = "🌐 𝐆𝐋𝐎𝐁𝐀𝐋 " if t.get("global") else ""
    text = f"""
{PLACEHOLDER}═══《 📑 {tag}{t['name']} 》═══{PLACEHOLDER}

{preview_line}

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    keyboard = [make_styled_row([{"text": "▶️ 𝐔𝐒𝐄", "style": "success", "callback_data": f"tpl_use_{template_id}_{uid}"}])]
    can_delete = (t.get("owner") == uid) or (t.get("global") and has_permission(uid, PERM_MANAGE_TEMPLATES))
    row2 = []
    if t.get("owner") == uid:
        row2.append({"text": "📋 𝐃𝐔𝐏𝐋𝐈𝐂𝐀𝐓𝐄", "style": "secondary", "callback_data": f"tpl_dup_{template_id}_{uid}"})
    if can_delete:
        row2.append({"text": "🗑 𝐃𝐄𝐋𝐄𝐓𝐄", "style": "danger", "callback_data": f"tpl_del_{template_id}_{uid}"})
    if row2:
        keyboard.append(make_styled_row(row2))
    keyboard.append([make_button_with_icon(text="🔙 𝐁𝐀𝐂𝐊", style="secondary", callback_data=f"tpllist_0_{uid}")])
    return text, InlineKeyboardMarkup(keyboard)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tpl_open_"))
def handle_template_open(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        _, _, template_id, _uid_str = call.data.split("_", 3)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    text, markup = build_template_detail(uid, template_id)
    if text is None:
        bot.answer_callback_query(call.id, "That template no longer exists.")
        return
    entities = _build_pe_entities(text)
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id,
                              entities=entities, reply_markup=markup, parse_mode=None)
    except Exception:
        pass
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tpl_use_"))
def handle_template_use(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    try:
        _, _, template_id, _uid_str = call.data.split("_", 3)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    t = find_template(uid, template_id)
    if not t:
        bot.answer_callback_query(call.id, "That template no longer exists.")
        return
    temp_data[uid] = temp_data_from_snapshot(t["snapshot"])
    bot.answer_callback_query(call.id, "Template loaded!")
    button_editor_hub(chat_id, uid)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tpl_dup_"))
def handle_template_duplicate(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        _, _, template_id, _uid_str = call.data.split("_", 3)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    t = find_template(uid, template_id)
    if not t or t.get("owner") != uid:
        bot.answer_callback_query(call.id, "Can't duplicate that template.")
        return
    key = str(uid)
    user_templates = templates.get(key, [])
    if len(user_templates) >= get_template_limit(uid):
        bot.answer_callback_query(call.id, f"Template limit reached ({get_template_limit(uid)}).", show_alert=True)
        return
    user_templates.append({
        "id": make_unique_id("t"),
        "name": f"{t['name']} (copy)"[:40],
        "global": False,
        "owner": uid,
        "snapshot": t["snapshot"],
        "created_at": int(time.time()),
    })
    templates[key] = user_templates
    save_templates()
    text, markup = build_templates_list_page(uid, 0)
    entities = _build_pe_entities(text)
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id,
                              entities=entities, reply_markup=markup, parse_mode=None)
    except Exception:
        pass
    bot.answer_callback_query(call.id, "Duplicated!")

@bot.callback_query_handler(func=lambda c: c.data.startswith("tpl_del_"))
def handle_template_delete(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        _, _, template_id, _uid_str = call.data.split("_", 3)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    removed = delete_template(uid, template_id)
    if removed:
        text, markup = build_templates_list_page(uid, 0)
        entities = _build_pe_entities(text)
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id,
                                  entities=entities, reply_markup=markup, parse_mode=None)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Template deleted.")
    else:
        bot.answer_callback_query(call.id, "Not allowed or already removed.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("savetpl_"))
def handle_save_template_prompt(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    if uid not in temp_data:
        bot.answer_callback_query(call.id, "Session expired!")
        return
    sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐀 𝐍𝐀𝐌𝐄 𝐅𝐎𝐑 𝐓𝐇𝐈𝐒 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
    bot.register_next_step_handler(sent, receive_save_template_name)
    bot.answer_callback_query(call.id)

def receive_save_template_name(message):
    uid = message.from_user.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!")
        return
    name = (message.text or "").strip()
    if not name:
        sent = _send_pe_return(message.chat.id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃 𝐀 𝐕𝐀𝐋𝐈𝐃 𝐍𝐀𝐌𝐄, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
        bot.register_next_step_handler(sent, receive_save_template_name)
        return

    if has_permission(uid, PERM_MANAGE_TEMPLATES):
        keyboard = [make_styled_row([
            {"text": "👤 𝐉𝐔𝐒𝐓 𝐌𝐄", "style": "secondary", "callback_data": f"tplscope_own_{uid}"},
            {"text": "🌐 𝐆𝐋𝐎𝐁𝐀𝐋 (𝐄𝐕𝐄𝐑𝐘𝐎𝐍𝐄)", "style": "primary", "callback_data": f"tplscope_global_{uid}"},
        ])]
        pending_template_names[uid] = name
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐒𝐀𝐕𝐄 𝐓𝐇𝐈𝐒 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄 𝐅𝐎𝐑:",
                 reply_markup=InlineKeyboardMarkup(keyboard))
        return

    result = save_template(uid, name, make_global=False)
    if result == "saved":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄 \"{name}\" 𝐒𝐀𝐕𝐄𝐃!")
    elif result == "full":
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄 𝐋𝐈𝐌𝐈𝐓 𝐑𝐄𝐀𝐂𝐇𝐄𝐃 ({get_template_limit(uid)}).")
    else:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐒𝐄𝐒𝐒𝐈𝐎𝐍 𝐄𝐗𝐏𝐈𝐑𝐄𝐃!")

pending_template_names = {}  # uid -> name, transient (admin scope-choice step only)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tplscope_"))
def handle_template_scope_choice(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    parts = call.data.split("_")
    scope = parts[1]
    name = pending_template_names.pop(uid, None)
    if not name:
        bot.answer_callback_query(call.id, "Session expired!")
        return
    result = save_template(uid, name, make_global=(scope == "global"))
    if result == "saved":
        scope_label = "🌐 globally" if scope == "global" else "for you"
        bot.answer_callback_query(call.id, f"Template \"{name}\" saved {scope_label}!")
    elif result == "full":
        bot.answer_callback_query(call.id, f"Template limit reached.", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "Session expired!")

# ============================================================
# MAKE POST — offer drafts/templates before a blank start
# ============================================================
# start_post() (defined far above, in the original post-builder
# section) currently jumps straight to "send your text now". Instead
# of touching that function, MAKE POST's message handler is
# intercepted one step earlier: this new handler runs FIRST for the
# same button text, checks whether the user has anything to resume,
# and either shows a chooser or calls the original flow directly by
# re-dispatching to it (falls straight through with no menu at all
# for a user with nothing saved — same experience as before Phase 3).
# ============================================================

_original_start_post = start_post

def start_post_with_chooser(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)

    if not bot_active and not is_admin(uid):
        _original_start_post(message)
        return

    has_drafts = bool(get_user_drafts(uid))
    has_templates = bool(get_user_templates(uid))

    if not has_drafts and not has_templates:
        _original_start_post(message)
        return

    text = f"""
{PLACEHOLDER}═══《 ✍️ 𝐂𝐑𝐄𝐀𝐓𝐄 𝐏𝐎𝐒𝐓 》═══{PLACEHOLDER}

𝐒𝐓𝐀𝐑𝐓 𝐅𝐑𝐄𝐒𝐇, 𝐎𝐑 𝐏𝐈𝐂𝐊 𝐔𝐏 𝐖𝐇𝐄𝐑𝐄 𝐘𝐎𝐔 𝐋𝐄𝐅𝐓 𝐎𝐅𝐅:

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    keyboard = [[make_button_with_icon(text="🆕 𝐒𝐓𝐀𝐑𝐓 𝐁𝐋𝐀𝐍𝐊", style="success", callback_data=f"mpstart_blank_{uid}")]]
    if has_drafts:
        keyboard.append([make_button_with_icon(text="📝 𝐑𝐄𝐒𝐔𝐌𝐄 𝐀 𝐃𝐑𝐀𝐅𝐓", style="primary", callback_data=f"mpstart_drafts_{uid}")])
    if has_templates:
        keyboard.append([make_button_with_icon(text="📑 𝐔𝐒𝐄 𝐀 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄", style="primary", callback_data=f"mpstart_templates_{uid}")])
    _send_pe(message.chat.id, text, reply_markup=InlineKeyboardMarkup(keyboard))

# Re-register the MAKE POST button on the new handler. python-telebot
# checks message handlers in registration order and uses the first
# match, so simply registering a second handler for the same button
# text would leave the OLD start_post as the one that actually runs
# (it was registered first). Instead, the bot's internal handler list
# is edited directly: swap the old start_post entry for the new
# chooser, in place, so registration order (and every other handler)
# is completely undisturbed.
for _h in bot.message_handlers:
    if _h.get("handler") is _original_start_post:
        _h["handler"] = start_post_with_chooser
        break

@bot.callback_query_handler(func=lambda c: c.data.startswith("mpstart_"))
def handle_make_post_chooser(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    parts = call.data.split("_")
    choice = parts[1]

    if choice == "blank":
        bot.answer_callback_query(call.id)
        _original_start_post(call.message)
        return
    elif choice == "drafts":
        text, markup = build_drafts_list_page(uid, 0)
        _send_pe(chat_id, text, reply_markup=markup)
    elif choice == "templates":
        text, markup = build_templates_list_page(uid, 0)
        _send_pe(chat_id, text, reply_markup=markup)

    bot.answer_callback_query(call.id)

# ============================================================
# Hook auto-save into the existing checkpoints, and add the
# save-draft / save-template buttons to the preview action row.
# Same "edit the handler list in place" technique as above, applied
# to process_post_text (text checkpoint), receive_media (media
# checkpoint — its two_wrapped call sites inside handle_media_selection
# and receive_media both flow through button_editor_hub, so hooking
# button_editor_hub itself covers every path that reaches it), and
# create_preview (adds the two new buttons to the action row).
# ============================================================

_original_button_editor_hub = button_editor_hub

def button_editor_hub_with_autosave(chat_id, uid):
    auto_save_draft(uid)
    _original_button_editor_hub(chat_id, uid)

for _name in ("button_editor_hub",):
    globals()[_name] = button_editor_hub_with_autosave

_original_create_preview = create_preview

def create_preview_with_template_draft_buttons(chat_id, uid):
    auto_save_draft(uid)
    _original_create_preview(chat_id, uid)
    # Append a third action row (Save Draft / Save Template) onto the
    # action message that _original_create_preview just sent, by
    # editing it in place rather than duplicating its send logic.
    data = temp_data.get(uid)
    if not data or not data.get("action_msg_id"):
        return
    extra_row = make_styled_row([
        {"text": "💾 𝐒𝐀𝐕𝐄 𝐃𝐑𝐀𝐅𝐓", "style": "secondary", "callback_data": f"savedraft_{uid}"},
        {"text": "📑 𝐒𝐀𝐕𝐄 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄", "style": "secondary", "callback_data": f"savetpl_{uid}"},
    ])
    action_keyboard = [
        make_styled_row([
            {"text": "𝐑𝐄𝐅𝐑𝐄𝐒𝐇", "style": "primary", "callback_data": f"refresh_{uid}"},
            {"text": "𝐃𝐄𝐋𝐄𝐓𝐄", "style": "danger", "callback_data": f"delete_{uid}"},
        ]),
        make_styled_row([
            {"text": "𝐃𝐎𝐍𝐄", "style": "success", "callback_data": f"done_{uid}"},
            {"text": "𝐅𝐎𝐑𝐖𝐀𝐑𝐃", "style": "primary", "callback_data": f"forward_{uid}"},
        ]),
        extra_row,
    ]
    try:
        bot.edit_message_text(
            f"{PLACEHOLDER}═══《 ✨ 𝐏𝐑𝐄𝐕𝐈𝐄𝐖 𝐑𝐄𝐀𝐃𝐘 》═══{PLACEHOLDER}\n\n𝐘𝐨𝐮𝐫 𝐩𝐨𝐬𝐭 𝐢𝐬 𝐫𝐞𝐚𝐝𝐲!\n\n{PLACEHOLDER} 𝐑𝐄𝐅𝐑𝐄𝐒𝐇 - 𝐍𝐞𝐰 𝐞𝐦𝐨𝐣𝐢𝐬\n{PLACEHOLDER} 𝐃𝐄𝐋𝐄𝐓𝐄 - 𝐑𝐞𝐦𝐨𝐯𝐞 𝐩𝐫𝐞𝐯𝐢𝐞𝐰\n{PLACEHOLDER} 𝐃𝐎𝐍𝐄 - 𝐅𝐢𝐧𝐢𝐬𝐡\n{PLACEHOLDER} 𝐅𝐎𝐑𝐖𝐀𝐑𝐃 - 𝐁𝐫𝐨𝐚𝐝𝐜𝐚𝐬𝐭 𝐰𝐢𝐭𝐡 𝐛𝐮𝐭𝐭𝐨𝐧𝐬\n{PLACEHOLDER} 𝐒𝐀𝐕𝐄 𝐃𝐑𝐀𝐅𝐓/𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄 - 𝐊𝐞𝐞𝐩 𝐟𝐨𝐫 𝐥𝐚𝐭𝐞𝐫\n\n{PLACEHOLDER}═════════════════════{PLACEHOLDER}",
            chat_id=chat_id, message_id=data["action_msg_id"],
            entities=_build_pe_entities(f"{PLACEHOLDER}═══《 ✨ 𝐏𝐑𝐄𝐕𝐈𝐄𝐖 𝐑𝐄𝐀𝐃𝐘 》═══{PLACEHOLDER}\n\n𝐘𝐨𝐮𝐫 𝐩𝐨𝐬𝐭 𝐢𝐬 𝐫𝐞𝐚𝐝𝐲!\n\n{PLACEHOLDER} 𝐑𝐄𝐅𝐑𝐄𝐒𝐇 - 𝐍𝐞𝐰 𝐞𝐦𝐨𝐣𝐢𝐬\n{PLACEHOLDER} 𝐃𝐄𝐋𝐄𝐓𝐄 - 𝐑𝐞𝐦𝐨𝐯𝐞 𝐩𝐫𝐞𝐯𝐢𝐞𝐰\n{PLACEHOLDER} 𝐃𝐎𝐍𝐄 - 𝐅𝐢𝐧𝐢𝐬𝐡\n{PLACEHOLDER} 𝐅𝐎𝐑𝐖𝐀𝐑𝐃 - 𝐁𝐫𝐨𝐚𝐝𝐜𝐚𝐬𝐭 𝐰𝐢𝐭𝐡 𝐛𝐮𝐭𝐭𝐨𝐧𝐬\n{PLACEHOLDER} 𝐒𝐀𝐕𝐄 𝐃𝐑𝐀𝐅𝐓/𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄 - 𝐊𝐞𝐞𝐩 𝐟𝐨𝐫 𝐥𝐚𝐭𝐞𝐫\n\n{PLACEHOLDER}═════════════════════{PLACEHOLDER}"),
            reply_markup=InlineKeyboardMarkup(action_keyboard), parse_mode=None,
        )
    except Exception:
        pass

globals()["create_preview"] = create_preview_with_template_draft_buttons

# Clear the autosave slot once a post is actually finished (Done) or
# explicitly discarded (Delete on the preview screen) — resuming a
# finished post isn't useful, and an abandoned one shouldn't linger.
_original_handle_preview_actions_func = None
for _h in bot.callback_query_handlers:
    if _h.get("handler") and _h["handler"].__name__ == "handle_preview_actions":
        _original_handle_preview_actions_func = _h["handler"]
        break

if _original_handle_preview_actions_func:
    def handle_preview_actions_with_draft_cleanup(call):
        uid = call.from_user.id
        action = call.data.split("_")[0]
        if action in ("done", "delete"):
            clear_autosave_draft(uid)
        _original_handle_preview_actions_func(call)

    for _h in bot.callback_query_handlers:
        if _h.get("handler") is _original_handle_preview_actions_func:
            _h["handler"] = handle_preview_actions_with_draft_cleanup
            break


# ============================================================
# PHASE 4 — PROFILES, REFERRALS, ANALYTICS
# ============================================================
# (XP/Levels/Leaderboard intentionally excluded from this phase.)
#
# New JSON file (kept separate from every other file — nothing below
# reads or writes users.json, usernames.json, emoji_packs.json,
# custom_packs.json, emoji_categories.json, favorites.json,
# emoji_names.json, drafts.json, or templates.json):
#   user_stats.json  - per-user: join_date, posts_created,
#                       referred_by, referral_count
#
# New menu buttons: 👤 MY PROFILE, 👥 REFERRALS (everyone)
# New commands: /profile, /referrals
# New admin screen: 📈 DETAILED ANALYTICS (extends the existing
# 🪾 STATS button's admin view rather than adding a separate button)
#
# Referral flow: /start ref_<inviter_id> — Telegram passes this as
# message.text = "/start ref_12345". Only counts on a user's FIRST
# EVER /start (checked against user_stats, not all_users, since
# all_users is written by register_user() which Phase 4 also hooks —
# see the ordering note in hook_referral_from_start_payload). A user
# cannot refer themselves; a user who already has a referred_by
# can't be re-attributed to a different inviter by starting the bot
# again with a different link.
# ============================================================

USER_STATS_FILE = "user_stats.json"
user_stats: dict = load_json_file(USER_STATS_FILE, {})

def save_user_stats():
    save_json_file(USER_STATS_FILE, user_stats)

def get_user_stat_record(uid: int) -> dict:
    """Always returns a record (creating a bare one if this is
    somehow the first time we're seeing this uid through Phase 4
    code, e.g. an existing user from before this phase was added)."""
    key = str(uid)
    if key not in user_stats:
        user_stats[key] = {
            "join_date": int(time.time()),
            "posts_created": 0,
            "referred_by": None,
            "referral_count": 0,
        }
        save_user_stats()
    return user_stats[key]

# ---- hook into register_user() for join_date, without touching its
#      14 existing call sites or its own source ----
_original_register_user = register_user

def register_user_with_stats(uid: int, username: str = None):
    is_new = uid not in all_users
    _original_register_user(uid, username)
    if is_new:
        key = str(uid)
        if key not in user_stats:
            user_stats[key] = {
                "join_date": int(time.time()),
                "posts_created": 0,
                "referred_by": None,
                "referral_count": 0,
            }
            save_user_stats()

globals()["register_user"] = register_user_with_stats

# ---- referral capture on /start ref_<uid> ----
# welcome() (the /start handler) is registered with commands=["start"],
# so telebot already strips the "/start" token and passes the rest as
# message.text in most client flows, but not reliably across every
# telebot version/config — the deep-link payload is read defensively
# from the raw text instead of relying on that stripping.

def _extract_referral_payload(message) -> str:
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()

_original_welcome = welcome

def welcome_with_referral(message):
    uid = message.from_user.id
    payload = _extract_referral_payload(message)

    # Only ever attribute a referral on a user's very first /start,
    # BEFORE calling the original handler (which calls register_user
    # and would otherwise make "is this a new user" unanswerable).
    is_first_start = str(uid) not in user_stats and uid not in all_users

    _original_welcome(message)

    if is_first_start and payload.startswith("ref_"):
        try:
            inviter_id = int(payload[4:])
        except ValueError:
            inviter_id = None
        if inviter_id and inviter_id != uid:
            my_record = get_user_stat_record(uid)
            if not my_record.get("referred_by"):
                my_record["referred_by"] = inviter_id
                save_user_stats()
                if str(inviter_id) in user_stats or inviter_id in all_users:
                    inviter_record = get_user_stat_record(inviter_id)
                    inviter_record["referral_count"] = inviter_record.get("referral_count", 0) + 1
                    save_user_stats()

for _h in bot.message_handlers:
    if _h.get("handler") is _original_welcome:
        _h["handler"] = welcome_with_referral
        break

def build_referral_text(uid: int) -> str:
    record = get_user_stat_record(uid)
    count = record.get("referral_count", 0)
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}" if BOT_USERNAME else f"(set BOT_USERNAME to show your link)"
    text = f"""
{PLACEHOLDER}═══《 👥 𝐑𝐄𝐅𝐄𝐑𝐑𝐀𝐋𝐒 》═══{PLACEHOLDER}

𝐈𝐍𝐕𝐈𝐓𝐄 𝐅𝐑𝐈𝐄𝐍𝐃𝐒 𝐖𝐈𝐓𝐇 𝐘𝐎𝐔𝐑 𝐋𝐈𝐍𝐊:
{link}

𝐒𝐔𝐂𝐂𝐄𝐒𝐒𝐅𝐔𝐋 𝐑𝐄𝐅𝐄𝐑𝐑𝐀𝐋𝐒: {count}

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    return text

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐑𝐄𝐅𝐄𝐑𝐑𝐀𝐋𝐒"))
def referrals_msg(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    kb = get_menu(uid)
    _send_pe(message.chat.id, build_referral_text(uid), reply_markup=kb)

@bot.message_handler(commands=["referrals"])
def referrals_command(message):
    referrals_msg(message)

# ---- post-created counter, hooked onto the preview "Done" action
#      the same way Phase 3 hooked handle_preview_actions (stacking
#      on top of Phase 3's wrapper, not replacing it) ----

_handler_before_phase4_stats = None
for _h in bot.callback_query_handlers:
    if _h.get("handler") and "handle_preview_actions" in _h["handler"].__name__:
        _handler_before_phase4_stats = _h["handler"]
        break

if _handler_before_phase4_stats:
    def handle_preview_actions_with_post_count(call):
        uid = call.from_user.id
        action = call.data.split("_")[0]
        if action == "done":
            record = get_user_stat_record(uid)
            record["posts_created"] = record.get("posts_created", 0) + 1
            save_user_stats()
        _handler_before_phase4_stats(call)

    for _h in bot.callback_query_handlers:
        if _h.get("handler") is _handler_before_phase4_stats:
            _h["handler"] = handle_preview_actions_with_post_count
            break

# ============================================================
# PROFILE
# ============================================================

def build_profile_text(uid: int, username: str = None) -> str:
    record = get_user_stat_record(uid)
    join_dt = datetime.fromtimestamp(record.get("join_date", int(time.time())))
    join_str = join_dt.strftime("%d %b %Y")

    favorites_count = len(get_user_favorites(uid))
    templates_count = len([t for t in get_user_templates(uid) if t.get("owner") == uid])
    drafts_count = len([d for d in get_user_drafts(uid) if not d.get("autosave")])
    posts_count = record.get("posts_created", 0)
    referral_count = record.get("referral_count", 0)

    uname_line = f"@{username}" if username else "(no username set)"

    text = f"""
{PLACEHOLDER}═══《 👤 𝐌𝐘 𝐏𝐑𝐎𝐅𝐈𝐋𝐄 》═══{PLACEHOLDER}

𝐔𝐒𝐄𝐑 𝐈𝐃: {uid}
𝐔𝐒𝐄𝐑𝐍𝐀𝐌𝐄: {uname_line}
𝐉𝐎𝐈𝐍𝐄𝐃: {join_str}

📝 𝐏𝐎𝐒𝐓𝐒 𝐂𝐑𝐄𝐀𝐓𝐄𝐃: {posts_count}
⭐ 𝐅𝐀𝐕𝐎𝐑𝐈𝐓𝐄𝐒: {favorites_count}
📑 𝐓𝐄𝐌𝐏𝐋𝐀𝐓𝐄𝐒: {templates_count}
📝 𝐒𝐀𝐕𝐄𝐃 𝐃𝐑𝐀𝐅𝐓𝐒: {drafts_count}
👥 𝐑𝐄𝐅𝐄𝐑𝐑𝐀𝐋𝐒: {referral_count}
💎 𝐏𝐋𝐀𝐍: 𝐅𝐫𝐞𝐞

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    return text

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐌𝐘 𝐏𝐑𝐎𝐅𝐈𝐋𝐄"))
def profile_msg(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    kb = get_menu(uid)
    text = build_profile_text(uid, message.from_user.username)
    _send_pe(message.chat.id, text, reply_markup=kb)

@bot.message_handler(commands=["profile"])
def profile_command(message):
    profile_msg(message)

# ============================================================
# ANALYTICS (admin) — extends the existing 🪾 STATS screen
# ============================================================
# stats_msg() (the 🪾 STATS / 🍁 BOT STATS button) is public and shown
# to everyone with basic numbers. Rather than change what non-admins
# see there, a new admin-only button/command is added for the deeper
# breakdown the original spec calls for (new users today/this week,
# posts created, referral totals, favorite/template usage). This
# reuses data already being tracked by this phase and Phase 1/3 —
# nothing here introduces a new tracking mechanism, it's read-only
# aggregation over user_stats / favorites / templates / drafts.

def _count_new_users_since(cutoff_ts: int) -> int:
    count = 0
    for record in user_stats.values():
        if record.get("join_date", 0) >= cutoff_ts:
            count += 1
    return count

def build_analytics_text() -> str:
    now = int(time.time())
    day_ago = now - 86400
    week_ago = now - 7 * 86400

    total_users = len(all_users)
    new_today = _count_new_users_since(day_ago)
    new_week = _count_new_users_since(week_ago)

    total_posts = sum(r.get("posts_created", 0) for r in user_stats.values())
    total_referrals = sum(r.get("referral_count", 0) for r in user_stats.values())
    referred_users = sum(1 for r in user_stats.values() if r.get("referred_by"))

    total_favorites = sum(len(v) for v in favorites.values())
    total_templates = sum(len(v) for k, v in templates.items())
    total_drafts = sum(len([d for d in v if not d.get("autosave")]) for v in drafts.values())

    text = f"""
{PLACEHOLDER}═══《 📈 𝐃𝐄𝐓𝐀𝐈𝐋𝐄𝐃 𝐀𝐍𝐀𝐋𝐘𝐓𝐈𝐂𝐒 》═══{PLACEHOLDER}

👥 𝐔𝐒𝐄𝐑𝐒
• 𝐓𝐨𝐭𝐚𝐥: {total_users}
• 𝐍𝐞𝐰 𝐭𝐨𝐝𝐚𝐲: {new_today}
• 𝐍𝐞𝐰 𝐭𝐡𝐢𝐬 𝐰𝐞𝐞𝐤: {new_week}

📝 𝐏𝐎𝐒𝐓𝐒
• 𝐓𝐨𝐭𝐚𝐥 𝐜𝐫𝐞𝐚𝐭𝐞𝐝: {total_posts}

👥 𝐑𝐄𝐅𝐄𝐑𝐑𝐀𝐋𝐒
• 𝐒𝐮𝐜𝐜𝐞𝐬𝐬𝐟𝐮𝐥: {total_referrals}
• 𝐔𝐬𝐞𝐫𝐬 𝐰𝐡𝐨 𝐜𝐚𝐦𝐞 𝐯𝐢𝐚 𝐫𝐞𝐟𝐞𝐫𝐫𝐚𝐥: {referred_users}

⭐ 𝐄𝐍𝐆𝐀𝐆𝐄𝐌𝐄𝐍𝐓
• 𝐅𝐚𝐯𝐨𝐫𝐢𝐭𝐞𝐬 𝐬𝐚𝐯𝐞𝐝: {total_favorites}
• 𝐓𝐞𝐦𝐩𝐥𝐚𝐭𝐞𝐬 𝐬𝐚𝐯𝐞𝐝: {total_templates}
• 𝐃𝐫𝐚𝐟𝐭𝐬 𝐬𝐚𝐯𝐞𝐝: {total_drafts}

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    return text

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐃𝐄𝐓𝐀𝐈𝐋𝐄𝐃 𝐀𝐍𝐀𝐋𝐘𝐓𝐈𝐂𝐒") and has_permission(m.from_user.id, PERM_VIEW_ANALYTICS))
def analytics_msg(message):
    kb = get_menu(message.from_user.id)
    _send_pe(message.chat.id, build_analytics_text(), reply_markup=kb)

@bot.message_handler(commands=["analytics"])
def analytics_command(message):
    if not has_permission(message.from_user.id, PERM_VIEW_ANALYTICS):
        return
    analytics_msg(message)


# ============================================================
# PHASE 5 — PREMIUM MEMBERSHIP, EMOJI STORE, PROMO CODES
# ============================================================
# IMPORTANT SCOPE NOTE: this bot has no payment processing anywhere
# (no Stripe, no Telegram Payments API, no crypto). Premium/VIP status
# in this phase is ADMIN-GRANTED ONLY — an admin runs a command to
# give or remove a plan. There is no "buy" button anywhere that
# charges real money, and nothing here pretends otherwise. Wiring in
# an actual payment method (Telegram Stars is the natural fit for a
# Telegram bot) is a clean follow-up phase once you've decided which
# to use — the plan/limit infrastructure built here doesn't need to
# change when that happens, only "how does a user get upgraded" does.
#
# New JSON files (kept separate from every other file — nothing below
# reads or writes any of the 8 files earlier phases use):
#   plans.json        - configurable limits per plan (admin-editable)
#   store_items.json  - admin-published catalog (Section 16)
#   promo_codes.json  - codes + redemption tracking (Section 17)
#
# New admin commands:
#   /grantpremium <uid> [days]   - e.g. /grantpremium 12345 30
#   /grantvip <uid> [days]       - omit [days] for "no expiry"
#   /revokeplan <uid>            - back to Free immediately
#   /addstoreitem <name> | <plan required> | <description>
#   /removestoreitem <id>
#   /addpromo <CODE> <plan_or_none> <days> <max_uses> <per_user_uses>
#   /removepromo <CODE>
# New user commands: /redeem <CODE>, /store
# New menu buttons: 🛍 EMOJI STORE (everyone); the existing 👤 MY
# PROFILE screen now shows the user's real plan instead of the
# Phase 4 static "Free" placeholder.
# ============================================================

PLANS_FILE = "plans.json"
STORE_ITEMS_FILE = "store_items.json"
PROMO_CODES_FILE = "promo_codes.json"

DEFAULT_PLANS = {
    "free": {"label": "Free", "favorite_limit": DEFAULT_FAVORITE_LIMIT,
              "draft_limit": MAX_DRAFTS_PER_USER, "template_limit": MAX_TEMPLATES_PER_USER},
    "premium": {"label": "💎 Premium", "favorite_limit": 200,
                 "draft_limit": 25, "template_limit": 50},
    "vip": {"label": "👑 VIP", "favorite_limit": 1000,
             "draft_limit": 100, "template_limit": 200},
}

plans: dict = load_json_file(PLANS_FILE, DEFAULT_PLANS)
store_items: dict = load_json_file(STORE_ITEMS_FILE, {})
promo_codes: dict = load_json_file(PROMO_CODES_FILE, {})

def save_plans():
    save_json_file(PLANS_FILE, plans)

def save_store_items():
    save_json_file(STORE_ITEMS_FILE, store_items)

def save_promo_codes():
    save_json_file(PROMO_CODES_FILE, promo_codes)

# ---- plan resolution (with lazy expiry) ----

def get_user_plan(uid: int) -> str:
    """Returns 'free' | 'premium' | 'vip'. Expires a timed plan back
    to 'free' on read if its expiry has passed — there's no
    background scheduler in this bot, so expiry is checked whenever
    the plan is looked up rather than on a timer."""
    record = get_user_stat_record(uid)
    plan = record.get("plan", "free")
    expires = record.get("plan_expires")
    if plan != "free" and expires is not None and int(time.time()) >= expires:
        record["plan"] = "free"
        record["plan_expires"] = None
        save_user_stats()
        return "free"
    return plan

def set_user_plan(uid: int, plan: str, days: int = None) -> bool:
    """days=None means no expiry. Returns False for an unknown plan id."""
    if plan not in plans:
        return False
    record = get_user_stat_record(uid)
    record["plan"] = plan
    record["plan_expires"] = (int(time.time()) + days * 86400) if days else None
    save_user_stats()
    return True

def get_plan_label(plan_id: str) -> str:
    return plans.get(plan_id, {}).get("label", plan_id.title())

# ---- wire the three existing limit functions to be plan-aware ----
# get_favorite_limit already existed (Phase 1 built it as a function
# specifically so this override wouldn't need to touch its call
# sites); get_draft_limit / get_template_limit are new here — the 6
# call sites in the Phase 3 section were updated in this same pass to
# call these by name instead of the old flat MAX_DRAFTS_PER_USER /
# MAX_TEMPLATES_PER_USER constants (which still exist, now only as
# the Free-tier fallback default inside DEFAULT_PLANS above).

def get_favorite_limit_by_plan(uid: int) -> int:
    plan = get_user_plan(uid)
    return plans.get(plan, plans["free"]).get("favorite_limit", DEFAULT_FAVORITE_LIMIT)

def get_draft_limit(uid: int) -> int:
    plan = get_user_plan(uid)
    return plans.get(plan, plans["free"]).get("draft_limit", MAX_DRAFTS_PER_USER)

def get_template_limit(uid: int) -> int:
    plan = get_user_plan(uid)
    return plans.get(plan, plans["free"]).get("template_limit", MAX_TEMPLATES_PER_USER)

globals()["get_favorite_limit"] = get_favorite_limit_by_plan

# ---- Profile screen: show the real plan instead of Phase 4's static
#      "Free" placeholder, without touching Phase 4's source ----

_original_build_profile_text = build_profile_text

def build_profile_text_with_plan(uid: int, username: str = None) -> str:
    text = _original_build_profile_text(uid, username)
    plan_label = get_plan_label(get_user_plan(uid))
    return text.replace("💎 𝐏𝐋𝐀𝐍: 𝐅𝐫𝐞𝐞", f"💎 𝐏𝐋𝐀𝐍: {plan_label}")

globals()["build_profile_text"] = build_profile_text_with_plan

# ============================================================
# ADMIN: GRANT / REVOKE PLANS
# ============================================================

def _parse_uid_and_days(message, require_days=False):
    parts = (message.text or "").split()
    if len(parts) < 2:
        return None, None, "usage"
    try:
        target_uid = int(parts[1])
    except ValueError:
        return None, None, "bad_uid"
    days = None
    if len(parts) >= 3:
        try:
            days = int(parts[2])
        except ValueError:
            return None, None, "bad_days"
    elif require_days:
        return None, None, "usage"
    return target_uid, days, None

@bot.message_handler(commands=["grantpremium"])
def grant_premium_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_PREMIUM):
        return
    target_uid, days, err = _parse_uid_and_days(message)
    if err:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /grantpremium <user_id> [days] (𝐨𝐦𝐢𝐭 𝐝𝐚𝐲𝐬 𝐟𝐨𝐫 𝐧𝐨 𝐞𝐱𝐩𝐢𝐫𝐲)")
        return
    set_user_plan(target_uid, "premium", days)
    expiry_note = f"𝐟𝐨𝐫 {days} 𝐝𝐚𝐲𝐬" if days else "𝐰𝐢𝐭𝐡 𝐧𝐨 𝐞𝐱𝐩𝐢𝐫𝐲"
    _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐆𝐫𝐚𝐧𝐭𝐞𝐝 💎 𝐏𝐫𝐞𝐦𝐢𝐮𝐦 𝐭𝐨 {target_uid} {expiry_note}.")

@bot.message_handler(commands=["grantvip"])
def grant_vip_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_PREMIUM):
        return
    target_uid, days, err = _parse_uid_and_days(message)
    if err:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /grantvip <user_id> [days] (𝐨𝐦𝐢𝐭 𝐝𝐚𝐲𝐬 𝐟𝐨𝐫 𝐧𝐨 𝐞𝐱𝐩𝐢𝐫𝐲)")
        return
    set_user_plan(target_uid, "vip", days)
    expiry_note = f"𝐟𝐨𝐫 {days} 𝐝𝐚𝐲𝐬" if days else "𝐰𝐢𝐭𝐡 𝐧𝐨 𝐞𝐱𝐩𝐢𝐫𝐲"
    _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐆𝐫𝐚𝐧𝐭𝐞𝐝 👑 𝐕𝐈𝐏 𝐭𝐨 {target_uid} {expiry_note}.")

@bot.message_handler(commands=["revokeplan"])
def revoke_plan_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_PREMIUM):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /revokeplan <user_id>")
        return
    try:
        target_uid = int(parts[1])
    except ValueError:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐈𝐍𝐕𝐀𝐋𝐈𝐃 𝐔𝐒𝐄𝐑 𝐈𝐃.")
        return
    set_user_plan(target_uid, "free")
    _send_pe(message.chat.id, f"{PLACEHOLDER} {target_uid} 𝐢𝐬 𝐧𝐨𝐰 𝐨𝐧 𝐭𝐡𝐞 𝐅𝐫𝐞𝐞 𝐩𝐥𝐚𝐧.")

# ============================================================
# EMOJI STORE
# ============================================================
# A catalog admins publish to. Each item is tagged with the minimum
# plan required to use it (free/premium/vip). Since there's no
# payment flow, "buying" a free item just confirms it's available;
# a paid item shows what plan unlocks it rather than a purchase
# button that would charge nothing and mean nothing.

PLAN_RANK = {"free": 0, "premium": 1, "vip": 2}

def user_meets_plan(uid: int, required_plan: str) -> bool:
    return PLAN_RANK.get(get_user_plan(uid), 0) >= PLAN_RANK.get(required_plan, 0)

@bot.message_handler(commands=["addstoreitem"])
def add_store_item_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_PREMIUM):
        return
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2 or "|" not in raw[1]:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /addstoreitem Name | plan | description\n𝐩𝐥𝐚𝐧 𝐢𝐬 𝐨𝐧𝐞 𝐨𝐟: free, premium, vip")
        return
    fields = [f.strip() for f in raw[1].split("|")]
    if len(fields) < 2:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐌𝐈𝐒𝐒𝐈𝐍𝐆 𝐅𝐈𝐄𝐋𝐃𝐒. 𝐔𝐒𝐀𝐆𝐄: /addstoreitem Name | plan | description")
        return
    name, plan_req = fields[0], fields[1].lower()
    description = fields[2] if len(fields) > 2 else ""
    if plan_req not in PLAN_RANK:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐏𝐋𝐀𝐍 𝐌𝐔𝐒𝐓 𝐁𝐄 free, premium, 𝐨𝐫 vip.")
        return
    item_id = make_unique_id("item")
    store_items[item_id] = {
        "id": item_id, "name": name[:60], "plan_required": plan_req,
        "description": description[:200], "created_at": int(time.time()),
    }
    save_store_items()
    _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐀𝐝𝐝𝐞𝐝 \"{name}\" 𝐭𝐨 𝐭𝐡𝐞 𝐬𝐭𝐨𝐫𝐞 ({plan_req}).")

@bot.message_handler(commands=["removestoreitem"])
def remove_store_item_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_PREMIUM):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or parts[1] not in store_items:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /removestoreitem <item_id> (𝐬𝐞𝐞 🛍 𝐄𝐌𝐎𝐉𝐈 𝐒𝐓𝐎𝐑𝐄 𝐟𝐨𝐫 𝐈𝐃𝐬)")
        return
    removed = store_items.pop(parts[1])
    save_store_items()
    _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐑𝐞𝐦𝐨𝐯𝐞𝐝 \"{removed['name']}\".")

def build_store_page(uid: int, page: int):
    items = list(store_items.values())
    items.sort(key=lambda i: PLAN_RANK.get(i.get("plan_required", "free"), 0))
    total = len(items)
    page_size = 6
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    page_items = items[start:start + page_size]

    text = f"""
{PLACEHOLDER}═══《 🛍 𝐄𝐌𝐎𝐉𝐈 𝐒𝐓𝐎𝐑𝐄 》═══{PLACEHOLDER}

𝐘𝐎𝐔𝐑 𝐏𝐋𝐀𝐍: {get_plan_label(get_user_plan(uid))}

"""
    if not page_items:
        text += "𝐍𝐎 𝐈𝐓𝐄𝐌𝐒 𝐏𝐔𝐁𝐋𝐈𝐒𝐇𝐄𝐃 𝐘𝐄𝐓.\n\n"
    else:
        for item in page_items:
            required = item.get("plan_required", "free")
            unlocked = user_meets_plan(uid, required)
            status = "✅ 𝐔𝐍𝐋𝐎𝐂𝐊𝐄𝐃" if unlocked else f"🔒 𝐑𝐄𝐐𝐔𝐈𝐑𝐄𝐒 {get_plan_label(required).upper()}"
            text += f"𝐈𝐃: {item['id']}\n{item['name']}\n{item.get('description', '')}\n{status}\n\n"
    text += f"{PLACEHOLDER}═════════════════════{PLACEHOLDER}\n"

    keyboard = []
    nav_row = []
    if page > 0:
        nav_row.append(make_button_with_icon(text="◀ 𝐏𝐑𝐄𝐕", style="secondary", callback_data=f"storelist_{page-1}_{uid}"))
    if total_pages > 1:
        nav_row.append(make_button_with_icon(text=f"📄 {page+1}/{total_pages}", style="secondary", callback_data=f"storelist_{page}_{uid}"))
    if page < total_pages - 1:
        nav_row.append(make_button_with_icon(text="𝐍𝐄𝐗𝐓 ▶", style="secondary", callback_data=f"storelist_{page+1}_{uid}"))
    if nav_row:
        keyboard.append(nav_row)
    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    return text, markup

@bot.message_handler(func=lambda m: button_matches(m.text, "𝐄𝐌𝐎𝐉𝐈 𝐒𝐓𝐎𝐑𝐄"))
def store_msg(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    text, markup = build_store_page(uid, 0)
    _send_pe(message.chat.id, text, reply_markup=markup)

@bot.message_handler(commands=["store"])
def store_command(message):
    store_msg(message)

@bot.callback_query_handler(func=lambda c: c.data.startswith("storelist_"))
def handle_store_page(call):
    uid = call.from_user.id
    page = int(call.data.split("_")[1])
    text, markup = build_store_page(uid, page)
    entities = _build_pe_entities(text)
    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id,
                              entities=entities, reply_markup=markup, parse_mode=None)
    except Exception:
        pass
    bot.answer_callback_query(call.id)

# ============================================================
# PROMO CODES
# ============================================================
# Fully functional (unlike Store/Premium above) since redeeming a
# code involves no money — it's pure data. A code can grant a plan
# for N days and/or nothing beyond that (plan=None is valid — a code
# that exists purely for tracking/analytics without a reward is a
# legitimate use case the original spec's "discount/reward" language
# allows for).

def create_promo_code(code: str, plan: str, days: int, max_uses: int, per_user_uses: int, expires_days: int = None) -> bool:
    code = code.strip().upper()
    if not code or code in promo_codes:
        return False
    promo_codes[code] = {
        "code": code,
        "plan": plan if plan and plan != "none" else None,
        "days": days,
        "max_uses": max_uses,
        "per_user_uses": per_user_uses,
        "active": True,
        "created_at": int(time.time()),
        "expires_at": (int(time.time()) + expires_days * 86400) if expires_days else None,
        "redemptions": {},  # uid(str) -> use count
    }
    save_promo_codes()
    return True

def redeem_promo_code(uid: int, code: str) -> str:
    """Returns one of: 'redeemed', 'not_found', 'inactive', 'expired',
    'max_uses', 'user_limit'."""
    code = code.strip().upper()
    entry = promo_codes.get(code)
    if not entry:
        return "not_found"
    if not entry.get("active", True):
        return "inactive"
    if entry.get("expires_at") and int(time.time()) >= entry["expires_at"]:
        return "expired"
    total_uses = sum(entry.get("redemptions", {}).values())
    if entry.get("max_uses") and total_uses >= entry["max_uses"]:
        return "max_uses"
    key = str(uid)
    user_uses = entry.get("redemptions", {}).get(key, 0)
    if entry.get("per_user_uses") and user_uses >= entry["per_user_uses"]:
        return "user_limit"

    entry.setdefault("redemptions", {})[key] = user_uses + 1
    if entry.get("plan"):
        set_user_plan(uid, entry["plan"], entry.get("days"))
    save_promo_codes()
    return "redeemed"

@bot.message_handler(commands=["addpromo"])
def add_promo_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_PREMIUM):
        return
    parts = (message.text or "").split()
    # /addpromo CODE plan_or_none days max_uses per_user_uses [expires_days]
    if len(parts) < 6:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /addpromo CODE plan_or_none days max_uses per_user_uses [expires_days]\n𝐄𝐗: /addpromo WELCOME7 premium 7 100 1 30")
        return
    code = parts[1]
    plan = parts[2].lower()
    try:
        days = int(parts[3])
        max_uses = int(parts[4])
        per_user_uses = int(parts[5])
        expires_days = int(parts[6]) if len(parts) > 6 else None
    except ValueError:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐝𝐚𝐲𝐬/𝐦𝐚𝐱_𝐮𝐬𝐞𝐬/𝐩𝐞𝐫_𝐮𝐬𝐞𝐫_𝐮𝐬𝐞𝐬/𝐞𝐱𝐩𝐢𝐫𝐞𝐬_𝐝𝐚𝐲𝐬 𝐦𝐮𝐬𝐭 𝐛𝐞 𝐧𝐮𝐦𝐛𝐞𝐫𝐬.")
        return
    if plan != "none" and plan not in PLAN_RANK:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐩𝐥𝐚𝐧 𝐦𝐮𝐬𝐭 𝐛𝐞 none, free, premium, 𝐨𝐫 vip.")
        return
    ok = create_promo_code(code, plan, days, max_uses, per_user_uses, expires_days)
    if ok:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐏𝐫𝐨𝐦𝐨 𝐜𝐨𝐝𝐞 \"{code.upper()}\" 𝐜𝐫𝐞𝐚𝐭𝐞𝐝.")
    else:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐨𝐝𝐞 𝐚𝐥𝐫𝐞𝐚𝐝𝐲 𝐞𝐱𝐢𝐬𝐭𝐬 𝐨𝐫 𝐢𝐬 𝐢𝐧𝐯𝐚𝐥𝐢𝐝.")

@bot.message_handler(commands=["removepromo"])
def remove_promo_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_PREMIUM):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /removepromo CODE")
        return
    code = parts[1].strip().upper()
    if code in promo_codes:
        promo_codes.pop(code)
        save_promo_codes()
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐑𝐞𝐦𝐨𝐯𝐞𝐝 \"{code}\".")
    else:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐨𝐝𝐞 𝐧𝐨𝐭 𝐟𝐨𝐮𝐧𝐝.")

@bot.message_handler(commands=["redeem"])
def redeem_command(message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username)
    parts = (message.text or "").split()
    if len(parts) < 2:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /redeem CODE")
        return
    result = redeem_promo_code(uid, parts[1])
    messages_map = {
        "redeemed": f"{PLACEHOLDER} 𝐂𝐨𝐝𝐞 𝐫𝐞𝐝𝐞𝐞𝐦𝐞𝐝! 𝐂𝐡𝐞𝐜𝐤 👤 𝐌𝐘 𝐏𝐑𝐎𝐅𝐈𝐋𝐄 𝐟𝐨𝐫 𝐲𝐨𝐮𝐫 𝐮𝐩𝐝𝐚𝐭𝐞𝐝 𝐩𝐥𝐚𝐧.",
        "not_found": f"{PLACEHOLDER} 𝐓𝐡𝐚𝐭 𝐜𝐨𝐝𝐞 𝐝𝐨𝐞𝐬𝐧'𝐭 𝐞𝐱𝐢𝐬𝐭.",
        "inactive": f"{PLACEHOLDER} 𝐓𝐡𝐚𝐭 𝐜𝐨𝐝𝐞 𝐢𝐬 𝐧𝐨 𝐥𝐨𝐧𝐠𝐞𝐫 𝐚𝐜𝐭𝐢𝐯𝐞.",
        "expired": f"{PLACEHOLDER} 𝐓𝐡𝐚𝐭 𝐜𝐨𝐝𝐞 𝐡𝐚𝐬 𝐞𝐱𝐩𝐢𝐫𝐞𝐝.",
        "max_uses": f"{PLACEHOLDER} 𝐓𝐡𝐚𝐭 𝐜𝐨𝐝𝐞 𝐡𝐚𝐬 𝐫𝐞𝐚𝐜𝐡𝐞𝐝 𝐢𝐭𝐬 𝐮𝐬𝐚𝐠𝐞 𝐥𝐢𝐦𝐢𝐭.",
        "user_limit": f"{PLACEHOLDER} 𝐘𝐨𝐮'𝐯𝐞 𝐚𝐥𝐫𝐞𝐚𝐝𝐲 𝐮𝐬𝐞𝐝 𝐭𝐡𝐢𝐬 𝐜𝐨𝐝𝐞.",
    }
    _send_pe(message.chat.id, messages_map.get(result, f"{PLACEHOLDER} 𝐒𝐨𝐦𝐞𝐭𝐡𝐢𝐧𝐠 𝐰𝐞𝐧𝐭 𝐰𝐫𝐨𝐧𝐠."))


# ============================================================
# PHASE 6 — ADMIN DASHBOARD 2.0, USER MANAGEMENT, ADVANCED
# BROADCAST, MULTI-ADMIN/STAFF ROLES, AUDIT LOGS
# ============================================================
# New JSON files (kept separate from every other file — nothing below
# reads or writes any of the 11 files earlier phases use):
#   staff.json      - uid -> {role, granted_by, granted_at}
#   banned.json     - set of banned/restricted user IDs
#   audit_log.json  - append-only log of admin actions
#
# ------------------------------------------------------------
# ROLES & PERMISSIONS
# ------------------------------------------------------------
# Every ID in the original ADMIN_IDS list is auto-migrated to the
# OWNER role the first time this runs (only if staff.json doesn't
# exist yet — this never overwrites a staff.json that's already been
# customized). is_admin(uid) is extended, not replaced, to mean
# "holds ANY staff role" — this is what most of the 32 existing
# is_admin() call sites actually need (force-join bypass, bot-off
# bypass, "does the menu show admin buttons"). The ~15 call sites
# that needed a SPECIFIC permission (not just "any staff") were
# changed in this same pass to call has_permission(uid, PERM) instead
# — see PATCH.md for the full site-by-site mapping.
#
# OWNER implicitly has every permission and cannot be removed via
# /removestaff (only another edit to this file, or manually editing
# staff.json, can remove an OWNER — matches "only OWNER can manage
# administrators" from the spec, applied to OWNER's own safety too).
# ============================================================

STAFF_FILE = "staff.json"
BANNED_FILE = "banned.json"
AUDIT_LOG_FILE = "audit_log.json"

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_STAFF = "staff"
ROLE_MODERATOR = "moderator"
VALID_ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_STAFF, ROLE_MODERATOR)

PERM_MANAGE_USERS = "MANAGE_USERS"
PERM_MANAGE_EMOJIS = "MANAGE_EMOJIS"
PERM_MANAGE_PACKS = "MANAGE_PACKS"
PERM_BROADCAST = "BROADCAST"
PERM_MANAGE_PREMIUM = "MANAGE_PREMIUM"
PERM_MANAGE_TEMPLATES = "MANAGE_TEMPLATES"
PERM_VIEW_ANALYTICS = "VIEW_ANALYTICS"
PERM_BACKUP_DATABASE = "BACKUP_DATABASE"
PERM_BOT_CONTROL = "BOT_CONTROL"       # bot on/off — high impact, kept separate from MANAGE_USERS
PERM_MANAGE_STAFF = "MANAGE_STAFF"     # add/remove other staff — OWNER only, enforced in code too

ALL_PERMS = {
    PERM_MANAGE_USERS, PERM_MANAGE_EMOJIS, PERM_MANAGE_PACKS, PERM_BROADCAST,
    PERM_MANAGE_PREMIUM, PERM_MANAGE_TEMPLATES, PERM_VIEW_ANALYTICS,
    PERM_BACKUP_DATABASE, PERM_BOT_CONTROL, PERM_MANAGE_STAFF,
}

ROLE_PERMISSIONS = {
    ROLE_OWNER: ALL_PERMS,  # OWNER always has everything; see has_permission()
    ROLE_ADMIN: {
        PERM_MANAGE_USERS, PERM_MANAGE_EMOJIS, PERM_MANAGE_PACKS, PERM_BROADCAST,
        PERM_MANAGE_PREMIUM, PERM_MANAGE_TEMPLATES, PERM_VIEW_ANALYTICS,
        PERM_BACKUP_DATABASE, PERM_BOT_CONTROL,
        # NOT MANAGE_STAFF - only OWNER manages administrators
    },
    ROLE_STAFF: {
        PERM_MANAGE_EMOJIS, PERM_MANAGE_PACKS, PERM_MANAGE_TEMPLATES,
        PERM_VIEW_ANALYTICS,
        # NOT MANAGE_USERS, BROADCAST, MANAGE_PREMIUM, BOT_CONTROL,
        # BACKUP_DATABASE, MANAGE_STAFF - day-to-day content work only
    },
    ROLE_MODERATOR: {
        PERM_MANAGE_USERS,  # ban/unban/restrict, no bot-wide controls
        PERM_VIEW_ANALYTICS,
    },
}

ROLE_LABELS = {
    ROLE_OWNER: "👑 Owner", ROLE_ADMIN: "🛡 Admin",
    ROLE_STAFF: "⭐ Staff", ROLE_MODERATOR: "🔧 Moderator",
}

def _migrate_legacy_admin_ids_if_needed():
    if os.path.exists(STAFF_FILE):
        return load_json_file(STAFF_FILE, {})
    migrated = {}
    for legacy_uid in ADMIN_IDS:
        migrated[str(legacy_uid)] = {
            "role": ROLE_OWNER, "granted_by": None, "granted_at": int(time.time()),
        }
    save_json_file(STAFF_FILE, migrated)
    return migrated

staff: dict = _migrate_legacy_admin_ids_if_needed()
banned_users: set = set(load_json_file(BANNED_FILE, []))
audit_log: list = load_json_file(AUDIT_LOG_FILE, [])

def save_staff():
    save_json_file(STAFF_FILE, staff)

def save_banned():
    save_json_file(BANNED_FILE, list(banned_users))

MAX_AUDIT_LOG_ENTRIES = 2000  # oldest entries roll off past this, so the file doesn't grow forever

def save_audit_log():
    save_json_file(AUDIT_LOG_FILE, audit_log)

def log_action(admin_id: int, action: str, target: str = "", result: str = "success"):
    audit_log.append({
        "admin_id": admin_id, "action": action, "target": target,
        "result": result, "timestamp": int(time.time()),
    })
    if len(audit_log) > MAX_AUDIT_LOG_ENTRIES:
        del audit_log[:len(audit_log) - MAX_AUDIT_LOG_ENTRIES]
    save_audit_log()

def get_user_role(uid: int):
    """Returns a role string, or None if the user holds no staff role."""
    return staff.get(str(uid), {}).get("role")

def has_permission(uid: int, permission: str) -> bool:
    role = get_user_role(uid)
    if role is None:
        return False
    if role == ROLE_OWNER:
        return True  # OWNER always passes, regardless of ROLE_PERMISSIONS contents
    return permission in ROLE_PERMISSIONS.get(role, set())

def is_owner(uid: int) -> bool:
    return get_user_role(uid) == ROLE_OWNER

# ---- is_admin() extended to mean "holds any staff role" (not
#      replaced - the function name and its "any staff" meaning stay
#      intact for the ~17 call sites that need exactly that) ----
_original_is_admin = is_admin

def is_admin_with_roles(user_id: int) -> bool:
    if _original_is_admin(user_id):  # legacy ADMIN_IDS list, kept as a fallback safety net
        return True
    return get_user_role(user_id) is not None

globals()["is_admin"] = is_admin_with_roles

# ============================================================
# ADMIN: STAFF MANAGEMENT (OWNER only)
# ============================================================

@bot.message_handler(commands=["addstaff"])
def add_staff_command(message):
    uid = message.from_user.id
    if not is_owner(uid):
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /addstaff <user_id> <role>\n𝐫𝐨𝐥𝐞 𝐢𝐬 𝐨𝐧𝐞 𝐨𝐟: owner, admin, staff, moderator")
        return
    try:
        target_uid = int(parts[1])
    except ValueError:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐈𝐍𝐕𝐀𝐋𝐈𝐃 𝐔𝐒𝐄𝐑 𝐈𝐃.")
        return
    role = parts[2].lower()
    if role not in VALID_ROLES:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐫𝐨𝐥𝐞 𝐦𝐮𝐬𝐭 𝐛𝐞 𝐨𝐧𝐞 𝐨𝐟: owner, admin, staff, moderator")
        return
    staff[str(target_uid)] = {"role": role, "granted_by": uid, "granted_at": int(time.time())}
    save_staff()
    log_action(uid, "staff_added", f"{target_uid} -> {role}")
    _send_pe(message.chat.id, f"{PLACEHOLDER} {target_uid} 𝐢𝐬 𝐧𝐨𝐰 {ROLE_LABELS.get(role, role)}.")

@bot.message_handler(commands=["removestaff"])
def remove_staff_command(message):
    uid = message.from_user.id
    if not is_owner(uid):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /removestaff <user_id>")
        return
    try:
        target_uid = int(parts[1])
    except ValueError:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐈𝐍𝐕𝐀𝐋𝐈𝐃 𝐔𝐒𝐄𝐑 𝐈𝐃.")
        return
    key = str(target_uid)
    if key not in staff:
        _send_pe(message.chat.id, f"{PLACEHOLDER} {target_uid} 𝐡𝐨𝐥𝐝𝐬 𝐧𝐨 𝐬𝐭𝐚𝐟𝐟 𝐫𝐨𝐥𝐞.")
        return
    if staff[key].get("role") == ROLE_OWNER and target_uid == uid:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐘𝐨𝐮 𝐜𝐚𝐧'𝐭 𝐫𝐞𝐦𝐨𝐯𝐞 𝐲𝐨𝐮𝐫 𝐨𝐰𝐧 𝐎𝐰𝐧𝐞𝐫 𝐫𝐨𝐥𝐞. 𝐇𝐚𝐯𝐞 𝐚𝐧𝐨𝐭𝐡𝐞𝐫 𝐎𝐰𝐧𝐞𝐫 𝐝𝐨 𝐢𝐭, 𝐨𝐫 𝐞𝐝𝐢𝐭 staff.json 𝐝𝐢𝐫𝐞𝐜𝐭𝐥𝐲.")
        return
    old_role = staff[key].get("role")
    staff.pop(key)
    save_staff()
    log_action(uid, "staff_removed", f"{target_uid} (was {old_role})")
    _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐑𝐞𝐦𝐨𝐯𝐞𝐝 {target_uid}'𝐬 𝐬𝐭𝐚𝐟𝐟 𝐫𝐨𝐥𝐞.")

@bot.message_handler(commands=["staff"])
def list_staff_command(message):
    uid = message.from_user.id
    if not is_admin(uid):
        return
    text = f"""
{PLACEHOLDER}═══《 🛡 𝐒𝐓𝐀𝐅𝐅 》═══{PLACEHOLDER}

"""
    if not staff:
        text += "𝐍𝐎 𝐒𝐓𝐀𝐅𝐅 𝐑𝐄𝐂𝐎𝐑𝐃𝐒 (𝐮𝐬𝐢𝐧𝐠 𝐥𝐞𝐠𝐚𝐜𝐲 𝐀𝐃𝐌𝐈𝐍_𝐈𝐃𝐒 𝐨𝐧𝐥𝐲).\n"
    else:
        for member_uid, record in staff.items():
            role = record.get("role", "?")
            text += f"• {member_uid} — {ROLE_LABELS.get(role, role)}\n"
    text += f"\n{PLACEHOLDER}═════════════════════{PLACEHOLDER}\n"
    _send_pe(message.chat.id, text, reply_markup=get_menu(uid))


# ============================================================
# USER MANAGEMENT (ban / unban / restrict, search, view)
# ============================================================
# Ban enforcement is applied at the two natural entry chokepoints
# that already exist rather than a global per-message middleware:
# /start (welcome_with_referral, already wrapped once by Phase 4 for
# referrals — wrapped again here on top) and MAKE POST
# (start_post_with_chooser, already wrapped by Phase 3 for the
# drafts/templates chooser — wrapped again here too). A banned user
# hitting either gets a clear "you're banned" message instead of the
# normal flow. This covers the two most consequential actions
# (starting the bot, creating content) without touching the other
# ~40 handlers individually, which would be a much larger and
# harder-to-verify change for this pass.

def is_banned(uid: int) -> bool:
    return uid in banned_users

_original_welcome_with_referral = welcome_with_referral

def welcome_with_ban_check(message):
    uid = message.from_user.id
    if is_banned(uid):
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐘𝐨𝐮𝐫 𝐚𝐜𝐜𝐨𝐮𝐧𝐭 𝐡𝐚𝐬 𝐛𝐞𝐞𝐧 𝐫𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐟𝐫𝐨𝐦 𝐮𝐬𝐢𝐧𝐠 𝐭𝐡𝐢𝐬 𝐛𝐨𝐭.")
        return
    _original_welcome_with_referral(message)

for _h in bot.message_handlers:
    if _h.get("handler") is _original_welcome_with_referral:
        _h["handler"] = welcome_with_ban_check
        break

_original_start_post_with_chooser = start_post_with_chooser

def start_post_with_ban_check(message):
    uid = message.from_user.id
    if is_banned(uid):
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐘𝐨𝐮𝐫 𝐚𝐜𝐜𝐨𝐮𝐧𝐭 𝐡𝐚𝐬 𝐛𝐞𝐞𝐧 𝐫𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐟𝐫𝐨𝐦 𝐜𝐫𝐞𝐚𝐭𝐢𝐧𝐠 𝐩𝐨𝐬𝐭𝐬.")
        return
    _original_start_post_with_chooser(message)

for _h in bot.message_handlers:
    if _h.get("handler") is _original_start_post_with_chooser:
        _h["handler"] = start_post_with_ban_check
        break

def ban_user(admin_id: int, target_uid: int):
    banned_users.add(target_uid)
    save_banned()
    log_action(admin_id, "user_banned", str(target_uid))

def unban_user(admin_id: int, target_uid: int):
    banned_users.discard(target_uid)
    save_banned()
    log_action(admin_id, "user_unbanned", str(target_uid))

@bot.message_handler(commands=["ban"])
def ban_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_USERS):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /ban <user_id>")
        return
    try:
        target_uid = int(parts[1])
    except ValueError:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐈𝐍𝐕𝐀𝐋𝐈𝐃 𝐔𝐒𝐄𝐑 𝐈𝐃.")
        return
    if is_admin(target_uid):
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐂𝐚𝐧'𝐭 𝐛𝐚𝐧 𝐚 𝐬𝐭𝐚𝐟𝐟 𝐦𝐞𝐦𝐛𝐞𝐫. 𝐑𝐞𝐦𝐨𝐯𝐞 𝐭𝐡𝐞𝐢𝐫 𝐫𝐨𝐥𝐞 𝐟𝐢𝐫𝐬𝐭 𝐢𝐟 𝐭𝐡𝐚𝐭'𝐬 𝐢𝐧𝐭𝐞𝐧𝐝𝐞𝐝.")
        return
    ban_user(uid, target_uid)
    _send_pe(message.chat.id, f"{PLACEHOLDER} {target_uid} 𝐡𝐚𝐬 𝐛𝐞𝐞𝐧 𝐛𝐚𝐧𝐧𝐞𝐝.")

@bot.message_handler(commands=["unban"])
def unban_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_USERS):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /unban <user_id>")
        return
    try:
        target_uid = int(parts[1])
    except ValueError:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐈𝐍𝐕𝐀𝐋𝐈𝐃 𝐔𝐒𝐄𝐑 𝐈𝐃.")
        return
    unban_user(uid, target_uid)
    _send_pe(message.chat.id, f"{PLACEHOLDER} {target_uid} 𝐡𝐚𝐬 𝐛𝐞𝐞𝐧 𝐮𝐧𝐛𝐚𝐧𝐧𝐞𝐝.")

@bot.message_handler(commands=["finduser"])
def find_user_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_MANAGE_USERS):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /finduser <user_id 𝐨𝐫 @username>")
        return
    query = parts[1].strip()
    target_uid = None
    if query.startswith("@"):
        uname = query[1:].lower()
        for k, v in user_usernames.items():
            if v and v.lower() == uname:
                target_uid = int(k)
                break
    else:
        try:
            target_uid = int(query)
        except ValueError:
            pass
    if target_uid is None or target_uid not in all_users:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐬𝐞𝐫 𝐧𝐨𝐭 𝐟𝐨𝐮𝐧𝐝.")
        return
    text = build_profile_text(target_uid, user_usernames.get(str(target_uid)))
    ban_status = "🚫 𝐁𝐀𝐍𝐍𝐄𝐃" if is_banned(target_uid) else "✅ 𝐀𝐜𝐭𝐢𝐯𝐞"
    role = get_user_role(target_uid)
    role_line = f"\n𝐒𝐓𝐀𝐅𝐅 𝐑𝐎𝐋𝐄: {ROLE_LABELS.get(role, 'None')}" if role else ""
    text += f"\n𝐒𝐓𝐀𝐓𝐔𝐒: {ban_status}{role_line}\n"
    _send_pe(message.chat.id, text, reply_markup=get_menu(uid))

# ============================================================
# ADVANCED BROADCAST — segmentation, progress, cancellation
# ============================================================
# Extends (does not replace) the existing broadcast_start/do_broadcast
# flow: adds a segment picker before the message prompt, and swaps
# the plain send-loop for one that reports progress, supports a live
# cancel button, and stays flood-safe (unchanged 0.05s delay between
# sends from the original code, kept as-is since it already handled
# that concern).

BROADCAST_SEGMENTS = {
    "all": "📣 𝐀𝐥𝐥 𝐔𝐬𝐞𝐫𝐬",
    "premium": "💎 𝐏𝐫𝐞𝐦𝐢𝐮𝐦 𝐔𝐬𝐞𝐫𝐬",
    "vip": "👑 𝐕𝐈𝐏 𝐔𝐬𝐞𝐫𝐬",
    "new": "🆕 𝐍𝐞𝐰 𝐔𝐬𝐞𝐫𝐬 (𝐥𝐚𝐬𝐭 𝟕 𝐝𝐚𝐲𝐬)",
    "active": "🟢 𝐀𝐜𝐭𝐢𝐯𝐞 𝐔𝐬𝐞𝐫𝐬 (𝐩𝐨𝐬𝐭𝐞𝐝 𝐥𝐚𝐬𝐭 𝟑𝟎 𝐝𝐚𝐲𝐬)",
    "inactive": "😴 𝐈𝐧𝐚𝐜𝐭𝐢𝐯𝐞 𝐔𝐬𝐞𝐫𝐬 (𝐧𝐨 𝐩𝐨𝐬𝐭𝐬 𝐲𝐞𝐭)",
}

def get_segment_user_ids(segment: str) -> list:
    now = int(time.time())
    week_ago = now - 7 * 86400

    if segment == "all":
        return list(all_users)
    if segment == "premium":
        return [int(k) for k, v in user_stats.items() if get_user_plan(int(k)) == "premium"]
    if segment == "vip":
        return [int(k) for k, v in user_stats.items() if get_user_plan(int(k)) == "vip"]
    if segment == "new":
        return [int(k) for k, v in user_stats.items() if v.get("join_date", 0) >= week_ago]
    if segment == "active":
        return [int(k) for k, v in user_stats.items() if v.get("posts_created", 0) > 0]
    if segment == "inactive":
        return [int(k) for k, v in user_stats.items() if v.get("posts_created", 0) == 0]
    return []

broadcast_cancel_flags = {}  # admin_uid -> True means "cancel requested"

def broadcast_start_with_segments(message):
    uid = message.from_user.id
    keyboard = []
    for seg_id, label in BROADCAST_SEGMENTS.items():
        keyboard.append([make_button(text=label, callback_data=f"bcastseg_{seg_id}_{uid}")])
    text = f"""
{PLACEHOLDER}═══《 📢 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓 》═══{PLACEHOLDER}

𝐂𝐇𝐎𝐎𝐒𝐄 𝐖𝐇𝐎 𝐑𝐄𝐂𝐄𝐈𝐕𝐄𝐒 𝐓𝐇𝐈𝐒 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓:

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    _send_pe(message.chat.id, text, reply_markup=InlineKeyboardMarkup(keyboard))

# NOTE: this function is deliberately NOT decorated with
# @bot.message_handler — it replaces the original broadcast_start
# (message-text-prompt version) by swapping into ITS existing
# registration entry below, the same technique Phase 3/4/5 use
# throughout. Decorating this function directly as well would create
# a second, redundant registration alongside the swapped-in one for
# the same button and predicate — harmless in effect (both would fire
# identically) but sloppy and confusing, so the swap is the only
# registration path here.
for _h in bot.message_handlers:
    if _h.get("handler").__name__ == "broadcast_start" and _h is not None:
        _h["handler"] = broadcast_start_with_segments
        break

@bot.callback_query_handler(func=lambda c: c.data.startswith("bcastseg_"))
def handle_broadcast_segment_pick(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    if not has_permission(uid, PERM_BROADCAST):
        bot.answer_callback_query(call.id)
        return
    try:
        _, segment, _uid_str = call.data.split("_", 2)
    except Exception:
        bot.answer_callback_query(call.id)
        return
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    recipient_count = len(get_segment_user_ids(segment))
    temp_data.setdefault(uid, {})["_broadcast_segment"] = segment
    sent = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐒𝐄𝐍𝐃𝐈𝐍𝐆 𝐓𝐎: {BROADCAST_SEGMENTS.get(segment, segment)} ({recipient_count} 𝐮𝐬𝐞𝐫𝐬)\n\n𝐍𝐨𝐰 𝐬𝐞𝐧𝐝 𝐭𝐡𝐞 𝐦𝐞𝐬𝐬𝐚𝐠𝐞 𝐭𝐨 𝐛𝐫𝐨𝐚𝐝𝐜𝐚𝐬𝐭, 𝐨𝐫 /𝐜𝐚𝐧𝐜𝐞𝐥.")
    bot.register_next_step_handler(sent, receive_broadcast_message_with_segment)
    bot.answer_callback_query(call.id)

def receive_broadcast_message_with_segment(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    if message.text and message.text.strip() == "/cancel":
        _send_pe(chat_id, f"{PLACEHOLDER} 𝐂𝐀𝐍𝐂𝐄𝐋𝐄𝐃!", reply_markup=get_menu(uid))
        return
    segment = temp_data.get(uid, {}).pop("_broadcast_segment", "all")
    recipient_ids = get_segment_user_ids(segment)

    broadcast_cancel_flags[uid] = False
    cancel_markup = InlineKeyboardMarkup([[make_button_with_icon(text="🛑 𝐂𝐀𝐍𝐂𝐄𝐋", style="danger", callback_data=f"bcastcancel_{uid}")]])
    progress_msg = _send_pe_return(chat_id, f"{PLACEHOLDER} 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓𝐈𝐍𝐆... 0/{len(recipient_ids)}")
    try:
        bot.edit_message_text(f"{PLACEHOLDER} 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓𝐈𝐍𝐆... 0/{len(recipient_ids)}",
                              chat_id=chat_id, message_id=progress_msg.message_id, reply_markup=cancel_markup)
    except Exception:
        pass

    success, failed = 0, 0
    for i, target_uid in enumerate(recipient_ids):
        if broadcast_cancel_flags.get(uid):
            break
        try:
            bot.send_message(target_uid, message.text or "")
            success += 1
        except Exception:
            failed += 1
        if (i + 1) % 20 == 0:
            try:
                bot.edit_message_text(f"{PLACEHOLDER} 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓𝐈𝐍𝐆... {i+1}/{len(recipient_ids)}",
                                      chat_id=chat_id, message_id=progress_msg.message_id, reply_markup=cancel_markup)
            except Exception:
                pass
        time.sleep(0.05)  # same flood-safe delay the original broadcast used

    cancelled = broadcast_cancel_flags.pop(uid, False)
    status_line = "🛑 𝐂𝐀𝐍𝐂𝐄𝐋𝐋𝐄𝐃 𝐌𝐈𝐃-𝐖𝐀𝐘" if cancelled else "✅ 𝐂𝐎𝐌𝐏𝐋𝐄𝐓𝐄"
    final_text = f"{PLACEHOLDER} 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓 {status_line}\n\n✅ 𝐒𝐞𝐧𝐭: {success}\n❌ 𝐅𝐚𝐢𝐥𝐞𝐝: {failed}"
    try:
        bot.edit_message_text(final_text, chat_id=chat_id, message_id=progress_msg.message_id)
    except Exception:
        pass
    log_action(uid, "broadcast_sent", f"segment={segment} sent={success} failed={failed} cancelled={cancelled}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("bcastcancel_"))
def handle_broadcast_cancel(call):
    uid = call.from_user.id
    broadcast_cancel_flags[uid] = True
    bot.answer_callback_query(call.id, "Cancelling after the current send...")

# ============================================================
# ADMIN DASHBOARD 2.0 — single entry point summarizing the sections
# ============================================================
# Doesn't replace the existing individual buttons (Users List,
# Broadcast, Manage Categories, etc. all still work exactly as
# before) - this is an additional at-a-glance summary + shortcut
# screen, reachable via /admin, matching the spec's dashboard concept
# without restructuring how any existing feature is triggered.

@bot.message_handler(commands=["admin"])
def admin_dashboard_command(message):
    uid = message.from_user.id
    if not is_admin(uid):
        return
    role = get_user_role(uid) or "legacy admin"
    total_users = len(all_users)
    total_staff = len(staff)
    total_banned = len(banned_users)
    total_store_items = len(store_items)
    total_promo_codes = len(promo_codes)
    text = f"""
{PLACEHOLDER}═══《 👑 𝐀𝐃𝐌𝐈𝐍 𝐏𝐀𝐍𝐄𝐋 》═══{PLACEHOLDER}

𝐘𝐎𝐔𝐑 𝐑𝐎𝐋𝐄: {ROLE_LABELS.get(role, role)}
𝐁𝐎𝐓 𝐒𝐓𝐀𝐓𝐔𝐒: {'🟢 𝐎𝐍𝐋𝐈𝐍𝐄' if bot_active else '🔴 𝐎𝐅𝐅𝐋𝐈𝐍𝐄'}

📊 𝐐𝐔𝐈𝐂𝐊 𝐍𝐔𝐌𝐁𝐄𝐑𝐒
• 𝐔𝐬𝐞𝐫𝐬: {total_users}
• 𝐒𝐭𝐚𝐟𝐟: {total_staff}
• 𝐁𝐚𝐧𝐧𝐞𝐝: {total_banned}
• 𝐒𝐭𝐨𝐫𝐞 𝐢𝐭𝐞𝐦𝐬: {total_store_items}
• 𝐏𝐫𝐨𝐦𝐨 𝐜𝐨𝐝𝐞𝐬: {total_promo_codes}

👥 𝐔𝐒𝐄𝐑𝐒 — /finduser, /ban, /unban
😀 𝐄𝐌𝐎𝐉𝐈𝐒 — 🗂 𝐌𝐚𝐧𝐚𝐠𝐞 𝐂𝐚𝐭𝐞𝐠𝐨𝐫𝐢𝐞𝐬, /nameemoji
📢 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓 — 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓 𝐛𝐮𝐭𝐭𝐨𝐧
💎 𝐏𝐑𝐄𝐌𝐈𝐔𝐌 — /grantpremium, /grantvip, /revokeplan
🛍 𝐒𝐓𝐎𝐑𝐄 — /addstoreitem, /removestoreitem
🎟 𝐏𝐑𝐎𝐌𝐎 — /addpromo, /removepromo
🛡 𝐒𝐓𝐀𝐅𝐅 — /addstaff, /removestaff, /staff
📜 𝐋𝐎𝐆𝐒 — /adminlogs

{PLACEHOLDER}═════════════════════{PLACEHOLDER}
"""
    _send_pe(message.chat.id, text, reply_markup=get_menu(uid))

# ============================================================
# AUDIT LOG VIEWER
# ============================================================

@bot.message_handler(commands=["adminlogs"])
def admin_logs_command(message):
    uid = message.from_user.id
    if not is_admin(uid):
        return
    recent = audit_log[-15:][::-1]
    text = f"""
{PLACEHOLDER}═══《 📜 𝐑𝐄𝐂𝐄𝐍𝐓 𝐀𝐃𝐌𝐈𝐍 𝐀𝐂𝐓𝐈𝐎𝐍𝐒 》═══{PLACEHOLDER}

"""
    if not recent:
        text += "𝐍𝐎 𝐀𝐂𝐓𝐈𝐎𝐍𝐒 𝐋𝐎𝐆𝐆𝐄𝐃 𝐘𝐄𝐓.\n"
    else:
        for entry in recent:
            dt = datetime.fromtimestamp(entry["timestamp"]).strftime("%d %b %H:%M")
            target_note = f" → {entry['target']}" if entry.get("target") else ""
            text += f"• [{dt}] {entry['admin_id']}: {entry['action']}{target_note}\n"
    text += f"\n{PLACEHOLDER}═════════════════════{PLACEHOLDER}\n"
    _send_pe(message.chat.id, text, reply_markup=get_menu(uid))


# ============================================================
# PHASE 7 (PARTIAL) — BACKUP/RESTORE, SECURITY HARDENING,
# PERFORMANCE NOTES
# ============================================================
# SCOPE NOTE: the full JSON -> SQLite migration (Section 21 of the
# original spec) is deliberately NOT part of this pass. Rewriting how
# all 15 JSON files used across 6 phases of working, tested code are
# read and written is the single highest-risk change in this whole
# project — a subtle bug there could silently corrupt or lose data
# across every feature built so far. Building Backup/Restore FIRST,
# in this pass, means the eventual SQLite migration gets a real,
# working safety net to fall back on, rather than being the first
# thing that touches this data with nothing to recover from if
# something goes wrong. The migration itself is the natural next
# phase.
#
# New admin commands:
#   /backup           - create a timestamped backup of all 15 JSON
#                        files (zipped) into backups/
#   /listbackups       - list available backups
#   /restore <name>    - restore from a backup (creates a safety
#                        backup of current state FIRST, always)
#   /deletebackup <name>
# Permission: BACKUP_DATABASE (Owner/Admin only, per Phase 6's role
# table — Staff/Moderator do not have this permission)
# ============================================================

import shutil
import zipfile

BACKUP_DIR = "backups"

# Every JSON file across every phase, in one place — this list is
# also what the eventual SQLite migration will read FROM, so a
# correct, complete list here matters for both features.
ALL_DATA_FILES = [
    user_db_file, usernames_db_file, emoji_packs_file, custom_packs_file,
    EMOJI_NAMES_FILE, CATEGORIES_FILE, FAVORITES_FILE,
    DRAFTS_FILE, TEMPLATES_FILE,
    USER_STATS_FILE,
    PLANS_FILE, STORE_ITEMS_FILE, PROMO_CODES_FILE,
    STAFF_FILE, BANNED_FILE, AUDIT_LOG_FILE,
]

def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)

def create_backup(label: str = "") -> str:
    """Zips every existing data file into backups/<timestamp>[_label].zip.
    Missing files (e.g. a fresh install that never wrote favorites.json
    because nobody has favorited anything yet) are skipped, not errors —
    that's expected, not a fault."""
    _ensure_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]", "", label)[:30]
    filename = f"{timestamp}_{safe_label}.zip" if safe_label else f"{timestamp}.zip"
    path = os.path.join(BACKUP_DIR, filename)

    included = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in ALL_DATA_FILES:
            if os.path.exists(fname):
                zf.write(fname, arcname=fname)
                included.append(fname)
    return filename, included

def list_backups() -> list:
    _ensure_backup_dir()
    entries = []
    for fname in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if fname.endswith(".zip"):
            full = os.path.join(BACKUP_DIR, fname)
            size_kb = os.path.getsize(full) / 1024
            entries.append((fname, size_kb))
    return entries

def restore_backup(filename: str) -> tuple:
    """Returns (ok: bool, message: str). ALWAYS creates a safety
    backup of the current state before overwriting anything, so a
    restore is itself always reversible."""
    _ensure_backup_dir()
    backup_path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(backup_path):
        return False, "That backup file doesn't exist."

    # Safety backup of current state, unconditionally, before touching anything
    safety_name, _ = create_backup(label="pre_restore")

    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            names = zf.namelist()
            # Only ever extract the exact known data files, by exact
            # name match against ALL_DATA_FILES — never trust the
            # zip's internal paths directly, which guards against a
            # maliciously crafted zip trying to write outside the
            # working directory (path traversal via "../../etc/...").
            restored = []
            for fname in ALL_DATA_FILES:
                if fname in names:
                    with zf.open(fname) as src, open(fname, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    restored.append(fname)
    except Exception as e:
        logger.error(f"Restore failed for {filename}: {e}")
        return False, f"Restore failed (a safety backup of your pre-restore state was still saved as {safety_name})."

    return True, f"Restored {len(restored)} file(s). Your pre-restore state was saved as {safety_name}."

def delete_backup(filename: str) -> bool:
    path = os.path.join(BACKUP_DIR, filename)
    # Exact-match against list_backups() output only — never accept a
    # raw path from user input, which is what makes this safe against
    # someone passing "../something" as the filename.
    valid_names = {f for f, _ in list_backups()}
    if filename not in valid_names:
        return False
    os.remove(path)
    return True

@bot.message_handler(commands=["backup"])
def backup_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_BACKUP_DATABASE):
        return
    filename, included = create_backup()
    log_action(uid, "backup_created", filename)
    _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐁𝐚𝐜𝐤𝐮𝐩 𝐜𝐫𝐞𝐚𝐭𝐞𝐝: {filename}\n𝐈𝐧𝐜𝐥𝐮𝐝𝐞𝐝 {len(included)} 𝐟𝐢𝐥𝐞(𝐬).", reply_markup=get_menu(uid))

@bot.message_handler(commands=["listbackups"])
def list_backups_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_BACKUP_DATABASE):
        return
    backups = list_backups()
    text = f"""
{PLACEHOLDER}═══《 💾 𝐁𝐀𝐂𝐊𝐔𝐏𝐒 》═══{PLACEHOLDER}

"""
    if not backups:
        text += "𝐍𝐎 𝐁𝐀𝐂𝐊𝐔𝐏𝐒 𝐘𝐄𝐓. 𝐔𝐒𝐄 /backup 𝐭𝐨 𝐜𝐫𝐞𝐚𝐭𝐞 𝐨𝐧𝐞.\n"
    else:
        for fname, size_kb in backups[:20]:
            text += f"• {fname} ({size_kb:.1f} KB)\n"
        if len(backups) > 20:
            text += f"\n…𝐚𝐧𝐝 {len(backups) - 20} 𝐦𝐨𝐫𝐞.\n"
    text += f"\n{PLACEHOLDER}═════════════════════{PLACEHOLDER}\n"
    _send_pe(message.chat.id, text, reply_markup=get_menu(uid))

@bot.message_handler(commands=["restore"])
def restore_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_BACKUP_DATABASE):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /restore <backup_filename>\n𝐒𝐞𝐞 /listbackups 𝐟𝐨𝐫 𝐧𝐚𝐦𝐞𝐬.")
        return
    filename = parts[1].strip()
    keyboard = InlineKeyboardMarkup([[
        make_button_with_icon(text="⚠️ 𝐂𝐎𝐍𝐅𝐈𝐑𝐌 𝐑𝐄𝐒𝐓𝐎𝐑𝐄", style="danger", callback_data=f"restoreconfirm_{filename}_{uid}"),
        make_button_with_icon(text="❌ 𝐂𝐀𝐍𝐂𝐄𝐋", style="secondary", callback_data=f"restorecancel_{uid}"),
    ]])
    _send_pe(message.chat.id, f"{PLACEHOLDER} ⚠️ 𝐀𝐫𝐞 𝐲𝐨𝐮 𝐬𝐮𝐫𝐞? 𝐓𝐡𝐢𝐬 𝐰𝐢𝐥𝐥 𝐨𝐯𝐞𝐫𝐰𝐫𝐢𝐭𝐞 𝐜𝐮𝐫𝐫𝐞𝐧𝐭 𝐝𝐚𝐭𝐚 𝐰𝐢𝐭𝐡 \"{filename}\".\n(𝐀 𝐬𝐚𝐟𝐞𝐭𝐲 𝐛𝐚𝐜𝐤𝐮𝐩 𝐨𝐟 𝐜𝐮𝐫𝐫𝐞𝐧𝐭 𝐝𝐚𝐭𝐚 𝐰𝐢𝐥𝐥 𝐛𝐞 𝐦𝐚𝐝𝐞 𝐟𝐢𝐫𝐬𝐭, 𝐫𝐞𝐠𝐚𝐫𝐝𝐥𝐞𝐬𝐬.)",
              reply_markup=keyboard)

@bot.callback_query_handler(func=lambda c: c.data.startswith("restoreconfirm_"))
def handle_restore_confirm(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    if not has_permission(uid, PERM_BACKUP_DATABASE):
        bot.answer_callback_query(call.id)
        return
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    # callback_data shape: restoreconfirm_<filename>_<uid> — filename
    # itself may contain underscores, so split from the right using
    # the known uid suffix rather than a fixed split count.
    payload = call.data[len("restoreconfirm_"):]
    filename = payload.rsplit("_", 1)[0]
    ok, msg = restore_backup(filename)
    log_action(uid, "restore_attempted", f"{filename} -> {'success' if ok else 'failed'}")
    status = "✅" if ok else "❌"
    _send_pe(chat_id, f"{PLACEHOLDER} {status} {msg}", reply_markup=get_menu(uid))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("restorecancel_"))
def handle_restore_cancel(call):
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    _send_pe(chat_id, f"{PLACEHOLDER} 𝐑𝐞𝐬𝐭𝐨𝐫𝐞 𝐜𝐚𝐧𝐜𝐞𝐥𝐞𝐝.", reply_markup=get_menu(call.from_user.id))
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=["deletebackup"])
def delete_backup_command(message):
    uid = message.from_user.id
    if not has_permission(uid, PERM_BACKUP_DATABASE):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐔𝐒𝐀𝐆𝐄: /deletebackup <backup_filename>")
        return
    filename = parts[1].strip()
    ok = delete_backup(filename)
    if ok:
        log_action(uid, "backup_deleted", filename)
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐃𝐞𝐥𝐞𝐭𝐞𝐝 {filename}.", reply_markup=get_menu(uid))
    else:
        _send_pe(message.chat.id, f"{PLACEHOLDER} 𝐁𝐚𝐜𝐤𝐮𝐩 𝐧𝐨𝐭 𝐟𝐨𝐮𝐧𝐝.", reply_markup=get_menu(uid))


if __name__ == "__main__":
    logger.info("IMAX Premium Emoji bot starting")
    logger.info(f"Admins: {ADMIN_IDS}")
    logger.info(f"Primary Emojis: {len(PRIMARY_EMOJIS)}")
    logger.info(f"Total Premium Emojis: {len(ALL_PREMIUM_EMOJIS)}")
    logger.info(f"Country Flags: {len(FLAG_MAPPING)}")

    bot.remove_webhook()
    time.sleep(1)

    while True:
        try:
            logger.info("Starting polling...")
            bot.infinity_polling(timeout=30, long_polling_timeout=15)
        except Exception as e:
            logger.error(f"Polling crashed: {e}. Restarting in 5s...")
            # Notify staff with BACKUP_DATABASE permission (the closest
            # proxy for "senior enough to care about an infra-level
            # crash" among existing permissions) so a full poll-loop
            # crash doesn't go unnoticed until someone happens to check
            # logs. Best-effort only — if Telegram itself is what's
            # down, these sends will also fail, which is fine; the
            # restart loop below doesn't depend on them succeeding.
            for _staff_uid, _record in staff.items():
                if PERM_BACKUP_DATABASE in ROLE_PERMISSIONS.get(_record.get("role"), set()) or _record.get("role") == ROLE_OWNER:
                    try:
                        bot.send_message(int(_staff_uid), f"⚠️ Bot crashed and is restarting: {type(e).__name__}. Check server logs for details.")
                    except Exception:
                        pass
            time.sleep(5)
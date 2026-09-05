from flask import Flask, request, jsonify, make_response
import os
import threading
import sys
import json
import time

from datetime import datetime
from zoneinfo import ZoneInfo
import queue

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

import requests
import random

import traceback

try:
    import undetected_chromedriver as uc
    from selenium.common.exceptions import WebDriverException
except Exception:
    uc = None
    WebDriverException = Exception

try:
    from seleniumwire import webdriver as sw_webdriver
    SELENIUM_WIRE_AVAILABLE = True
except Exception:
    sw_webdriver = None
    SELENIUM_WIRE_AVAILABLE = False

# Optional SocketIO
try:
    from flask_socketio import SocketIO
except Exception:
    SocketIO = None


from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
# Optional Atlas fallback used only when the local Mongo connection fails.
MONGO_URI_ATLAS = os.getenv("MONGO_URI_ATLAS", "")
DB_NAME = os.getenv("MONGO_DB_NAME", "spotify_friends")
COLLECTION_NAME = os.getenv("MONGO_COLLECTION", "friends_activity")

FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

# default interval (seconds) if config missing
ACTIVITY_INTERVAL = int(os.getenv("ACTIVITY_INTERVAL", "60"))

# Spotify internal endpoint used to read friend activity
SPOTIFY_BUDDYLIST_URL = "https://guc-spclient.spotify.com/presence-view/v1/buddylist"


# ================= TRAY / UI =================
import pystray
from pystray import MenuItem as item
from PIL import Image
import threading as th
import subprocess
import shutil
import platform
try:
    import winreg
except Exception:
    winreg = None
import subprocess

# ================= NOTIFICATIONS =================
# Disable system toasts on Windows to avoid spawning notification subprocesses
toaster = None


# ================= PATH HELPERS & DATA FILES =================

def exe_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(".")


def resource_path(rel):
    try:
        base = sys._MEIPASS
    except Exception:
        base = exe_dir()
    return os.path.join(base, rel)


BASE_DIR = exe_dir()
ICON_PATH = resource_path("icon.ico")
IST = ZoneInfo("Asia/Kolkata")

DOCUMENTS_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Spotify Friend Tracker")
CONFIG_PATH = os.path.join(DOCUMENTS_DIR, "config.json")
COOKIES_PATH = os.path.join(DOCUMENTS_DIR, "cook.json")
RAW_FRIENDS_PATH = os.path.join(DOCUMENTS_DIR, "spotify_friends.json")
LOGS_PATH = os.path.join(DOCUMENTS_DIR, "logs.txt")

DEFAULT_CONFIG = {
    "sp_dc": "",
    "activity_interval": 60,
    "headless": False,
    "chrome_profile": "",
    "auth_token": "",
    "client_token": "",
    "auth_captured_at": 0,
    "client_captured_at": 0,
    "debug_tokens": False,
    "last_login_check": 0,
    "last_fetch": 0,
    "last_successful_activity": 0,
    "spotify_logged_in": True
}

# Token timing assumptions
AUTH_TTL = 45 * 60        # 45 minutes
CLIENT_TTL = 7 * 24 * 3600 # 7 days


# ================= MONGODB =================
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')
    db = mongo_client[DB_NAME]
    friends_collection = db[COLLECTION_NAME]
    print("✅ MongoDB connection successful [Local]")
except ConnectionFailure:
    if not MONGO_URI_ATLAS:
        print("⚠️  MongoDB connection failed. Set MONGO_URI_ATLAS to enable the Atlas fallback.")
        mongo_client = None
        db = None
        friends_collection = None
    else:
        try:
            mongo_client = MongoClient(MONGO_URI_ATLAS, serverSelectionTimeoutMS=10000)
            mongo_client.admin.command('ping')
            db = mongo_client[DB_NAME]
            friends_collection = db[COLLECTION_NAME]
            print("✅ MongoDB connection successful [MongoDB Atlas]")
        except Exception as e:
            print("ATLAS ERROR:", e)
            mongo_client = None
            db = None
            friends_collection = None

# Ensure a unique index on user_uri (sparse to remain compatible with older docs)
try:
    if friends_collection is not None:
        try:
            friends_collection.create_index("user_uri", unique=True, sparse=True)
            print('[Spotify] Created unique index on user_uri')
        except Exception as e:
            print('[Spotify] ⚠️ user_uri index creation warning:', e)
except Exception:
    pass


# ================= FLASK / SOCKET =================
app = Flask(__name__)
LOCK = threading.Lock()

socketio = None
if SocketIO:
    try:
        socketio = SocketIO(app, cors_allowed_origins='*')
        print('[Spotify] SocketIO enabled')
    except Exception:
        socketio = None


# ================= LOGGING =================
LOG_BUFFER = []


class LogWriter:
    def write(self, msg):
        if not msg:
            return
        if isinstance(msg, bytes):
            try:
                msg = msg.decode("utf-8", errors="replace")
            except Exception:
                msg = str(msg)
        msg = str(msg)
        if msg.strip():
            LOG_BUFFER.append(msg)
            if len(LOG_BUFFER) > 500:
                LOG_BUFFER.pop(0)
            try:
                os.makedirs(DOCUMENTS_DIR, exist_ok=True)
                with open(LOGS_PATH, "a", encoding="utf-8") as f:
                    f.write(msg + "\n")
            except Exception:
                pass

    def flush(self):
        pass



def ensure_data_files():
    try:
        os.makedirs(DOCUMENTS_DIR, exist_ok=True)
        if not os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
        if not os.path.exists(COOKIES_PATH):
            with open(COOKIES_PATH, "w", encoding="utf-8") as f:
                json.dump([], f)
        if not os.path.exists(RAW_FRIENDS_PATH):
            with open(RAW_FRIENDS_PATH, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=2, ensure_ascii=False)
        if not os.path.exists(LOGS_PATH):
            open(LOGS_PATH, "a", encoding="utf-8").close()
    except Exception as e:
        print("⚠️ ensure_data_files error:", e)


ensure_data_files()

# Reset logs at startup and add a startup timestamp
try:
    with open(LOGS_PATH, "w", encoding="utf-8") as f:
        f.write(f"===== Spotify Friend Tracker Started ===== {datetime.now(IST).isoformat()}\n")
except Exception:
    pass

# Redirect stdout/stderr to LogWriter after ensuring files exist
sys.stdout = LogWriter()
sys.stderr = LogWriter()


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = DEFAULT_CONFIG.copy()
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print("⚠️ save_config error:", e)


def sanitize_cookie(c):
    # Keep only supported fields and normalize sameSite
    if not isinstance(c, dict):
        return None
    out = {}
    try:
        out['name'] = c.get('name')
        out['value'] = c.get('value')
        out['domain'] = c.get('domain') or c.get('host') or ".spotify.com"
        out['path'] = c.get('path', '/')
        if 'expires' in c and isinstance(c.get('expires'), (int, float)):
            out['expiry'] = int(c.get('expires'))
        out['httpOnly'] = bool(c.get('httpOnly', False))
        out['secure'] = bool(c.get('secure', False))
        ss = c.get('sameSite') or ''
        if isinstance(ss, str):
            ssu = ss.capitalize()
            if ssu in ('Strict', 'Lax', 'None'):
                out['sameSite'] = ssu
    except Exception:
        return None
    if not out.get('name') or out.get('value') is None:
        return None
    return out


def load_cookies():
    try:
        with open(COOKIES_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
            cookies = []
            for c in raw:
                s = sanitize_cookie(c)
                if s:
                    cookies.append(s)
            return cookies
    except Exception:
        return []


def save_cookies(cookies):
    try:
        cleaned = []
        for c in cookies:
            s = sanitize_cookie(c)
            if s:
                cleaned.append(s)
        with open(COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2)
    except Exception as e:
        print("⚠️ save_cookies error:", e)


# In-memory caches
LAST_TRACK_CACHE = {}
RECENT_ACTIVITY_CACHE = {}


def save_raw_friends(data):
    try:
        # Accept either dict (JSON) or raw text string
        if isinstance(data, str):
            with open(RAW_FRIENDS_PATH, 'w', encoding='utf-8') as f:
                f.write(data)
        else:
            with open(RAW_FRIENDS_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print('⚠️ save_raw_friends error:', e)


# ================= CORS =================

def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# ================= NOTIFY =================

def notify(friend, track, artist):
    msg = (f"{friend} started listening to\n" f"{track} — {artist}")
    try:
        NOTIFICATION_QUEUE.put(("🎧 Spotify Friend Tracker", msg))
    except Exception:
        # If queueing fails, log the notification; do not call toaster from worker threads
        try:
            print('[Spotify] Notification (queued failed):', msg)
        except Exception:
            pass


# ================= PROCESSING =================

def process_friends_list(friends):
    """Process a list of friend entries (internal)."""
    if friends_collection is None:
        return
    now_ts = time.time()
    count = 0
    with LOCK:
        for friend in friends:
            try:
                timestamp = friend.get('timestamp')
                user = friend.get('user', {})
                track = friend.get('track', {})
                artist = track.get('artist', {})
                album = track.get('album', {})
                context = track.get('context', {})
                name = user.get('name')
                user_uri = user.get('uri')
                user_image = user.get('imageUrl') or user.get('image')
                track_name = track.get('name')
                track_uri = track.get('uri')
                track_image = track.get('imageUrl') or track.get('image')
                artist_name = artist.get('name')
                artist_uri = artist.get('uri')
                album_name = album.get('name')
                album_uri = album.get('uri')
                context_name = context.get('name')
                context_uri = context.get('uri')

                # use user_uri as the primary identity key; fall back to name if missing
                id_key = user_uri or name

                # duplicate prevention using in-memory cache and recent activity cache
                last = LAST_TRACK_CACHE.get(id_key)
                try:
                    t_ts = float(timestamp) if timestamp else now_ts
                except Exception:
                    t_ts = now_ts

                # skip if same user+track+timestamp existed recently
                recent_key = f"{id_key}:{track_uri}:{timestamp}"
                if RECENT_ACTIVITY_CACHE.get(recent_key):
                    continue

                if last and last.get('track_uri') == track_uri and (t_ts - last.get('timestamp', 0) < 180):
                    continue

                entry = {
                    "timestamp": timestamp,
                    "played_at": datetime.now(IST).isoformat(),
                    "track": track_name,
                    "track_uri": track_uri,
                    "track_image": track_image,
                    "artist": artist_name,
                    "artist_uri": artist_uri,
                    "album": album_name,
                    "album_uri": album_uri,
                    "context": {"name": context_name, "uri": context_uri}
                }

                # Find existing document by `user_uri`. If not present, try migrating
                # legacy documents indexed by display `name` when a `user_uri` is available.
                existing = friends_collection.find_one({"user_uri": user_uri}) if friends_collection is not None else None
                if not existing and friends_collection is not None and user_uri and name:
                    try:
                        byname = friends_collection.find_one({"name": name})
                        if byname and not byname.get('user_uri'):
                            try:
                                friends_collection.update_one({"name": name}, {"$set": {"user_uri": user_uri}})
                                existing = friends_collection.find_one({"user_uri": user_uri})
                            except Exception:
                                existing = byname
                        else:
                            existing = byname
                    except Exception:
                        existing = None
                if not existing:
                    existing = {"history": [], "last_uri": None}
                last_uri = existing.get('last_uri')

                # write if new or sufficiently spaced
                if last_uri != track_uri or (t_ts - (existing.get('last_seen_ts', 0) or 0) > 30):
                    if friends_collection is not None:
                        # prefer filtering by user_uri; fall back to name if user_uri missing
                        id_filter = {"user_uri": user_uri} if user_uri else {"name": name}
                        friends_collection.update_one(id_filter, {"$set": {"name": name, "user_uri": user_uri, "user_image": user_image, "last_uri": track_uri, "last_seen": datetime.now(IST).isoformat(), "last_seen_ts": t_ts}, "$push": {"history": entry}}, upsert=True)
                        # trim history to last 5000 entries
                        try:
                            friends_collection.update_one(id_filter, {"$push": {"history": {"$each": [], "$slice": -5000}}})
                        except Exception:
                            pass
                    print(f"[Spotify] 🎧 FRIEND ACTIVITY\n👤 {name} — {track_name} — {artist_name} ({timestamp})")
                    notify(name, track_name, artist_name)
                    count += 1

                    # emit websocket event
                    try:
                        if socketio:
                            socketio.emit('new_activity', {"user": name, "track": track_name, "artist": artist_name})
                    except Exception:
                        pass

                LAST_TRACK_CACHE[id_key] = {"track_uri": track_uri, "timestamp": t_ts}
                RECENT_ACTIVITY_CACHE[recent_key] = time.time()

                # cleanup RECENT_ACTIVITY_CACHE entries older than 600s occasionally
                if len(RECENT_ACTIVITY_CACHE) > 10000:
                    thresh = time.time() - 600
                    for k, v in list(RECENT_ACTIVITY_CACHE.items()):
                        if v < thresh:
                            RECENT_ACTIVITY_CACHE.pop(k, None)

            except Exception as e:
                print('❌ Error processing friend:', e)
    if count:
        print(f"[Spotify] Saved Activity: {count} new entries")
        cfg = load_config()
        cfg['last_successful_activity'] = time.time()
        save_config(cfg)


# ================= ROUTES =================
@app.route("/ping", methods=["POST", "OPTIONS"])
def ping():
    if request.method == "OPTIONS":
        return cors(make_response("", 200))
    print("✅ CLIENT CONNECTED")
    return cors(jsonify(ok=True))


@app.route("/", methods=["GET"])
def index():
    return cors(jsonify({
        "name": "Spotify Friend Logger",
        "status": "ok",
        "endpoints": ["/ping", "/spotify-data", "/activity", "/users", "/current", "/health", "/stats"]
    }))


@app.route("/spotify-data", methods=["POST", "OPTIONS"])
def spotify_data():
    # kept for compatibility; forwards to internal processor
    if request.method == "OPTIONS":
        return cors(make_response("", 200))
    data = request.json or {}
    friends = data.get('friends') or []
    process_friends_list(friends)
    return cors(jsonify(ok=True))


@app.route("/activity", methods=["GET"])
def get_activity():
    if friends_collection is None:
        return jsonify([])
    try:
        output = []
        users = (friends_collection.find())
        for user in users:
            name = user.get("name", "Unknown")
            history = user.get("history", [])
            for entry in history[-20:]:
                output.append({"user": name, "track": entry.get("track"), "track_uri": entry.get("track_uri"), "image": entry.get("track_image"), "artist": entry.get("artist"), "artist_uri": entry.get("artist_uri"), "album": entry.get("album"), "album_uri": entry.get("album_uri"), "context": entry.get("context"), "played_at": entry.get("played_at"), "timestamp": entry.get("timestamp")})
        output.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return cors(jsonify(output[:100]))
    except Exception as e:
        print("❌ Error in /activity:", e)
        return jsonify([])


@app.route("/users", methods=["GET", "OPTIONS"])
def get_users():
    if request.method == "OPTIONS":
        return cors(make_response("", 200))
    try:
        users = []
        for user in friends_collection.find():
            name = user.get("name")
            user_uri = user.get("user_uri")
            user_image = user.get("user_image")
            history = user.get("history") or []
            count = len(history)
            last_seen = user.get("last_seen")

            # Determine latest activity entry. Prefer the last element of history
            # (we append new entries), but fall back to an aggregation query
            # sorted by timestamp in case ordering differs.
            latest = None
            if history:
                try:
                    latest = history[-1]
                except Exception:
                    latest = None
            if not latest:
                try:
                    # prefer matching by user_uri; fall back to name for legacy docs
                    match_filter = {"user_uri": user_uri} if user_uri else {"name": name}
                    cursor = friends_collection.aggregate([
                        {"$match": match_filter},
                        {"$unwind": "$history"},
                        {"$sort": {"history.timestamp": -1}},
                        {"$limit": 1}
                    ])
                    first = next(cursor, None)
                    if first:
                        latest = first.get('history')
                except Exception:
                    latest = None

            last_track = None
            last_artist = None
            last_track_image = None
            if latest and isinstance(latest, dict):
                last_track = latest.get('track')
                last_artist = latest.get('artist')
                # prefer modern 'track_image', fall back to legacy 'image'
                last_track_image = latest.get('track_image') or latest.get('image')

            # final fallback for image
            if not last_track_image:
                last_track_image = user_image

            users.append({
                "name": name,
                "count": count,
                "last_seen": last_seen,
                "last_track": last_track,
                "last_artist": last_artist,
                "last_track_image": last_track_image,
                "user_image": user_image,
                "user_uri": user_uri
            })
        return cors(jsonify(users))
    except Exception as e:
        print("❌ /users error:", e)
        return cors(jsonify([]))


@app.route("/user/<user_uri>", methods=["GET", "OPTIONS"])
def get_user_history(user_uri):
    if request.method == "OPTIONS":
        return cors(make_response("", 200))
    try:
        limit = int(request.args.get("limit", 20))
        offset = int(request.args.get("offset", 0))
        # normalize incoming identifier: frontend sometimes sends raw
        # "spotify:user:<id>" URIs, percent-encoded values, or empty strings
        from urllib.parse import unquote
        key = unquote(user_uri or '').strip()
        if key.lower().startswith('spotify:user:'):
            key = key[len('spotify:user:'):]
        if not key:
            return cors(jsonify([]))
        # prefer lookup by user_uri; fall back to display name if user_uri missing
        user = None
        if friends_collection is not None:
            try:
                user = friends_collection.find_one({"user_uri": user_uri})
            except Exception:
                user = None
        if not user and friends_collection is not None:
            try:
                # last-resort: allow queries where caller passed a display name
                user = friends_collection.find_one({"name": user_uri})
            except Exception:
                user = None
        if not user:
            return cors(jsonify([]))
        history = user.get("history", [])
        history = list(reversed(history))
        sliced = history[offset: offset + limit]
        return cors(jsonify(sliced))
    except Exception as e:
        print("❌ pagination error:", e)
        return cors(jsonify([]))


@app.route("/health", methods=["GET"])
def health():
    cfg = load_config()
    browser_alive = _browser_alive()
    spotify_logged_in = cfg.get('spotify_logged_in', True)
    auth_present = bool(cfg.get('auth_token'))
    client_present = bool(cfg.get('client_token'))
    last_fetch = cfg.get('last_fetch', 0)
    active_count = 0
    try:
        data = json.load(open(RAW_FRIENDS_PATH, 'r', encoding='utf-8'))
        friends = data.get('friends') or data.get('buddies') or []
        active_count = len(friends)
    except Exception:
        active_count = 0
    return cors(jsonify({
        "browser_alive": browser_alive,
        "spotify_logged_in": spotify_logged_in,
        "auth_token": auth_present,
        "client_token": client_present,
        "last_fetch": last_fetch,
        "active_friends": active_count
    }))


@app.route("/current", methods=["GET"])
def current():
    try:
        data = json.load(open(RAW_FRIENDS_PATH, 'r', encoding='utf-8'))
        friends = data.get('friends') or data.get('buddies') or []
        # return minimal fields
        out = []
        for f in friends:
            u = f.get('user', {})
            t = f.get('track', {})
            out.append({"user": u.get('name'), "track": t.get('name'), "artist": t.get('artist', {}).get('name'), "timestamp": f.get('timestamp')})
        return cors(jsonify(out))
    except Exception as e:
        print('❌ /current error:', e)
        return cors(jsonify([]))


@app.route("/stats", methods=["GET"])
def stats():
    # aggregate top artists/tracks/playlists and counts
    try:
        pipeline_tracks = [
            {"$unwind": "$history"},
            {"$group": {"_id": "$history.track", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 20}
        ]
        pipeline_artists = [
            {"$unwind": "$history"},
            {"$group": {"_id": "$history.artist", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 20}
        ]
        top_tracks = list(friends_collection.aggregate(pipeline_tracks)) if friends_collection is not None else []
        top_artists = list(friends_collection.aggregate(pipeline_artists)) if friends_collection is not None else []
        total_plays = 0
        active_users = 0
        if friends_collection is not None:
            total_plays = sum([u.get('count', 0) for u in friends_collection.aggregate([{"$project": {"count": {"$size": {"$ifNull": ["$history", []]}}}}])])
            active_users = friends_collection.count_documents({})
        return cors(jsonify({"top_tracks": top_tracks, "top_artists": top_artists, "total_plays": total_plays, "active_users": active_users}))
    except Exception as e:
        print('❌ /stats error:', e)
        return cors(jsonify({}))


# ================= LOG WINDOW =================

def open_logs():
    try:
        # Use os.startfile on Windows to avoid spawning a separate console host
        if platform.system() == 'Windows':
            try:
                os.startfile(LOGS_PATH)
            except Exception:
                subprocess.Popen(["notepad", LOGS_PATH])
        else:
            subprocess.Popen(["notepad", LOGS_PATH])
    except Exception:
        print('[Spotify] ⚠️ Could not open logs with notepad')


# ================= TRAY =================

def quit_app(icon, item):
    icon.stop()
    os._exit(0)


def tray_icon():
    # Disable tray icon to avoid spawning GUI/notification subprocesses on Windows 8.1
    print('[Spotify] Tray icon disabled for stability on this platform')
    return


# ================= SELENIUM STEALTH & TOKEN CAPTURE =================

drivers_lock = threading.Lock()
TOKEN_REFRESH_LOCK = threading.Lock()
_browser_driver = None
_browser_failures = 0
_browser_last_failure = 0
MAX_BROWSER_RETRIES = 3
BROWSER_RETRY_COOLDOWN = 60

# Global guard to prevent overlapping browser refreshes/launches
BROWSER_REFRESH_ACTIVE = False
BROWSER_REFRESH_LOCK = threading.Lock()

# Cache for detected Chrome major version to avoid repeated subprocess calls
CHROME_MAJOR_CACHE = None

# Notification queue and worker to ensure toasts are shown only from a single thread
NOTIFICATION_QUEUE = queue.Queue()


def notification_worker():
    while True:
        try:
            item = NOTIFICATION_QUEUE.get()
            if not item:
                time.sleep(1)
                continue
            title, msg = item if isinstance(item, tuple) and len(item) >= 2 else ("Spotify Friend Tracker", str(item))
            # Only log notifications to avoid spawning Windows toast subprocesses
            try:
                print('[Spotify] Notification:', title, msg)
            except Exception as e:
                print('[Spotify] ⚠️ notification error:', e)
            # small delay to avoid log flooding
            time.sleep(1)
        except Exception as e:
            print('[Spotify] ⚠️ notification worker error:', e)
            time.sleep(1)


def detect_chrome_major():
    """Attempt to detect installed Chrome major version. Return int or None."""
    global CHROME_MAJOR_CACHE
    if CHROME_MAJOR_CACHE is not None:
        return CHROME_MAJOR_CACHE
    # Try common executable paths
    candidates = []
    # PATH
    which_paths = ['chrome', 'chrome.exe']
    for name in which_paths:
        p = shutil.which(name)
        if p:
            candidates.append(p)

    # Common Windows locations
    if platform.system() == 'Windows':
        pf = os.environ.get('PROGRAMFILES', r'C:\Program Files')
        pfx86 = os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')
        local_app = os.environ.get('LOCALAPPDATA')
        candidates.extend([
            os.path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe'),
            os.path.join(pfx86, 'Google', 'Chrome', 'Application', 'chrome.exe') if pfx86 else None,
            os.path.join(local_app, 'Google', 'Chrome', 'Application', 'chrome.exe') if local_app else None
        ])
    else:
        # Linux / macOS common
        candidates.extend(['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'])

    for c in candidates:
        if not c:
            continue
        if not os.path.exists(c):
            continue
        try:
            # On Windows some Chrome builds may briefly open a window when
            # invoked; prevent that by using CREATE_NO_WINDOW when available.
            creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            out = subprocess.check_output([c, '--version'], stderr=subprocess.STDOUT, timeout=5, creationflags=creationflags)
            s = out.decode('utf-8', errors='ignore').strip()
            # e.g., Google Chrome 148.0.7778.179
            parts = [p for p in s.split() if any(ch.isdigit() for ch in p)]
            if parts:
                ver = parts[-1]
                major = int(ver.split('.')[0])
                CHROME_MAJOR_CACHE = major
                print(f'[Spotify] Detected Chrome Version: {major}')
                return major
        except Exception:
            continue

    # Try registry on Windows
    if platform.system() == 'Windows' and winreg:
        try:
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    key = winreg.OpenKey(root, r"SOFTWARE\Google\Chrome\BLBeacon")
                    val, _ = winreg.QueryValueEx(key, 'version')
                    if val:
                        major = int(str(val).split('.')[0])
                        CHROME_MAJOR_CACHE = major
                        print(f'[Spotify] Detected Chrome Version: {major} (registry)')
                        return major
                except Exception:
                    continue
        except Exception:
            pass

    print('[Spotify] Could not detect Chrome version')
    return None


def _browser_alive():
    global _browser_driver
    try:
        return _browser_driver is not None
    except Exception:
        return False


def create_browser(cfg):
    global _browser_driver
    global _browser_failures, _browser_last_failure
    # Respect retry limits
    if _browser_failures >= MAX_BROWSER_RETRIES:
        since = time.time() - _browser_last_failure
        if since < BROWSER_RETRY_COOLDOWN:
            print(f"[Spotify] Browser start suppressed for {int(BROWSER_RETRY_COOLDOWN - since)}s due to previous failures")
            return None
        else:
            # reset counter after cooldown
            _browser_failures = 0

    # Try to use undetected_chromedriver if available; otherwise fall back
    # to normal selenium + webdriver_manager ChromeDriver. This keeps the
    # same options/cookies/CDP logic while improving compatibility on
    # platforms (eg. Windows 8.1) where uc may fail to import/start.
    use_uc = False
    driver = None
    if uc is not None:
        use_uc = True
    else:
        try:
            import undetected_chromedriver as _uc
            # assign to module-level name for consistency
            globals()['uc'] = _uc
            use_uc = True
        except Exception:
            use_uc = False

    # Use a single, stable Options class for all browser types. This avoids
    # fragile platform-specific variants (uc.ChromeOptions vs
    # webdriver.ChromeOptions) and improves compatibility on Windows 8.1.
    try:
        from selenium.webdriver.chrome.options import Options
        opts = Options()
    except Exception:
        print('[Spotify] ⚠️ Failed to import/create selenium Options; traceback:')
        traceback.print_exc()
        return None
    # Stealth/realistic options: use the REAL detected Chrome version in the UA.
    # A mismatched UA (e.g. claiming 114 while actual is 109) is a bot-detection flag.
    detected_major = None
    try:
        detected_major = detect_chrome_major()
    except Exception:
        detected_major = None
    if detected_major:
        ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{detected_major}.0.0.0 Safari/537.36"
    else:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
    opts.add_argument(f'--user-agent={ua}')
    if cfg.get('headless'):
        opts.add_argument('--headless=new')
        opts.add_argument('--disable-gpu')
    # Stability and platform flags (helpful on older Windows builds)
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-software-rasterizer")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-background-timer-throttling")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_argument("--disable-features=RendererCodeIntegrity")
    opts.add_argument("--disable-features=VizDisplayCompositor")
    opts.add_argument("--disable-sync")
    opts.add_argument("--metrics-recording-only")
    opts.add_argument("--mute-audio")
    opts.add_argument("--disable-default-apps")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    # SSL / legacy Windows compatibility
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--allow-running-insecure-content")
    opts.add_argument("--ignore-ssl-errors=yes")
    opts.add_argument("--ignore-certificate-errors-spki-list")
    # Sandbox fixes
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-setuid-sandbox")
    # performance / privacy
    prefs = {
        "profile.default_content_setting_values": {"images": 2, "notifications": 2},
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False
    }
    try:
        opts.add_experimental_option('prefs', prefs)
    except Exception:
        pass
    # enable performance logging so we can read Network events via get_log('performance')
    try:
        opts.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    except Exception:
        try:
            opts.add_experimental_option('w3c', False)
        except Exception:
            pass
    # disable automation flags
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_argument('--no-first-run')
    opts.add_argument('--no-default-browser-check')
    opts.add_argument('--disable-infobars')
    opts.add_argument('--disable-extensions')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-background-timer-throttling')
    opts.add_argument('--disable-backgrounding-occluded-windows')
    opts.add_argument('--disable-renderer-backgrounding')
    opts.add_argument('--window-size=1200,800')
    if cfg.get('chrome_profile'):
        opts.add_argument(f"--user-data-dir={cfg.get('chrome_profile')}")
    # detect chrome version and pass version_main to uc.Chrome
    version_main = None
    try:
        version_main = detect_chrome_major()
    except Exception:
        version_main = None

    if version_main:
        print('[Spotify] Starting ChromeDriver...')
    else:
        print('[Spotify] Starting ChromeDriver (version auto) ...')

    try:
        # Choose driver creation strategy and log which we're using
        if use_uc:
            print('[Spotify] Using undetected_chromedriver')
        else:
            print('[Spotify] Falling back to normal Selenium ChromeDriver')

        # Prefer selenium-wire when available (keeps request interception)
        if SELENIUM_WIRE_AVAILABLE:
            try:
                # disable encoding to make responses easier to read
                sw_opts = {'disable_encoding': True}
                driver = sw_webdriver.Chrome(options=opts)
            except Exception:
                driver = None

        # If selenium-wire didn't produce a driver, try primary choices
        if driver is None:
            if use_uc:
                if version_main:
                    driver = uc.Chrome(options=opts, version_main=version_main)
                else:
                    driver = uc.Chrome(options=opts)
            else:
                # Normal Selenium fallback using webdriver-manager to install
                try:
                    from selenium import webdriver as selenium_webdriver
                    from selenium.webdriver.chrome.service import Service
                    # For Windows with Chrome 109 prefer a local chromedriver.exe
                    CHROMEDRIVER_PATH = os.path.join(BASE_DIR, 'chromedriver.exe')
                    if platform.system() == 'Windows' and version_main == 109:
                        if os.path.exists(CHROMEDRIVER_PATH):
                            service = Service(CHROMEDRIVER_PATH)
                            # Hide the chromedriver console window on Windows
                            try:
                                service.creation_flags = 0x08000000
                            except Exception:
                                pass
                            driver = selenium_webdriver.Chrome(service=service, options=opts)
                        else:
                            print('[Spotify] chromedriver.exe not found')
                            driver = None
                    else:
                        # fallback to webdriver-manager for other versions/platforms
                        try:
                            from webdriver_manager.chrome import ChromeDriverManager
                            chromedriver_path = ChromeDriverManager().install()
                            service = Service(chromedriver_path)
                            # Hide the chromedriver console window on Windows
                            try:
                                service.creation_flags = 0x08000000
                            except Exception:
                                pass
                            driver = selenium_webdriver.Chrome(service=service, options=opts)
                        except Exception as e:
                            print('[Spotify] ⚠️ webdriver-manager install error:', e)
                            driver = None
                except Exception as e:
                    print('[Spotify] ⚠️ normal Selenium start error:', e)
                    driver = None
        # additional stealth: redefine webdriver
        try:
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'
            })
        except Exception:
            pass
        _browser_driver = driver
        print('[Spotify] Chrome Started Successfully')
        # reset failure counters
        _browser_failures = 0
        _browser_last_failure = 0
        return driver
    except Exception as e:
        print('[Spotify] ⚠️ Chrome start error:', e)
        _browser_failures += 1
        _browser_last_failure = time.time()
        return None


def ensure_browser(cfg):
    global _browser_driver
    with drivers_lock:
        try:
            if _browser_driver is None:
                _browser_driver = create_browser(cfg)
            else:
                # quick ping
                _browser_driver.title
        except Exception:
            try:
                _browser_driver = create_browser(cfg)
            except Exception:
                _browser_driver = None
    return _browser_driver


# Previous in-page JS header capture removed. Using CDP Network events instead.


def browser_fetch_buddylist(cfg, headers, timeout=15):
    # Browser fallback disabled: do not auto-launch Chrome here. Browser fetch
    # should be done using the same browser instance used for token refresh.
    print('[Spotify] browser_fetch_buddylist called but auto-launch disabled')
    return 0, ''


def start_browser_and_capture_tokens_locked(cfg, timeout=30):
    global BROWSER_REFRESH_ACTIVE
    # Prevent overlapping refresh attempts
    with TOKEN_REFRESH_LOCK:
        with BROWSER_REFRESH_LOCK:
            if BROWSER_REFRESH_ACTIVE:
                print('[Spotify] Browser refresh already active; skipping')
                return 0, '', cfg
            BROWSER_REFRESH_ACTIVE = True
        try:
            return start_browser_and_capture_tokens(cfg, timeout)
        finally:
            with BROWSER_REFRESH_LOCK:
                BROWSER_REFRESH_ACTIVE = False


# scan_headers_for_tokens removed; using Chrome DevTools Protocol events via performance logs


def is_logged_in(driver):
    try:
        url = driver.current_url
        if '/login' in url:
            return False
        # check for "Log in" text presence
        txt = driver.execute_script('return document.body.innerText || ""') or ''
        if 'Log in' in txt or 'Sign in' in txt:
            return False
        return True
    except Exception:
        return False


def start_browser_and_capture_tokens(cfg, timeout=30):
    # Create a temporary browser for token capture; will be closed before returning
    global _browser_driver
    # Ensure only one thread attempts to create a browser at a time
    with drivers_lock:
        driver = create_browser(cfg)
    if driver is None:
        return 0, '', cfg

    try:
        driver.get('https://open.spotify.com/')
    except Exception as e:
        print('[Spotify] browser get error:', e)

    # inject cookies or sp_dc fallback
    cookies = load_cookies()
    if not cookies and cfg.get('sp_dc'):
        spdc = cfg.get('sp_dc')
        if spdc:
            c = {'name': 'sp_dc', 'value': spdc, 'domain': '.spotify.com', 'path': '/', 'secure': True}
            cookies = [c]
            print('[Spotify] Injecting sp_dc from config')
    # add cookies to browser
    try:
        for c in cookies:
            sc = sanitize_cookie(c)
            if not sc:
                continue
            cookie_for = {'name': sc['name'], 'value': sc['value'], 'domain': sc.get('domain'), 'path': sc.get('path', '/')}
            if 'expiry' in sc:
                cookie_for['expiry'] = sc['expiry']
            if sc.get('secure'):
                cookie_for['secure'] = True
            try:
                driver.add_cookie(cookie_for)
            except Exception:
                pass
        driver.refresh()
        print('[Spotify] Cookies Loaded')
    except Exception:
        pass

    # Use selenium-wire when available for reliable outgoing request capture.
    try:
        try:
            driver.execute_cdp_cmd('Network.enable', {})
            driver.execute_cdp_cmd('Network.setCacheDisabled', {'cacheDisabled': True})
        except Exception:
            pass

        # Force navigations and interactions to generate Spotify API traffic
        try:
            driver.get('https://open.spotify.com/collection/tracks')
            time.sleep(3)
            try:
                driver.refresh()
            except Exception:
                pass
            for _ in range(3):
                try:
                    driver.execute_script('window.scrollBy(0, 800);')
                except Exception:
                    pass
                time.sleep(0.5)
            try:
                driver.get('https://open.spotify.com/')
            except Exception:
                pass
            try:
                driver.get('https://open.spotify.com/collection/tracks')
            except Exception:
                pass
            try:
                driver.get('https://open.spotify.com/search')
            except Exception:
                pass
            try:
                driver.get('https://open.spotify.com/')
            except Exception:
                pass
        except Exception:
            pass

        start = time.time()
        auth = None
        client = None

        if SELENIUM_WIRE_AVAILABLE:
            try:
                try:
                    driver.requests.clear()
                except Exception:
                    pass
                while time.time() - start < timeout:
                    try:
                        for req in list(driver.requests):
                            try:
                                url = req.url or ''
                                if not any(h in url for h in ('open.spotify.com', 'spclient.wg.spotify.com', 'guc-spclient.spotify.com')):
                                    continue
                                headers = req.headers or {}
                                ah = headers.get('Authorization') or headers.get('authorization')
                                ch = headers.get('client-token') or headers.get('Client-Token') or headers.get('client-Token')
                                if ah and isinstance(ah, str) and ah.startswith('Bearer ') and ch and isinstance(ch, str) and len(ch) > 50 and len(ah) > 100:
                                    auth = ah
                                    client = ch
                                    break
                            except Exception:
                                continue
                        if auth and client:
                            break
                    except Exception:
                        pass
                    time.sleep(0.5)
            except Exception:
                pass
        else:
            # fallback: performance logs (less reliable)
            while time.time() - start < timeout:
                try:
                    logs = []
                    try:
                        logs = driver.get_log('performance')
                    except Exception:
                        logs = []
                    for entry in logs:
                        try:
                            msg = json.loads(entry.get('message', '{}')).get('message', {})
                            method = msg.get('method')
                            if method != 'Network.requestWillBeSent':
                                continue
                            params = msg.get('params', {})
                            request = params.get('request', {})
                            url = request.get('url', '')
                            if not any(h in url for h in ('open.spotify.com', 'spclient.wg.spotify.com', 'guc-spclient.spotify.com')):
                                continue
                            headers = request.get('headers') or {}
                            ah = headers.get('Authorization') or headers.get('authorization')
                            ch = headers.get('client-token') or headers.get('Client-Token') or headers.get('client-Token')
                            if ah and isinstance(ah, str) and ah.startswith('Bearer ') and ch and isinstance(ch, str) and len(ch) > 50 and len(ah) > 100:
                                auth = ah
                                client = ch
                                break
                        except Exception:
                            continue
                    if auth and client:
                        break
                except Exception:
                    pass
                try:
                    driver.execute_script('window.scrollBy(0, 400);')
                except Exception:
                    pass
                time.sleep(0.5)

        # Always print token debug summary (never print full tokens)
        try:
            a_len = len(auth) if auth else 0
        except Exception:
            a_len = 0
        try:
            c_len = len(client) if client else 0
        except Exception:
            c_len = 0
        print('[Spotify] Auth Token Found:', 'YES' if a_len>0 else 'NO')
        print('[Spotify] Client Token Found:', 'YES' if c_len>0 else 'NO')
        print('[Spotify] Auth Length:', a_len, 'Client Length:', c_len)

        # save fresh cookies
        try:
            new_cookies = driver.get_cookies()
            save_cookies(new_cookies)
            print('[Spotify] Cookies Saved')
        except Exception:
            pass

        # Only accept tokens if they meet strict criteria
        saved = False
        if auth and auth.startswith('Bearer ') and client and len(client) > 50:
            at = auth.split(' ', 1)[1]
            cfg['auth_token'] = at
            cfg['client_token'] = client
            now = time.time()
            cfg['auth_captured_at'] = now
            cfg['client_captured_at'] = now
            saved = True

        # login status — CRITICAL: if the web player is logged out, the captured
        # tokens belong to an ANONYMOUS session and presence-view will reject
        # them with 400. Abort early with a clear message instead of chasing 400s.
        logged = is_logged_in(driver)
        cfg['spotify_logged_in'] = bool(logged)
        cfg['last_login_check'] = time.time()
        save_config(cfg)

        if not logged:
            print('[Spotify] ❌ Web player is NOT logged in (sp_dc cookies expired or invalid).')
            print('[Spotify] 👉 Update sp_dc in config.json or log into Spotify in the browser.')
            try:
                NOTIFICATION_QUEUE.put(('Spotify Friend Tracker', 'Spotify not logged in. Update sp_dc in config.json.'))
            except Exception:
                pass
            try:
                driver.quit()
            except Exception:
                pass
            try:
                _browser_driver = None
            except Exception:
                pass
            return 0, '', cfg

        # If we successfully captured tokens, reset any previous failed-refresh cooldown
        if saved:
            try:
                if 'last_refresh_failed' in cfg:
                    cfg.pop('last_refresh_failed', None)
                    save_config(cfg)
            except Exception:
                pass

        if not saved:
            print('[Spotify] ⚠️ Valid tokens not captured during browser session')
            try:
                driver.quit()
            except Exception:
                pass
            try:
                _browser_driver = None
            except Exception:
                pass
            return 0, '', cfg

        # Attempt an in-browser buddylist fetch using the same browser instance
        status = 0
        text = ''
        try:
            fetch_script = r"""
var callback = arguments[arguments.length-1];
var url = arguments[0];
var headers = arguments[1] || {};
fetch(url, {method: 'GET', headers: headers, credentials: 'include'})
.then(function(res){
    res.text().then(function(text){
    callback(JSON.stringify({status: res.status, text: text}));
    }).catch(function(err){
    callback(JSON.stringify({status: res.status, text: '', error: String(err)}));
    });
}).catch(function(err){
    callback(JSON.stringify({status: 0, text: '', error: String(err)}));
});
"""
            headers = {
                'accept': 'application/json',
                'app-platform': 'WebPlayer',
                'origin': 'https://open.spotify.com',
                'referer': 'https://open.spotify.com/'
            }
            if cfg.get('auth_token'):
                headers['Authorization'] = f"Bearer {cfg.get('auth_token')}"
            if cfg.get('client_token'):
                headers['client-token'] = cfg.get('client_token')
            # execute_async_script requires an explicit script timeout
            # (the default is 0ms, which makes the async callback fail)
            try:
                driver.set_script_timeout(30)
            except Exception:
                pass
            raw = None
            try:
                raw = driver.execute_async_script(fetch_script, SPOTIFY_BUDDYLIST_URL, headers)
            except Exception as e:
                print('[Spotify] ⚠️ browser fetch error during refresh:', e)
                raw = None
            try:
                if isinstance(raw, str):
                    resp = json.loads(raw)
                else:
                    resp = raw or {}
            except Exception:
                resp = {}
            try:
                status = int(resp.get('status') or 0)
            except Exception:
                status = 0
            text = resp.get('text') or ''

        except Exception:
            status = 0
            text = ''

        # Close temporary browser to avoid leaving Chrome open
        try:
            driver.quit()
        except Exception:
            pass
        try:
            _browser_driver = None
        except Exception:
            pass

        # Update config with last activity if fetch succeeded
        if status == 200 and text:
            try:
                data = json.loads(text or '{}')
                if isinstance(data, dict) and (data.get('friends') is not None or data.get('buddies') is not None):
                    cfg['last_successful_activity'] = time.time()
                    save_config(cfg)
            except Exception:
                pass

        return status, text, cfg
    except Exception as e:
        print('[Spotify] ⚠️ token capture/fetch error:', e)
        try:
            driver.quit()
        except Exception:
            pass
        try:
            _browser_driver = None
        except Exception:
            pass
        return 0, '', cfg


# Keepalive thread: independent of fetch loop

# Note: persistent keepalive/browser threads removed. Browser is only launched temporarily
# when tokens must be refreshed.


# ================= FETCH LOOP =================

def fetch_buddylist_and_store(cfg, driver=None):
    print('[Spotify] Fetching Friend Activity...')
    url = SPOTIFY_BUDDYLIST_URL
    cfg['last_fetch'] = time.time()
    save_config(cfg)

    headers = {
        'accept': 'application/json',
        'app-platform': 'WebPlayer',
        'origin': 'https://open.spotify.com',
        'referer': 'https://open.spotify.com/'
    }
    if cfg.get('auth_token'):
        headers['Authorization'] = f"Bearer {cfg.get('auth_token')}"
    if cfg.get('client_token'):
        headers['client-token'] = cfg.get('client_token')

    # Try simple requests GET with retries (no browser) first
    attempts = 3
    delay = 5
    response_text = None
    status_code = None
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, headers=headers, timeout=10)
            status_code = r.status_code
            response_text = r.text
            print(f"[Spotify] Fetch attempt {attempt} status: {status_code}")
            if status_code == 200:
                break
            if status_code in (400, 401, 403):
                # auth/client-token problems are not transient — no point retrying
                break
        except Exception as e:
            print('[Spotify] ⚠️ fetch error attempt', attempt, e)
            response_text = None
            status_code = None
        time.sleep(delay)

    # Debug: print status and snippet
    try:
        print('[Spotify] Buddylist Status:', status_code)
        snippet = (response_text or '')[:300]
        print('[Spotify] Buddylist Response Snippet:', snippet)
    except Exception:
        pass

    # Only overwrite the raw file on SUCCESS — previously a failed request
    # wrote '' over the last good buddylist, wiping /current data.
    if status_code == 200 and response_text:
        try:
            save_raw_friends(response_text)
        except Exception:
            pass
        try:
            data = json.loads(response_text or '{}')
        except Exception as e:
            print('[Spotify] ⚠️ parse buddylist error:', e)
            return
        try:
            if isinstance(data, dict) and (data.get('friends') is not None or data.get('buddies') is not None):
                cfg['last_successful_activity'] = time.time()
                save_config(cfg)
        except Exception:
            pass
        friends = data.get('friends') or data.get('buddies') or []
        print(f"[Spotify] Found {len(friends)} Active Friends")
        process_friends_list(friends)
        return

    # If we reach here, local requests failed (or returned auth error).
    # Instead of auto-launching another browser for a fallback fetch, perform
    # a single token refresh which will launch ONE browser and use it to both
    # capture tokens and perform an in-browser fetch. This avoids double
    # browser launches.
    # Decide whether to allow a browser refresh now. We should allow an
    # immediate refresh when tokens are missing/expired or when the
    # buddylist returned 401. Only apply cooldown for repeated failed
    # browser launches using `last_refresh_failed` stored in config.
    now = time.time()
    last_failed = float(cfg.get('last_refresh_failed', 0) or 0)
    auth_missing = not bool(cfg.get('auth_token'))
    client_missing = not bool(cfg.get('client_token'))
    need_immediate = auth_missing or client_missing or (status_code == 401)
    if not need_immediate:
        # If a previous browser refresh recently failed, enforce cooldown
        if now - last_failed < 300:
            print('[Spotify] Refresh cooldown active after failed browser launch')
            return

    print('[Spotify] Attempting token refresh and browser fetch')
    try:
        status, fetched_text, cfg = start_browser_and_capture_tokens_locked(cfg, timeout=30)
    except Exception as e:
        print('[Spotify] ⚠️ start browser error during refresh:', e)
        # mark this refresh attempt as a failure to trigger cooldown
        cfg['last_refresh_failed'] = time.time()
        cfg['last_login_check'] = time.time()
        cfg['spotify_logged_in'] = False
        save_config(cfg)
        try:
            NOTIFICATION_QUEUE.put(('Spotify Friend Tracker', 'Spotify cookies expired. Please update cookies/sp_dc.'))
        except Exception:
            pass
        return

    # If the browser-fetch returned a successful buddylist, process it
    print('[Spotify] Browser refresh fetch status:', status)
    try:
        print('[Spotify] Buddylist Response Snippet:', (fetched_text or '')[:300])
    except Exception:
        pass
    if status == 200 and fetched_text:
        try:
            save_raw_friends(fetched_text)
        except Exception:
            pass

    if status == 200 and fetched_text:
        try:
            data = json.loads(fetched_text or '{}')
        except Exception as e:
            print('[Spotify] ⚠️ parse buddylist error (after refresh browser fetch):', e)
            return
        try:
            if isinstance(data, dict) and (data.get('friends') is not None or data.get('buddies') is not None):
                cfg['last_successful_activity'] = time.time()
                save_config(cfg)
        except Exception:
            pass
        friends = data.get('friends') or data.get('buddies') or []
        print(f"[Spotify] Found {len(friends)} Active Friends (after refresh)")
        process_friends_list(friends)
        return

    # If we get here, refresh did not return a valid buddylist
    print('[Spotify] ⚠️ Buddylist still failing after refresh')
    # Mark this refresh attempt as failed so subsequent attempts are
    # subject to the cooldown.
    try:
        cfg['last_refresh_failed'] = time.time()
        save_config(cfg)
    except Exception:
        pass
    return


def background_fetch_loop():
    cfg = load_config()
    while True:
        try:
            # live reload config each loop
            cfg = load_config()

            # Perform a requests-first fetch using saved tokens. If that fails,
            # fetch_buddylist_and_store will handle a single browser refresh
            # (and respect cooldowns). This avoids proactive token recapture
            # on every loop and prevents unnecessary browser launches.
            fetch_buddylist_and_store(cfg)

        except Exception as e:
            print('[Spotify] ⚠️ loop fetch error:', e)
            # on errors in loop, sleep and continue
            time.sleep(5)
        # randomized interval around the configured value to look more natural
        try:
            cfg = load_config()
            interval = int(cfg.get('activity_interval', ACTIVITY_INTERVAL) or ACTIVITY_INTERVAL)
        except Exception:
            interval = ACTIVITY_INTERVAL
        sleep_time = max(20, interval + random.randint(-10, 20))
        try:
            print(f"[Spotify] Next fetch in {sleep_time}s")
        except Exception:
            pass
        time.sleep(sleep_time)


# ================= START =================

def start_flask():
    if socketio:
        socketio.run(app, host=FLASK_HOST, port=FLASK_PORT)
    else:
        app.run(FLASK_HOST, FLASK_PORT, threaded=True)


if __name__ == "__main__":

    print("🚀 Spotify Friend Tracker started")

    th.Thread(
        target=start_flask,
        daemon=True
    ).start()

    th.Thread(
        target=notification_worker,
        daemon=True
    ).start()

    th.Thread(
        target=background_fetch_loop,
        daemon=True
    ).start()

    # Keep main thread alive
    while True:
        time.sleep(60)
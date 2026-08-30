from flask import Flask, request, jsonify, make_response
import json, os, re, tempfile, threading, sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
import requests

# ================= CONFIGURATION =================

# MongoDB Configuration
MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "spotify_friends"
COLLECTION_NAME = "friends_activity"

# WhatsApp Configuration
SPOTIFY_GROUP_NAME = "Spoti-Friend-Logger"
API_PORT = 3939
WHATSAPP_API_URL = f"http://localhost:{API_PORT}/spotify"

# Flask Configuration
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 5000

# ---- Tray / UI ----
import pystray
from pystray import MenuItem as item
from PIL import Image
import threading as th
import tkinter as tk
from tkinter.scrolledtext import ScrolledText

# ---- Windows Notification ----
try:
    from win10toast import ToastNotifier
    toaster = ToastNotifier()
except Exception:
    toaster = None

# ================= PATH HELPERS =================

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

# ================= BASE CONFIG =================

BASE_DIR = exe_dir()
ICON_PATH = resource_path("icon.ico")
IST = ZoneInfo("Asia/Kolkata")

# MongoDB Connection
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')
    db = mongo_client[DB_NAME]
    friends_collection = db[COLLECTION_NAME]
    print("✅ MongoDB connection successful")
except ConnectionFailure:
    print("⚠️  MongoDB connection failed. Make sure MongoDB is running.")
    mongo_client = None
    db = None
    friends_collection = None

# Ensure unique index on user_uri (sparse for backward compatibility)
try:
    if friends_collection is not None:
        try:
            friends_collection.create_index("user_uri", unique=True, sparse=True)
            print('[Spotify] Created unique index on user_uri (legacy server)')
        except Exception as e:
            print('[Spotify] ⚠️ legacy server user_uri index warning:', e)
except Exception:
    pass

app = Flask(__name__)
LOCK = threading.Lock()

# ================= LOGGING =================

LOG_BUFFER = []

class LogWriter:
    def write(self, msg):
        if not msg:
            return
        if isinstance(msg, bytes):
            msg = msg.decode("utf-8", errors="replace")
        msg = str(msg)
        if msg.strip():
            LOG_BUFFER.append(msg)
            if len(LOG_BUFFER) > 500:
                LOG_BUFFER.pop(0)

    def flush(self):
        pass

sys.stdout = LogWriter()
sys.stderr = LogWriter()

# ================= CORS =================

def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp

# ================= TIME HELPERS =================

TIME_RE = re.compile(r"^\d+\s*(sec|secs|min|mins|hr|hrs|h|d|day|days)$", re.I)

def is_time(s):
    return bool(TIME_RE.match(s.strip()))

# ================= STORAGE =================

def load_friends():
    """Load all friends from MongoDB"""
    if friends_collection is None:
        return {}
    try:
        friends = {}
        for doc in friends_collection.find():
            friend_id = doc.pop("_id", None)
            if friend_id:
                friends[str(friend_id)] = doc
        return friends
    except Exception as e:
        print(f"❌ Error loading friends from MongoDB: {e}")
        return {}

def save_friends(data):
    """Save friends data to MongoDB"""
    if friends_collection is None:
        print("❌ MongoDB not connected")
        return
    try:
        with LOCK:
            for name, user_data in data.items():
                # prefer user_uri when present in provided data
                user_uri = user_data.get('user_uri') if isinstance(user_data, dict) else None
                id_filter = {"user_uri": user_uri} if user_uri else {"name": name}
                friends_collection.update_one(
                    id_filter,
                    {"$set": user_data},
                    upsert=True
                )
    except Exception as e:
        print(f"❌ Error saving to MongoDB: {e}")

# ================= PARSER =================

def extract_friend_activity(text):
    start = text.find("Friend Activity")
    if start == -1:
        return []

    end = len(text)
    for m in ["Resize main navigation", "Now playing:"]:
        i = text.find(m, start)
        if i != -1:
            end = min(end, i)

    block = text[start:end]
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    if not lines or lines[0] != "Friend Activity":
        return []

    lines = lines[1:]
    out = []
    i = 0

    while i < len(lines):
        name = lines[i]
        if is_time(name) or name == "•":
            i += 1
            continue

        i += 1
        if i < len(lines) and is_time(lines[i]):
            i += 1

        if i + 2 >= len(lines):
            break

        track, dot, artist = lines[i:i+3]
        if dot != "•":
            continue

        out.append({
            "name": name,
            "track": track,
            "artist": artist
        })
        i += 3

    return out

# 🔥 GLOBAL CACHE (add near top)
IMAGE_CACHE = {}

def clean_track(track):
    if not track:
        return ""
    track = track.split(" - ")[0]
    track = track.replace('"', '').strip()
    return track


def get_album_image(track, artist):
    key = f"{track}|{artist}"

    # ✅ CACHE HIT
    if key in IMAGE_CACHE:
        return IMAGE_CACHE[key]

    track = clean_track(track)
    query = f"{track} {artist}".strip()

    # ================= iTunes =================
    try:
        res = requests.get(
            "https://itunes.apple.com/search",
            params={
                "term": query,
                "media": "music",
                "limit": 1
            },
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=5
        )

        if res.status_code == 200:
            data = res.json()
            results = data.get("results", [])

            if results:
                img = results[0].get("artworkUrl100")
                if img:
                    img = img.replace("100x100", "300x300")
                    IMAGE_CACHE[key] = img
                    return img
        else:
            print(f"⚠️ iTunes {res.status_code}")

    except Exception as e:
        print("⚠️ iTunes error:", e)

    # ================= Deezer (fallback) =================
    try:
        res = requests.get(
            "https://api.deezer.com/search",
            params={"q": query},
            timeout=5
        )

        if res.status_code == 200:
            data = res.json()
            results = data.get("data", [])

            if results:
                img = results[0]["album"]["cover_big"]
                IMAGE_CACHE[key] = img
                return img
        else:
            print(f"⚠️ Deezer {res.status_code}")

    except Exception as e:
        print("⚠️ Deezer error:", e)

    IMAGE_CACHE[key] = None
    return None

# ================= NOTIFY =================

def notify(friend, track, artist):
    msg = f"{friend} started listening to\n{track} — {artist}"
    if toaster:
        try:
            toaster.show_toast(
                "🎧 Spotify Friend Tracker",
                msg,
                icon_path=ICON_PATH,
                duration=5,
                threaded=True
            )
        except:
            pass

# ================= ROUTES =================

@app.route("/ping", methods=["POST", "OPTIONS"])
def ping():
    if request.method == "OPTIONS":
        return cors(make_response("", 200))
    print("✅ EXTENSION CONNECTED")
    return cors(jsonify(ok=True))

@app.route("/activity", methods=["GET"])
def get_activity():
    if friends_collection is None:
        return jsonify([])

    try:
        output = []

        # Get all users
        users = friends_collection.find()

        for user in users:
            name = user.get("name", "Unknown")
            history = user.get("history", [])

            # Get last 10 songs per user
            for entry in history[-10:]:
                output.append({
                    "user": name,
                    "track": entry.get("track"),
                    "artist": entry.get("artist"),
                    "played_at": entry.get("played_at"),
                    "uri": entry.get("uri", None)  # will be None (fine)
                })

        # Sort latest first
        output.sort(
            key=lambda x: x.get("played_at", ""),
            reverse=True
        )

        return cors(jsonify(output[:50]))

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
            users.append({
                "name": user.get("name"),
                "count": len(user.get("history", []))
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

        # prefer lookup by user_uri; fall back to display name
        user = None
        if friends_collection is not None:
            try:
                user = friends_collection.find_one({"user_uri": user_uri})
            except Exception:
                user = None
        if not user and friends_collection is not None:
            try:
                user = friends_collection.find_one({"name": user_uri})
            except Exception:
                user = None

        if not user:
            return cors(jsonify([]))

        history = user.get("history", [])

        # newest first
        history = list(reversed(history))

        # pagination
        sliced = history[offset:offset + limit]

        return cors(jsonify(sliced))

    except Exception as e:
        print("❌ pagination error:", e)
        return cors(jsonify([]))
    

@app.route("/page", methods=["POST", "OPTIONS"])
def page():
    if request.method == "OPTIONS":
        return cors(make_response("", 200))

    if friends_collection is None:
        return cors(jsonify(ok=False, error="MongoDB not connected"))

    parsed = extract_friend_activity((request.json or {}).get("text", ""))
    if not parsed:
        return cors(jsonify(ok=True))

    now = datetime.now(IST).isoformat()

    with LOCK:
        for e in parsed:
            name, track, artist = e["name"], e["track"], e["artist"]
            key = f"{track} | {artist}"

            try:
                # Get the current user document
                user = friends_collection.find_one({"name": name})
                
                # Initialize fields if needed
                if not user:
                    user = {"name": name, "history": [], "last": None}
                
                last_track = user.get("last")
                
                # Only add if track changed
                if last_track != key:
                    image = get_album_image(track, artist)
                    friends_collection.update_one(
                        {"name": name},
                        {
                            "$set": {"last": key},
                            "$push": {
                                "history": {
                                    "track": track,
                                    "artist": artist,
                                    "played_at": now,
                                    "image": image
                                }
                            }
                        },
                        upsert=True
                    )
                    
                    print(f"🎧 {name} → {track}")
                    notify(name, track, artist)
                    
                    # Send to WhatsApp
                    try:
                        response = requests.post(
                            WHATSAPP_API_URL,
                            json={"name": name, "track": track, "artist": artist},
                            timeout=5
                        )
                        if response.ok:
                            print(f"✅ Sent to WhatsApp: {name}")
                        else:
                            print(f"⚠️  WhatsApp send failed: {response.status_code}")
                    except Exception as wa_error:
                        print(f"⚠️  Could not reach WhatsApp server: {wa_error}")
            except Exception as e:
                print(f"❌ Error updating {name}: {e}")

    return cors(jsonify(ok=True))



def open_logs():
    win = tk.Tk()
    win.title("Spotify Friend Tracker — Logs")
    win.geometry("700x400")

    box = ScrolledText(win, state="disabled")
    box.pack(fill="both", expand=True)

    def refresh():
        at_bottom = box.yview()[1] >= 0.99
        box.config(state="normal")
        box.delete("1.0", tk.END)
        box.insert(tk.END, "".join(LOG_BUFFER))
        box.config(state="disabled")
        if at_bottom:
            box.see(tk.END)
        win.after(1000, refresh)

    refresh()
    win.mainloop()

# ================= TRAY =================

def quit_app(icon, item):
    icon.stop()
    os._exit(0)

def tray_icon():
    try:
        image = Image.open(ICON_PATH) if os.path.exists(ICON_PATH) else None
    except Exception as e:
        print(f"⚠️  Could not load icon: {e}")
        image = None
    
    if image is None:
        print("ℹ️  Running without icon")
        try:
            # Try to create a simple default icon
            image = Image.new('RGB', (64, 64), color='green')
        except:
            print("⚠️  Could not create default icon, tray may not display properly")
            return
    
    pystray.Icon(
        "SpotifyFriendTracker",
        image,
        "Spotify Friend Tracker",
        (
            item("Open Logs", lambda: th.Thread(target=open_logs, daemon=True).start()),
            item("Quit", quit_app)
        )
    ).run()

# ================= START =================

def start_flask():
    app.run(FLASK_HOST, FLASK_PORT, threaded=True)

if __name__ == "__main__":
    print("🚀 Spotify Friend Tracker started")
    th.Thread(target=start_flask, daemon=True).start()
    tray_icon()

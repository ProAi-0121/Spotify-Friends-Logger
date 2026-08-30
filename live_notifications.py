import argparse
import os
import tempfile
import threading
import time
from io import BytesIO

import requests
from PIL import Image, ImageDraw

from win11toast import toast

# =========================================================
# INSTALL:
#
# pip install win11toast pillow requests
#
# =========================================================


class SpotifyNotifier:

    def __init__(self, api_url, interval=5):

        self.api_url = api_url
        self.interval = interval

        # user -> last track cache
        self.cache = {}

    # =========================================================
    # API
    # =========================================================

    def fetch_users(self):

        try:
            r = requests.get(
                self.api_url,
                timeout=10
            )

            r.raise_for_status()

            return r.json()

        except Exception as e:

            print("API Error:", e)

            return []

    # =========================================================
    # IMAGE HELPERS
    # =========================================================

    def download_image(self, url, suffix=".png"):

        if not url:
            return None

        try:

            r = requests.get(
                url,
                timeout=10
            )

            r.raise_for_status()

            path = tempfile.mktemp(suffix=suffix)

            with open(path, "wb") as f:
                f.write(r.content)

            return path

        except Exception:

            return None

    def create_rounded_pfp(self, image_url):

        if not image_url:
            return None

        try:

            r = requests.get(
                image_url,
                timeout=10
            )

            r.raise_for_status()

            img = Image.open(
                BytesIO(r.content)
            ).convert("RGBA")

            img = img.resize((256, 256))

            # Create circular mask
            mask = Image.new(
                "L",
                (256, 256),
                0
            )

            draw = ImageDraw.Draw(mask)

            draw.ellipse(
                (0, 0, 256, 256),
                fill=255
            )

            output = Image.new(
                "RGBA",
                (256, 256)
            )

            output.paste(
                img,
                (0, 0),
                mask
            )

            path = tempfile.mktemp(suffix=".png")

            output.save(path)

            return path

        except Exception as e:

            print("PFP Error:", e)

            return None

    # =========================================================
    # CLEANUP
    # =========================================================

    def cleanup_file(self, path, delay=60):

        def cleanup():

            try:

                time.sleep(delay)

                if path and os.path.exists(path):
                    os.remove(path)

            except Exception:
                pass

        threading.Thread(
            target=cleanup,
            daemon=True
        ).start()

    # =========================================================
    # NOTIFICATION
    # =========================================================

    def show_notification(
        self,
        username,
        track,
        artist,
        album_image=None,
        user_image=None
    ):

        album_path = self.download_image(
            album_image,
            suffix=".jpg"
        )

        pfp_path = self.create_rounded_pfp(
            user_image
        )

        try:

            # Compact modern Windows 11 style
            toast(
                title=username,

                body=(
                    f"🎵 {track}\n"
                    f"🎤 {artist}"
                ),

                icon=pfp_path or album_path,

                image=album_path,

                duration="short",

                audio="silent"
            )

        except Exception as e:

            print("Toast Error:", e)

        if album_path:
            self.cleanup_file(album_path)

        if pfp_path:
            self.cleanup_file(pfp_path)

    # =========================================================
    # LOOP
    # =========================================================

    def start(self):

        print("🚀 Spotify Notification System Started")

        while True:

            users = self.fetch_users()

            if not isinstance(users, list):

                time.sleep(self.interval)

                continue

            for user in users:

                uid = (
                    user.get("user_uri")
                    or user.get("name")
                    or "unknown"
                )

                track = (
                    user.get("last_track")
                    or ""
                )

                artist = (
                    user.get("last_artist")
                    or ""
                )

                username = (
                    user.get("name")
                    or "Spotify Friend"
                )

                # unique cache key
                key = f"{track}::{artist}"

                # skip empty
                if not track:
                    continue

                previous = self.cache.get(uid)

                # already shown
                if previous == key:
                    continue

                self.cache[uid] = key

                self.show_notification(
                    username=username,

                    track=track,

                    artist=artist,

                    album_image=(
                        user.get("last_track_image")
                        or user.get("track_image")
                    ),

                    user_image=user.get("user_image")
                )

            time.sleep(self.interval)


# =========================================================
# MAIN
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--api",
        default="http://localhost:5000/users"
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=5
    )

    args = parser.parse_args()

    notifier = SpotifyNotifier(
        api_url=args.api,
        interval=args.interval
    )

    notifier.start()


if __name__ == "__main__":
    main()
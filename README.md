# Spotify Friend Logger

A small Flask service that tracks what your Spotify friends are listening to. It signs into Spotify in a real browser, captures the auth/client tokens Spotify uses for its internal web API, and polls the private `presence-view/v1/buddylist` endpoint. Each friend's activity is then stored in MongoDB.

It ships with a companion desktop script (`live_notifications.py`) that turns new activity into Windows toasts.

## What it does

- Logs in to Spotify using a real Chrome session (undetected-chromedriver) so the app can reuse your account cookies.
- Captures the `Authorization` and `client-token` headers, then calls the buddylist endpoint to see what friends are playing.
- Saves each friend's current track / artist / album into MongoDB with a history.
- Exposes a small REST API so other tools can query activity, users, stats, or the live "now playing" snapshot.
- Optionally pushes new activity to open browser clients over Socket.IO.

## How it works

`main.py` is the backend. On startup it:

1. Loads config from `Documents/Spotify Friend Tracker/config.json`.
2. Connects to MongoDB (local by default, with an optional Atlas fallback).
3. Starts a Flask API on `0.0.0.0:5000`.
4. Runs a background loop that keeps the Spotify session fresh and fetches friend activity on an interval.

The browser is only launched when the session tokens need refreshing, then closed again. Data and config are stored relative to the current user's Documents folder, so it keeps working even when the script is run from a different directory.

## Requirements

- Python 3.9+
- Google Chrome installed
- MongoDB running locally (default), or a MongoDB Atlas URI
- A Spotify account that's logged in to `open.spotify.com`

## Setup

```bash
pip install -r requirements.txt

# copy the config template and set your values
cp .env.example .env
```

Then run:

```bash
python main.py
```

The first time (or whenever the session expires) a Chrome window opens so you can log in to Spotify. After that the app keeps the session alive and fetches friend activity automatically.

The server config (database, ports, fetch interval) lives in `.env`; the Spotify session itself is stored in `Documents/Spotify Friend Tracker/config.json`.

## Endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| POST   | `/ping`          | Health ping used by clients |
| POST   | `/spotify-data`  | Manual feed of friend data (older extension path) |
| GET    | `/activity`      | Latest activity across all friends |
| GET    | `/users`         | One entry per friend, with last track + play count |
| GET    | `/user/<user_uri>` | Paginated history for one friend |
| GET    | `/current`       | Live "now playing" snapshot |
| GET    | `/stats`         | Top tracks / artists and totals |
| GET    | `/health`        | Status incl. browser + login state |

## Live notifications

`live_notifications.py` is the desktop companion. It polls `/users` and shows a Windows toast when a friend starts something new:

```bash
python live_notifications.py --api http://localhost:5000/users --interval 5
```

## Project layout

```
main.py                  Flask backend + Spotify session/fetch loop
live_notifications.py    Optional desktop toast notifier
requirements.txt
icon.ico
OLD EXTENSION METHOD/    Early prototype (browser-extension + WhatsApp bridge)
```

## Troubleshooting

- Nothing saving to the database → make sure MongoDB is running and check `.env` (a missing `MONGO_URI_ATLAS` just disables the hosted fallback).
- Tokens keep expiring → the capture needs a working Chrome, installed to a usual location. Check the logs in `Documents/Spotify Friend Tracker/logs.txt`.
- Toast wrapper needs Windows 11 → `win11toast` is only used by `live_notifications.py`, not the server.
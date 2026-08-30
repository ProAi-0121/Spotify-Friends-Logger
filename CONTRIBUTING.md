# Contributing

Thanks for taking a look! Small project, loose rules.

## Bugs and ideas

Open an issue describing what you're trying to do. If it's a bug, include the log lines
from `Documents/Spotify Friend Tracker/logs.txt` if you can.

## Pull requests

1. Fork the repo and create a branch.
2. Make your change. Keep it focused — no unrelated refactoring.
3. Run a syntax check on the Python files you touched:

   ```bash
   python -m py_compile main.py live_notifications.py
   ```

4. Open a PR with a short description of what and why.

The main things that matter: don't break the existing fetch loop, don't commit local
config (`.env`, `config.json`), and keep `backup/` out of PRs.
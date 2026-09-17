English | [简体中文](README_ZH.md)

# 3.jetbra.in

A static mirror of [3.jetbra.in](https://3.jetbra.in/), including its page assets and ZIP download. Automatically synced and deployed to GitHub Pages.

## Local Usage

Requires Python 3.10+.

```sh
python -m pip install -r requirements-dev.txt
python scripts/mirror.py
python scripts/mirror.py --verify-only
```

Open `public/index.html` to preview. Run tests with `python -m pytest -q`.

## Deploy

1. Push the project to the repository's `main` branch.
2. Set **Settings > Pages > Source** to **GitHub Actions**.
3. Run **Actions > Sync and deploy mirror > Run workflow**.

No additional secrets are required. Repository policies must allow Actions to commit to `main` and deploy to Pages.

The workflow syncs daily at **00:00 UTC / 08:00 Beijing time**, on code pushes to `main`, or manually. Failed downloads preserve the previous snapshot.

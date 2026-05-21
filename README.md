# Shop My Screen

Detect clothing in TV/film playing on your screen and find places to buy it.

## Quickstart

```bash
cd ~/Documents/valstb/shopmyscreen
uv run python main.py
```

`uv` installs everything from `pyproject.toml` into `.venv` on first run.

## First-run setup (macOS)

On the first scan, macOS will prompt for **Screen Recording** permission. Grant it (System Settings → Privacy & Security → Screen Recording → enable for Terminal / your IDE / Python), then restart the app.

## Detection modes

Pick one in the **Detection Mode** panel:

| Mode | Cost | First scan | Notes |
|------|------|-----------|-------|
| **Claude Vision** | API credits | instant | Most accurate. Needs `ANTHROPIC_API_KEY` set in env. |
| **BLIP Local** | free | ~1 GB download | Image captioning. Good descriptions of clothing. |
| **FashionCLIP** | free | ~600 MB download | Fashion-specific classifier. Crisp category labels. |

Models cache to `~/.cache/huggingface/` after first download — subsequent scans are fast.

## Using the app

1. Play a TV show / movie / YouTube video on screen
2. Click **📸 SCAN NOW**
3. Wait for results (instant for Claude, 2–10s for local modes)
4. Each detected item gets a card with shop buttons:
   - **🔍 Google Lens** — copies screenshot to clipboard, opens Lens (paste with ⌘V to identify exact brand/product)
   - **Google Shopping / Amazon / ASOS / Shopstyle** — opens a search for that item in your browser

Toggle **AUTO** to scan repeatedly on an interval (default 8s).

## Environment variables

Set in `~/.zshrc` for global use:

```bash
export HF_TOKEN=hf_...           # optional, suppresses HF rate-limit warning
export ANTHROPIC_API_KEY=sk-...  # only needed for Claude Vision mode
```

## Troubleshooting

- **"No clothing detected" every scan** → make sure something with visible characters is on screen, not just the app window or terminal
- **Captured a blank/black screenshot** → Screen Recording permission not granted (see First-run setup)
- **Buttons appear greyed out** → already fixed via Label workaround; if it returns, it's a Tk/macOS rendering bug
- **Google Lens button does nothing** → make sure you're on macOS (the clipboard copy uses `osascript`)
- **Scan button stuck on "Scanning…"** → first-time local model load can take 5–30s; check the status bar and progress bar for what's happening

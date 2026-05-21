#!/usr/bin/env python3
"""
Shop My Screen
Detects clothing in TV/film on your screen and provides clickable links to buy.

Three detection modes:
  Claude Vision  — most accurate, requires ANTHROPIC_API_KEY (~API cost)
  BLIP Local     — free, local, good descriptions (downloads ~1 GB model 1st run)
  FashionCLIP    — free, local, category-focused  (downloads ~600 MB model 1st run)

Requirements: pip install -r requirements.txt
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
import webbrowser

from detector import (
    BLIPClothingDetector,
    ClothingDetector,
    FashionCLIPDetector,
    capture_screen_pil,
    item_emoji,
)
from shopper import get_links, open_google_lens

# ── Palette ────────────────────────────────────────────────────────────────
BG       = "#0f0f1a"
PANEL_BG = "#1a1a2e"
CARD_BG  = "#16213e"
ACCENT   = "#e94560"
ACCENT2  = "#0f3460"
GREEN    = "#4ecca3"
GOLD     = "#f5a623"
WHITE    = "#e8e0f0"
MUTED    = "#6b6b8a"
BORDER   = "#1e2a4a"

STORE_STYLES = {
    "Google Shopping": ("#4285f4", WHITE),
    "Amazon":          ("#ff9900", "#111"),
    "ASOS":            ("#2d2d2d", WHITE),
    "Shopstyle":       ("#9b59b6", WHITE),
}

MODES = [
    ("claude",      "Claude Vision",  "most accurate · API cost"),
    ("blip",        "BLIP Local",     "free · image captions · ~1 GB 1st run"),
    ("fashionclip", "FashionCLIP",    "free · categories · ~600 MB 1st run"),
]

DEFAULT_INTERVAL = 8


class ShopMyScreen:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("SHOP MY SCREEN")
        self.root.configure(bg=BG)
        self.root.minsize(700, 580)

        # One persistent instance per backend (lazy-loads on first use)
        self._detectors = {
            "claude":      ClothingDetector(),
            "blip":        BLIPClothingDetector(),
            "fashionclip": FashionCLIPDetector(),
        }

        self.status_q: queue.Queue = queue.Queue()
        self.scanning = False
        self.auto_scan = False
        self.auto_after_id = None
        self._thumb_photo = None
        self._last_img = None

        self._build_ui()
        self._poll_queue()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg=BG)
        hdr.pack(fill="x", padx=28, pady=(22, 2))
        tk.Label(hdr, text="SHOP MY SCREEN",
                 font=("Georgia", 26, "bold"), fg=ACCENT, bg=BG).pack(side="left")

        tk.Label(self.root, text="Detect clothing on screen — click to shop",
                 font=("Helvetica", 11), fg=MUTED, bg=BG).pack()

        # Mode selector
        mode_f = tk.LabelFrame(
            self.root, text="  Detection Mode  ",
            bg=PANEL_BG, fg=MUTED, font=("Courier", 9, "bold"),
            relief="flat", bd=1, labelanchor="n",
        )
        mode_f.pack(pady=(14, 4), padx=28, fill="x")

        # Custom Label-based radio buttons (macOS Tk's native Radiobutton
        # often fails to render the selection indicator).
        self.mode_var = tk.StringVar(value="claude")
        self._mode_indicators: dict[str, tk.Label] = {}
        self._mode_text_labels: dict[str, tk.Label] = {}

        inner = tk.Frame(mode_f, bg=PANEL_BG)
        inner.pack(pady=8, padx=14, fill="x")

        for value, label, note in MODES:
            row = tk.Frame(inner, bg=PANEL_BG, cursor="hand2")
            row.pack(anchor="w", pady=3, fill="x")

            dot = tk.Label(row, text="○", bg=PANEL_BG, fg=MUTED,
                           font=("Courier", 13, "bold"))
            dot.pack(side="left", padx=(0, 8))
            self._mode_indicators[value] = dot

            text = tk.Label(row, text=label, bg=PANEL_BG, fg=WHITE,
                            font=("Courier", 11), cursor="hand2")
            text.pack(side="left")
            self._mode_text_labels[value] = text

            note_lbl = tk.Label(row, text=f"  ({note})", bg=PANEL_BG,
                                fg=MUTED, font=("Courier", 9), cursor="hand2")
            note_lbl.pack(side="left")

            for widget in (row, dot, text, note_lbl):
                widget.bind("<Button-1>", lambda e, v=value: self._select_mode(v))

        self._refresh_mode_indicators()

        # Controls
        ctrl = tk.Frame(self.root, bg=BG)
        ctrl.pack(pady=12, padx=28)

        self.scan_btn = tk.Button(
            ctrl, text="📸  SCAN NOW",
            command=self._scan_once,
            bg=ACCENT, fg=WHITE, font=("Courier", 13, "bold"),
            relief="flat", padx=18, pady=8, cursor="hand2",
            activebackground="#ff6b80", activeforeground=WHITE,
        )
        self.scan_btn.pack(side="left", padx=(0, 12))

        self.auto_btn = tk.Button(
            ctrl, text="⟳  AUTO: OFF",
            command=self._toggle_auto,
            bg=PANEL_BG, fg=MUTED, font=("Courier", 11, "bold"),
            relief="flat", padx=12, pady=8, cursor="hand2",
            activebackground=ACCENT2, activeforeground=WHITE,
        )
        self.auto_btn.pack(side="left", padx=(0, 8))

        tk.Label(ctrl, text="every", fg=MUTED, bg=BG,
                 font=("Courier", 10)).pack(side="left")
        self.interval_var = tk.IntVar(value=DEFAULT_INTERVAL)
        tk.Spinbox(ctrl, from_=3, to=120, textvariable=self.interval_var,
                   width=3, font=("Courier", 11),
                   bg=PANEL_BG, fg=GOLD, buttonbackground=PANEL_BG,
                   relief="flat").pack(side="left", padx=(4, 2))
        tk.Label(ctrl, text="sec", fg=MUTED, bg=BG,
                 font=("Courier", 10)).pack(side="left")

        # Status
        self.status_lbl = tk.Label(
            self.root,
            text="Ready — choose a mode and click Scan Now",
            font=("Courier", 10), fg=MUTED, bg=BG,
        )
        self.status_lbl.pack(pady=(0, 4))

        # Model progress bar (hidden until a local mode is used)
        self._prog_frame = tk.Frame(self.root, bg=BG)
        # not packed yet — shown on demand

        style = ttk.Style()
        style.theme_use("default")
        style.configure("SMS.Horizontal.TProgressbar",
                        troughcolor=PANEL_BG, background=GREEN,
                        bordercolor=BG, lightcolor=GREEN, darkcolor=GREEN)

        prog_inner = tk.Frame(self._prog_frame, bg=BG)
        prog_inner.pack()

        tk.Label(prog_inner, text="Model  ", font=("Courier", 9), fg=MUTED, bg=BG).pack(side="left")
        self._prog_bar = ttk.Progressbar(
            prog_inner, style="SMS.Horizontal.TProgressbar",
            orient="horizontal", length=260, mode="determinate",
        )
        self._prog_bar.pack(side="left")
        self._prog_lbl = tk.Label(prog_inner, text="", font=("Courier", 9), fg=GREEN, bg=BG, width=18)
        self._prog_lbl.pack(side="left", padx=(8, 0))

        # Scrollable results
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(padx=28, pady=(0, 22), fill="both", expand=True)

        tk.Label(outer, text="DETECTED ITEMS",
                 fg=MUTED, bg=BG, font=("Courier", 9, "bold")).pack(anchor="w", pady=(0, 6))

        wrap = tk.Frame(outer, bg=BG)
        wrap.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, width=640, height=340)
        vsb = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview,
                           bg=PANEL_BG, troughcolor=BG)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.cards_frame = tk.Frame(self.canvas, bg=BG)
        self._win_id = self.canvas.create_window((0, 0), window=self.cards_frame, anchor="nw")

        self.cards_frame.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(
            self._win_id, width=e.width))
        self.canvas.bind_all("<MouseWheel>",
                             lambda e: self.canvas.yview_scroll(int(-1 * e.delta / 120), "units"))

        self._show_placeholder()

    # ── Placeholder & cards ────────────────────────────────────────────────

    def _show_placeholder(self):
        tk.Label(
            self.cards_frame,
            text="No scan yet.\n\nChoose a detection mode then click 'Scan Now'\nto find clothing to buy.",
            font=("Courier", 11), fg=MUTED, bg=BG, justify="center",
        ).pack(pady=60)

    def _clear_cards(self):
        for w in self.cards_frame.winfo_children():
            w.destroy()

    # ── Scan logic ─────────────────────────────────────────────────────────

    def _select_mode(self, value: str):
        """Handle custom radio-button click."""
        self.mode_var.set(value)
        self._refresh_mode_indicators()
        label = {v: l for v, l, _ in MODES}[value]
        self.status_lbl.configure(text=f"{label} selected — click Scan Now", fg=MUTED)

    def _refresh_mode_indicators(self):
        current = self.mode_var.get()
        for value, dot in self._mode_indicators.items():
            if value == current:
                dot.configure(text="●", fg=ACCENT)
                self._mode_text_labels[value].configure(fg=ACCENT)
            else:
                dot.configure(text="○", fg=MUTED)
                self._mode_text_labels[value].configure(fg=WHITE)

    def _scan_once(self):
        if self.scanning:
            return
        self.scanning = True
        mode = self.mode_var.get()
        label = {v: l for v, l, _ in MODES}[mode]
        self.scan_btn.configure(text="⏳  Scanning…", state="disabled", bg=MUTED)
        self.status_lbl.configure(text=f"Starting {label} scan…", fg=GOLD)
        threading.Thread(target=self._do_scan, args=(mode,), daemon=True).start()

    def _do_scan(self, mode: str):
        try:
            label = {
                "claude":      "Claude Vision",
                "blip":        "BLIP (local)",
                "fashionclip": "FashionCLIP (local)",
            }[mode]

            img = capture_screen_pil()
            detector = self._detectors[mode]

            if mode in ("blip", "fashionclip"):
                if detector.is_ready():
                    # Already loaded in memory
                    self.status_q.put(("model_progress", 1.0))
                    self.status_q.put(("status", (f"Running {label}…", GOLD)))
                else:
                    # Needs to load — show indeterminate bar with correct message
                    self.status_q.put(("model_progress", "indeterminate"))
                    if detector.is_cached():
                        self.status_q.put(("status",
                            (f"Loading {label} model into memory…", GOLD)))
                    else:
                        self.status_q.put(("status",
                            (f"Downloading {label} model — one-time, may take a few minutes…", GOLD)))
                    # Explicitly load so the indeterminate bar shows during the wait
                    detector._load()
                    self.status_q.put(("model_progress", 1.0))
                    self.status_q.put(("status", (f"Running {label}…", GOLD)))
            else:
                self.status_q.put(("status", (f"Running {label}…", GOLD)))

            items = detector.detect(img)

            self.status_q.put(("results", (img, items, mode)))
        except Exception as e:
            self.status_q.put(("model_progress", None))
            self.status_q.put(("error", str(e)))

    def _toggle_auto(self):
        self.auto_scan = not self.auto_scan
        if self.auto_scan:
            self.auto_btn.configure(text="⟳  AUTO: ON", fg=GREEN, bg=ACCENT2)
            self._schedule_auto()
        else:
            self.auto_btn.configure(text="⟳  AUTO: OFF", fg=MUTED, bg=PANEL_BG)
            if self.auto_after_id:
                self.root.after_cancel(self.auto_after_id)
                self.auto_after_id = None

    def _schedule_auto(self):
        if not self.auto_scan:
            return
        self._scan_once()
        ms = self.interval_var.get() * 1000
        self.auto_after_id = self.root.after(ms, self._schedule_auto)

    # ── Queue ──────────────────────────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                kind, value = self.status_q.get_nowait()
                if kind == "status":
                    text, color = value
                    self.status_lbl.configure(text=text, fg=color)
                elif kind == "results":
                    img, items, mode = value
                    self._show_results(img, items, mode)
                    self.scanning = False
                    self.scan_btn.configure(text="📸  SCAN NOW", state="normal", bg=ACCENT)
                elif kind == "model_progress":
                    self._update_progress(value)
                elif kind == "error":
                    self.status_lbl.configure(text=f"Error: {value}", fg="#ff4444")
                    self.scanning = False
                    self.scan_btn.configure(text="📸  SCAN NOW", state="normal", bg=ACCENT)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    # ── Progress bar ───────────────────────────────────────────────────────

    def _update_progress(self, value):
        """value: None=hide, 'indeterminate'=bouncing, 0.0-1.0=fill."""
        if value is None:
            self._prog_bar.stop()
            self._prog_frame.pack_forget()
            return

        if not self._prog_frame.winfo_ismapped():
            self._prog_frame.pack(pady=(0, 8))

        if value == "indeterminate":
            self._prog_bar.configure(mode="indeterminate")
            self._prog_bar.start(12)
            self._prog_lbl.configure(text="downloading…", fg=GOLD)
        else:
            self._prog_bar.stop()
            self._prog_bar.configure(mode="determinate")
            self._prog_bar["value"] = value * 100
            if value >= 1.0:
                self._prog_lbl.configure(text="100% — ready", fg=GREEN)
            else:
                self._prog_lbl.configure(text=f"{value*100:.0f}%", fg=GOLD)

    # ── Results ────────────────────────────────────────────────────────────

    def _show_results(self, img, items, mode: str):
        self._last_img = img
        self._clear_cards()

        from PIL import ImageTk
        thumb = img.copy()
        thumb.thumbnail((200, 113))
        self._thumb_photo = ImageTk.PhotoImage(thumb)

        hdr = tk.Frame(self.cards_frame, bg=BG)
        hdr.pack(fill="x", pady=(0, 10))
        tk.Label(hdr, image=self._thumb_photo, bg=BG).pack(side="left")

        info = tk.Frame(hdr, bg=BG)
        info.pack(side="left", padx=14)

        ts = time.strftime("%H:%M:%S")
        mode_labels = {
            "claude": "Claude Vision", "blip": "BLIP Local",
            "fashionclip": "FashionCLIP",
        }
        tk.Label(info, text=f"Scanned at {ts}  ·  {mode_labels.get(mode, mode)}",
                 font=("Courier", 10), fg=MUTED, bg=BG).pack(anchor="w")

        if not items:
            tk.Label(
                info,
                text="No clothing detected.\nMake sure a TV/film frame\nwith visible characters is on screen.",
                font=("Courier", 11), fg=MUTED, bg=BG, justify="left",
            ).pack(anchor="w", pady=6)
            self.status_lbl.configure(
                text="No clothing detected — try a different frame", fg=MUTED)
            return

        n = len(items)
        tk.Label(info, text=f"{n} item{'s' if n != 1 else ''} detected",
                 font=("Courier", 13, "bold"), fg=GREEN, bg=BG).pack(anchor="w")
        tk.Label(info, text="Click a store button to open search in browser",
                 font=("Courier", 10), fg=MUTED, bg=BG).pack(anchor="w")
        self.status_lbl.configure(
            text=f"Found {n} item{'s' if n != 1 else ''} — click a store to shop",
            fg=GREEN,
        )

        tk.Frame(self.cards_frame, bg=BORDER, height=1).pack(fill="x", pady=(4, 8))
        for item in items:
            self._make_card(item)

    def _make_card(self, item: dict):
        card = tk.Frame(self.cards_frame, bg=CARD_BG,
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=5, padx=2, ipady=4)

        body = tk.Frame(card, bg=CARD_BG)
        body.pack(fill="x", padx=14, pady=8)

        item_type = item.get("item_type", "clothing item")
        color     = item.get("color", "")
        style     = item.get("style", "")
        character = item.get("character", "")
        query     = item.get("search_query", item_type)
        emoji     = item_emoji(item_type)

        # Title row
        title_row = tk.Frame(body, bg=CARD_BG)
        title_row.pack(anchor="w", fill="x")
        tk.Label(title_row, text=f"{emoji}  {item_type.title()}",
                 font=("Georgia", 13, "bold"), fg=WHITE, bg=CARD_BG).pack(side="left")
        if color:
            tk.Label(title_row, text=f"  · {color}",
                     font=("Courier", 10), fg=GOLD, bg=CARD_BG).pack(side="left")

        if character:
            tk.Label(body, text=f"worn by: {character}",
                     font=("Courier", 9), fg=MUTED, bg=CARD_BG).pack(anchor="w")

        if style and style != "fashion-clip":
            tk.Label(body, text=style,
                     font=("Courier", 10, "italic"), fg=MUTED, bg=CARD_BG).pack(
                anchor="w", pady=(1, 6))

        # Google Lens button
        lens_row = tk.Frame(body, bg=CARD_BG)
        lens_row.pack(anchor="w", pady=(6, 2))

        def _lens_click(e=None):
            if self._last_img:
                threading.Thread(
                    target=open_google_lens, args=(self._last_img,), daemon=True
                ).start()

        def _lens_click_with_hint(e=None):
            _lens_click()
            self.status_lbl.configure(
                text="Screenshot copied to clipboard — paste into Lens with ⌘V",
                fg=GREEN,
            )

        lens_lbl = tk.Label(
            lens_row, text="🔍  Google Lens  (⌘V to paste)",
            bg="#1a73e8", fg=WHITE,
            font=("Courier", 9, "bold"),
            padx=10, pady=5, cursor="hand2",
        )
        lens_lbl.pack(side="left")
        lens_lbl.bind("<Button-1>", _lens_click_with_hint)
        lens_lbl.bind("<Enter>", lambda e, w=lens_lbl: w.configure(bg="#1558b0"))
        lens_lbl.bind("<Leave>", lambda e, w=lens_lbl: w.configure(bg="#1a73e8"))

        tk.Label(lens_row, text="  copies screenshot · opens Lens · paste to search",
                 font=("Courier", 8), fg=MUTED, bg=CARD_BG).pack(side="left")

        # Shop buttons — use Label+binding so macOS honours custom background colours
        btn_row = tk.Frame(body, bg=CARD_BG)
        btn_row.pack(anchor="w", pady=(4, 0))
        for store_name, url in get_links(query):
            bg_c, fg_c = STORE_STYLES.get(store_name, (PANEL_BG, WHITE))
            lbl = tk.Label(
                btn_row, text=store_name,
                bg=bg_c, fg=fg_c,
                font=("Courier", 9, "bold"),
                padx=10, pady=5, cursor="hand2",
            )
            lbl.pack(side="left", padx=(0, 6))
            lbl.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
            lbl.bind("<Enter>",    lambda e, w=lbl: w.configure(bg=ACCENT, fg=WHITE))
            lbl.bind("<Leave>",    lambda e, w=lbl, b=bg_c, f=fg_c: w.configure(bg=b, fg=f))


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    ShopMyScreen(root)

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    root.mainloop()


if __name__ == "__main__":
    main()

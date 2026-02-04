import threading
import time
from dataclasses import dataclass
from datetime import datetime
import math
import hashlib

import requests
import tkinter as tk
from tkinter import ttk, messagebox

# Windows sound (works on Windows)
try:
    import winsound
    HAS_WINSOUND = True
except Exception:
    HAS_WINSOUND = False


# =========================
# DEFAULTS (user can change in UI)
# =========================
DEFAULT_PAIR = "ETHUSDC"
DEFAULT_SIDE = "SHORT"     # SHORT or LONG
DEFAULT_LEVERAGE = 1.0
DEFAULT_MAX_SIZE = 2.0     # in base asset units (e.g. ETH)
DEFAULT_RELOAD_RATE = 1.5  # geometric ratio between levels (martingale intensity)

# Bollinger parameters
BB_PERIOD = 20
BB_STD = 2.0

# Timeframes from smallest to bigger (Binance intervals)
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]

# Polling
POLL_SEC = 1.0

BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol={sym}"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines?symbol={sym}&interval={interval}&limit={limit}"


# =========================
# LIVE PRICE STATE
# =========================
@dataclass
class PriceState:
    ok: bool = False
    symbol: str = DEFAULT_PAIR
    price: float = 0.0
    ts: float = 0.0


class Shared:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = PriceState()


shared = Shared()


def fetch_price(symbol: str) -> float:
    url = BINANCE_PRICE_URL.format(sym=symbol.upper())
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    data = r.json()
    return float(data["price"])


def price_loop(stop_evt: threading.Event, symbol_getter):
    while not stop_evt.is_set():
        sym = (symbol_getter().strip().upper() or DEFAULT_PAIR)
        try:
            px = fetch_price(sym)
            now = time.time()
            with shared.lock:
                shared.state = PriceState(ok=True, symbol=sym, price=px, ts=now)
        except Exception:
            with shared.lock:
                shared.state.ok = False
                shared.state.symbol = sym
        stop_evt.wait(POLL_SEC)


# =========================
# Bollinger + ladder generation
# =========================
def fetch_klines_closes(symbol: str, interval: str, limit: int = 200):
    url = BINANCE_KLINES_URL.format(sym=symbol.upper(), interval=interval, limit=limit)
    r = requests.get(url, timeout=7)
    r.raise_for_status()
    data = r.json()
    closes = [float(k[4]) for k in data]  # close
    return closes


def bollinger_bands(closes, period=BB_PERIOD, num_std=BB_STD):
    if len(closes) < period:
        return 0.0, 0.0, 0.0
    window = closes[-period:]
    mean = sum(window) / period
    var = sum((x - mean) ** 2 for x in window) / period
    std = math.sqrt(var)
    upper = mean + num_std * std
    lower = mean - num_std * std
    return lower, mean, upper


def geometric_qtys(total, n, r=1.5, min_first_frac=0.06):
    if n <= 0:
        return []
    base0 = max(1e-9, min_first_frac)
    base = [base0 * (r ** i) for i in range(n)]
    s = sum(base)
    if s <= 0:
        return [total / n] * n
    scale = total / s
    return [q * scale for q in base]


def build_bollinger_ladder(symbol: str, current_price: float, side: str, max_size: float, reload_rate: float):
    raw = []
    for tf in TIMEFRAMES:
        closes = fetch_klines_closes(symbol, tf, limit=max(200, BB_PERIOD + 5))
        lower, mid, upper = bollinger_bands(closes, BB_PERIOD, BB_STD)
        lvl = upper if side == "SHORT" else lower
        if lvl > 0:
            raw.append((tf, lvl))

    if side == "SHORT":
        raw = [(tf, p) for tf, p in raw if p >= current_price]
        levels = []
        last = -1.0
        for tf, p in raw:
            if p > last:
                levels.append((tf, p))
                last = p
    else:
        raw = [(tf, p) for tf, p in raw if p <= current_price]
        levels = []
        last = float("inf")
        for tf, p in raw:
            if p < last:
                levels.append((tf, p))
                last = p

    if not levels:
        return [], f"Nessun livello Bollinger {'Upper' if side=='SHORT' else 'Lower'} valido rispetto al prezzo attuale."

    qtys = geometric_qtys(max_size, len(levels), r=max(1.0, reload_rate), min_first_frac=0.06)

    ladder = []
    for (tf, p), q in zip(levels, qtys):
        ladder.append((p, q, tf))
    return ladder, f"Generati {len(ladder)} livelli da Bollinger ({'Upper' if side=='SHORT' else 'Lower'}) su TF: {', '.join(tf for tf, _ in levels)}"


# =========================
# Ladder parsing / helpers
# =========================
def parse_ladder_text(text: str, max_size: float):
    rows = []
    for raw in text.splitlines():
        original = raw.rstrip("\n")
        line = original.strip()
        if not line or line.startswith("#"):
            continue

        line = line.split("#", 1)[0].strip()
        if not line:
            continue

        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 2:
            raise ValueError(f"Riga non valida: '{original}' (usa: prezzo, qty_asset)")

        p = float(parts[0])
        q = float(parts[1])
        if p <= 0 or q <= 0:
            raise ValueError(f"Valori non validi: '{original}' (prezzo>0, qty>0)")

        rows.append((p, q))

    rows.sort(key=lambda x: x[0])

    out = []
    used = 0.0
    for p, q in rows:
        if used >= max_size:
            break
        take = min(q, max_size - used)
        if take > 0:
            out.append((p, take))
            used += take

    return out


def weighted_avg(entries):
    qty = sum(q for _, q in entries)
    if qty <= 0:
        return 0.0, 0.0
    notional = sum(p * q for p, q in entries)
    return notional / qty, qty


def level_id(price: float, qty: float) -> str:
    s = f"{price:.8f}|{qty:.8f}"
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def unrealized_pnl_usdc(side: str, avg_entry: float, current_price: float, qty: float) -> float:
    if qty <= 0 or avg_entry <= 0 or current_price <= 0:
        return 0.0
    if side == "LONG":
        return (current_price - avg_entry) * qty
    return (avg_entry - current_price) * qty


# =========================
# UI
# =========================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Hedging Operational Rig (H.O.R.) - Bollinger Ladder Dashboard — LONG/SHORT + Dark Mode")
        self.geometry("1260x880")
        self.minsize(1080, 760)

        self.style = ttk.Style(self)

        self.stop_evt = threading.Event()

        # Controls
        self.symbol_var = tk.StringVar(value=DEFAULT_PAIR)
        self.side_var = tk.StringVar(value=DEFAULT_SIDE)
        self.leverage_var = tk.DoubleVar(value=DEFAULT_LEVERAGE)
        self.max_size_var = tk.DoubleVar(value=DEFAULT_MAX_SIZE)
        self.reload_rate_var = tk.DoubleVar(value=DEFAULT_RELOAD_RATE)

        # Alerts
        self.alerts_enabled_var = tk.BooleanVar(value=True)
        self.sound_enabled_var = tk.BooleanVar(value=True)
        self.popup_enabled_var = tk.BooleanVar(value=True)

        # Dark mode
        self.dark_mode_var = tk.BooleanVar(value=False)

        # Live labels
        self.live_status_var = tk.StringVar(value="Status: —")
        self.live_price_var = tk.StringVar(value="Prezzo: —")
        self.live_time_var = tk.StringVar(value="Time: —")

        # Results
        self.size_var = tk.StringVar(value="Size filled: —")
        self.avg_var = tk.StringVar(value="Average entry: —")
        self.pnl_var = tk.StringVar(value="PnL (real-time): —")
        self.next_var = tk.StringVar(value="Next level: —")
        self.note_var = tk.StringVar(value="")

        # State
        self.applied_ladder = []
        self.filled_ids_latched = set()
        self.last_price = None

        # avg-entry touch alert (debounce)
        self.last_avg_touch_ts = 0.0
        self.AVG_TOUCH_COOLDOWN_SEC = 30.0

        self._build_ui()
        self.apply_theme()  # init theme

        t = threading.Thread(target=price_loop, args=(self.stop_evt, self._get_symbol_threadsafe), daemon=True)
        t.start()

        self.after(200, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.stop_evt.set()
        self.destroy()

    def _get_symbol_threadsafe(self):
        try:
            return self.symbol_var.get()
        except Exception:
            return DEFAULT_PAIR

    # -------- THEME --------
    def apply_theme(self):
        """
        Simple dark mode using ttk style + manual widget colors for Text.
        Works with standard Tk/ttk (no extra libraries).
        """
        dark = bool(self.dark_mode_var.get())

        # Choose a base theme that supports styling
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        if dark:
            bg = "#1e1e1e"
            fg = "#e6e6e6"
            card = "#252526"
            border = "#3c3c3c"
            sel = "#2d2d30"
        else:
            bg = "#f3f3f3"
            fg = "#111111"
            card = "#ffffff"
            border = "#d0d0d0"
            sel = "#eaeaea"

        self.configure(bg=bg)

        # General ttk styles
        self.style.configure(".", background=bg, foreground=fg)
        self.style.configure("TFrame", background=bg)
        self.style.configure("TLabel", background=bg, foreground=fg)
        self.style.configure("TLabelframe", background=bg, foreground=fg)
        self.style.configure("TLabelframe.Label", background=bg, foreground=fg)

        self.style.configure("TButton", background=card, foreground=fg, bordercolor=border)
        self.style.map("TButton", background=[("active", sel)])

        self.style.configure("TCheckbutton", background=bg, foreground=fg)
        self.style.map("TCheckbutton", background=[("active", bg)])

        self.style.configure("TCombobox", fieldbackground=card, background=card, foreground=fg)
        self.style.map("TCombobox", fieldbackground=[("readonly", card)])

        self.style.configure("TEntry", fieldbackground=card, foreground=fg)
        self.style.configure("TSpinbox", fieldbackground=card, foreground=fg)

        # Treeview styling
        self.style.configure(
            "Treeview",
            background=card,
            fieldbackground=card,
            foreground=fg,
            bordercolor=border,
            rowheight=24,
        )
        self.style.configure("Treeview.Heading", background=sel, foreground=fg)
        self.style.map("Treeview", background=[("selected", "#3a3d41" if dark else "#cfe8ff")])

        # Manual: Text widget (not ttk)
        self.ladder_text.configure(
            background=card,
            foreground=fg,
            insertbackground=fg,
            selectbackground="#3a3d41" if dark else "#cfe8ff",
            selectforeground=fg if dark else "#000000",
        )

    # -------- UI BUILD --------
    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        # TOP CONTROLS
        top = ttk.LabelFrame(root, text="Impostazioni", padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="PAIR:").grid(row=0, column=0, sticky="w")
        pair_box = ttk.Combobox(
            top,
            textvariable=self.symbol_var,
            values=["ETHUSDC", "BTCUSDC", "SOLUSDC", "BNBUSDC", "ARBUSDC", "OPUSDC"],
            width=12,
            state="readonly",
        )
        pair_box.grid(row=0, column=1, sticky="w", padx=(6, 16))

        ttk.Label(top, text="Side:").grid(row=0, column=2, sticky="w")
        side_box = ttk.Combobox(top, textvariable=self.side_var, values=["SHORT", "LONG"], width=8, state="readonly")
        side_box.grid(row=0, column=3, sticky="w", padx=(6, 16))

        ttk.Label(top, text="Leva (x):").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(top, from_=1.0, to=125.0, increment=0.5, textvariable=self.leverage_var, width=8)\
            .grid(row=0, column=5, sticky="w", padx=(6, 16))

        ttk.Label(top, text="Max size (asset):").grid(row=0, column=6, sticky="w")
        ttk.Entry(top, textvariable=self.max_size_var, width=10).grid(row=0, column=7, sticky="w", padx=(6, 16))

        ttk.Label(top, text="Tasso ricarico (r):").grid(row=0, column=8, sticky="w")
        ttk.Spinbox(top, from_=1.0, to=50.0, increment=0.5, textvariable=self.reload_rate_var, width=8)\
            .grid(row=0, column=9, sticky="w", padx=(6, 16))

        toggles = ttk.Frame(top)
        toggles.grid(row=0, column=10, sticky="e")
        ttk.Checkbutton(toggles, text="Alerts", variable=self.alerts_enabled_var).pack(side=tk.LEFT, padx=6)
        ttk.Checkbutton(toggles, text="Sound", variable=self.sound_enabled_var).pack(side=tk.LEFT, padx=6)
        ttk.Checkbutton(toggles, text="Popup", variable=self.popup_enabled_var).pack(side=tk.LEFT, padx=6)
        ttk.Checkbutton(toggles, text="Dark mode", variable=self.dark_mode_var, command=self.apply_theme).pack(side=tk.LEFT, padx=6)

        top.grid_columnconfigure(10, weight=1)

        # LIVE HEADER
        header = ttk.Frame(root)
        header.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(header, textvariable=self.live_status_var, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.live_time_var).grid(row=0, column=1, sticky="w", padx=18)
        ttk.Label(header, textvariable=self.live_price_var, font=("Segoe UI", 18, "bold")).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        header.grid_columnconfigure(0, weight=1)

        # MAIN SPLIT
        mid = ttk.Frame(root)
        mid.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        left = ttk.LabelFrame(mid, text="Ladder (prezzo, qty asset)", padding=10)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        right = ttk.LabelFrame(mid, text="Risultati", padding=10)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Ladder text (tk.Text)
        self.ladder_text = tk.Text(left, height=18, width=58)
        self.ladder_text.pack(fill=tk.BOTH, expand=True)
        self.ladder_text.insert(
            "1.0",
            "# Premi 'Genera da Bollinger' per calcolare i livelli automatici\n"
            "# formato: prezzo, qty_asset  (es. ETH)\n"
        )

        btns = ttk.Frame(left)
        btns.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btns, text="Genera da Bollinger", command=self.generate_from_bollinger).pack(side=tk.LEFT)
        ttk.Button(btns, text="Applica Ladder", command=self.apply_ladder).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="Pulisci tutto", command=self.clear_all).pack(side=tk.LEFT)

        ttk.Label(
            left,
            text="FILLED latched: una volta fillato resta FILLED anche se il prezzo torna indietro.",
            foreground="gray"
        ).pack(anchor="w", pady=(10, 0))

        # Results labels (NO "PnL per +1$" as requested)
        ttk.Label(right, textvariable=self.size_var, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(right, textvariable=self.avg_var, font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(10, 0))
        ttk.Label(right, textvariable=self.pnl_var, font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(10, 0))
        ttk.Label(right, textvariable=self.next_var).pack(anchor="w", pady=(10, 0))

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=12)

        cols = ("status", "entry", "qty", "source")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=18)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.heading("status", text="Stato")
        self.tree.heading("entry", text="Entry")
        self.tree.heading("qty", text="Qty")
        self.tree.heading("source", text="Fonte")
        self.tree.column("status", width=110, anchor="w")
        self.tree.column("entry", width=160, anchor="w")
        self.tree.column("qty", width=120, anchor="w")
        self.tree.column("source", width=240, anchor="w")

        ttk.Label(right, textvariable=self.note_var, foreground="gray").pack(anchor="w", pady=(10, 0))

    # -------- ACTIONS --------
    def _read_float(self, var, name: str, minv: float = None):
        try:
            val = float(var.get())
        except Exception:
            raise ValueError(f"{name} non valido.")
        if minv is not None and val < minv:
            raise ValueError(f"{name} deve essere >= {minv}.")
        return val

    def clear_all(self):
        self.symbol_var.set(DEFAULT_PAIR)
        self.side_var.set(DEFAULT_SIDE)
        self.leverage_var.set(DEFAULT_LEVERAGE)
        self.max_size_var.set(DEFAULT_MAX_SIZE)
        self.reload_rate_var.set(DEFAULT_RELOAD_RATE)

        self.ladder_text.delete("1.0", tk.END)
        self.ladder_text.insert(
            "1.0",
            "# Premi 'Genera da Bollinger' per calcolare i livelli automatici\n"
            "# formato: prezzo, qty_asset  (es. ETH)\n"
        )

        self.applied_ladder = []
        self.filled_ids_latched = set()
        self.last_price = None
        self.last_avg_touch_ts = 0.0

        self.tree.delete(*self.tree.get_children())

        self.size_var.set("Size filled: —")
        self.avg_var.set("Average entry: —")
        self.pnl_var.set("PnL (real-time): —")
        self.next_var.set("Next level: —")
        self.note_var.set("")

    def apply_ladder(self):
        try:
            max_size = self._read_float(self.max_size_var, "Max size", minv=0.0001)
        except Exception as e:
            messagebox.showerror("Errore", str(e))
            return

        raw = self.ladder_text.get("1.0", tk.END)
        try:
            ladder = parse_ladder_text(raw, max_size=max_size)
            self.applied_ladder = ladder
            self.filled_ids_latched = set()  # reset latching when ladder changes
            self.note_var.set(f"Ladder applicata: {len(ladder)} livelli (totale <= {max_size:g}).")
        except Exception as e:
            messagebox.showerror("Errore ladder", str(e))

    def generate_from_bollinger(self):
        with shared.lock:
            st = shared.state
        if not st.ok or st.price <= 0:
            messagebox.showerror("Errore", "Prezzo live non disponibile. Controlla rete o pair.")
            return

        try:
            max_size = self._read_float(self.max_size_var, "Max size", minv=0.0001)
            reload_rate = self._read_float(self.reload_rate_var, "Tasso ricarico (r)", minv=1.0)
        except Exception as e:
            messagebox.showerror("Errore", str(e))
            return

        symbol = self.symbol_var.get().strip().upper() or DEFAULT_PAIR
        side = self.side_var.get().strip().upper()
        if side not in ("SHORT", "LONG"):
            messagebox.showerror("Errore", "Side deve essere SHORT o LONG.")
            return

        try:
            ladder, msg = build_bollinger_ladder(symbol, st.price, side=side, max_size=max_size, reload_rate=reload_rate)

            self.ladder_text.delete("1.0", tk.END)
            band_name = "Upper" if side == "SHORT" else "Lower"
            self.ladder_text.insert("1.0", f"# Ladder generata da Bollinger {band_name} ({side})\n")
            self.ladder_text.insert("2.0", f"# period={BB_PERIOD} std={BB_STD} | TF: {', '.join(TIMEFRAMES)} | r={reload_rate}\n")

            line_no = 3
            for price, qty, tf in ladder:
                self.ladder_text.insert(f"{line_no}.0", f"{price:.2f}, {qty:.6f}  # {tf} {band_name}\n")
                line_no += 1

            self.note_var.set(msg)
            self.apply_ladder()

        except Exception as e:
            messagebox.showerror("Errore Bollinger", str(e))

    # -------- Alerts: avg touch continuous beep + popup --------
    def _beep_continuous_thread(self, stop_flag: threading.Event):
        if not self.sound_enabled_var.get():
            return
        if not HAS_WINSOUND:
            return
        while not stop_flag.is_set():
            winsound.Beep(1400, 200)
            time.sleep(0.05)

    def _popup(self, title: str, msg: str):
        if not self.popup_enabled_var.get():
            return
        messagebox.showinfo(title, msg)

    def _notify_avg_touch(self, avg_entry: float, current_price: float):
        if not self.alerts_enabled_var.get():
            return

        now = time.time()
        if now - self.last_avg_touch_ts < self.AVG_TOUCH_COOLDOWN_SEC:
            return
        self.last_avg_touch_ts = now

        stop_flag = threading.Event()
        t = threading.Thread(target=self._beep_continuous_thread, args=(stop_flag,), daemon=True)
        t.start()

        side = self.side_var.get().strip().upper()
        msg = (
            f"Prezzo ha toccato l'Average Entry!\n\n"
            f"Side: {side}\n"
            f"Average entry: {avg_entry:,.2f}\n"
            f"Prezzo attuale: {current_price:,.2f}\n"
        )
        self._popup("ALERT — Average Entry Touched", msg)
        stop_flag.set()

    # -------- Core tick --------
    def _tick(self):
        with shared.lock:
            st = shared.state

        if st.ok and st.price > 0:
            self.live_status_var.set(f"Status: ONLINE | {st.symbol}")
            self.live_price_var.set(f"Prezzo: {st.price:,.2f} USDC")
            dt = datetime.fromtimestamp(st.ts)
            self.live_time_var.set(f"Time: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            current = st.price
        else:
            self.live_status_var.set(f"Status: OFFLINE | {st.symbol}")
            self.live_price_var.set("Prezzo: —")
            self.live_time_var.set("Time: —")
            current = 0.0

        self.tree.delete(*self.tree.get_children())

        side = self.side_var.get().strip().upper()

        if current <= 0 or not self.applied_ladder or side not in ("SHORT", "LONG"):
            if not self.applied_ladder:
                self.size_var.set("Size filled: —")
                self.avg_var.set("Average entry: —")
                self.pnl_var.set("PnL (real-time): —")
                self.next_var.set("Next level: —")
            self.last_price = current if current > 0 else self.last_price
            self.after(200, self._tick)
            return

        try:
            max_size = float(self.max_size_var.get())
        except Exception:
            max_size = max(sum(q for _, q in self.applied_ladder), 0.0)

        # LATCH levels once hit
        for p, q in self.applied_ladder:
            lid = level_id(p, q)
            if lid in self.filled_ids_latched:
                continue
            hit = (current >= p) if side == "SHORT" else (current <= p)
            if hit:
                self.filled_ids_latched.add(lid)

        filled = [(p, q) for (p, q) in self.applied_ladder if level_id(p, q) in self.filled_ids_latched]
        pending = [(p, q) for (p, q) in self.applied_ladder if level_id(p, q) not in self.filled_ids_latched]

        if side == "SHORT":
            pending.sort(key=lambda x: x[0])
        else:
            pending.sort(key=lambda x: x[0], reverse=True)

        avg, filled_qty = weighted_avg(filled)
        remaining = max(0.0, max_size - filled_qty)

        self.size_var.set(f"Size filled: {filled_qty:,.4f} / {max_size:,.4f} | Remaining: {remaining:,.4f}")

        if filled_qty > 0:
            self.avg_var.set(f"Average entry (filled): {avg:,.2f} USDC")
        else:
            self.avg_var.set("Average entry (filled): — (nessun livello riempito)")

        pnl = unrealized_pnl_usdc(side, avg, current, filled_qty)
        self.pnl_var.set(f"PnL (real-time, filled): {pnl:,.2f} USDC")

        if pending:
            p_next, q_next = pending[0]
            dist = (p_next - current) if side == "SHORT" else (current - p_next)
            self.next_var.set(f"Next level: {p_next:,.2f} (qty {q_next:,.6f}) | distanza: {dist:,.2f}")
        else:
            self.next_var.set("Next level: — (tutti i livelli sono FILLED)")

        # AVG ENTRY TOUCH alert
        if filled_qty > 0 and avg > 0 and self.last_price is not None:
            tol = max(0.50, avg * 0.0002)
            crossed = (self.last_price - avg) * (current - avg) <= 0
            near = abs(current - avg) <= tol
            if crossed or near:
                self._notify_avg_touch(avg, current)

        # Source map from comments
        src_map = {}
        for line in self.ladder_text.get("1.0", tk.END).splitlines():
            if "," not in line:
                continue
            comment = "-"
            if "#" in line:
                left, comment = line.split("#", 1)
                left = left.strip()
                comment = comment.strip()
            else:
                left = line.strip()
            try:
                left = left.split("#", 1)[0].strip()
                p = float(left.split(",")[0].strip())
                src_map[round(p, 2)] = comment
            except Exception:
                pass

        ladder_disp = list(self.applied_ladder)
        ladder_disp.sort(key=lambda x: x[0], reverse=(side == "LONG"))

        for p, q in ladder_disp:
            lid = level_id(p, q)
            status = "FILLED" if lid in self.filled_ids_latched else "PENDING"
            src = src_map.get(round(p, 2), "-")
            self.tree.insert("", tk.END, values=(status, f"{p:,.2f}", f"{q:,.6f}", src))

        self.last_price = current
        self.after(200, self._tick)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

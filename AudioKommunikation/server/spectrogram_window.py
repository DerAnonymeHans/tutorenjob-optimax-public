"""Fenster mit einem laufenden Spektrogramm des Mikrofons.

Einstellbar: Frequenzbereich, angezeigte Dauer, Farbskala (dB).
Mit Pause wird das Bild eingefroren; mit der Matplotlib-Werkzeugleiste
kann man dann hineinzoomen.
"""

from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.ticker import AutoLocator, FixedLocator, FuncFormatter, NullLocator, ScalarFormatter

from audio_engine import AudioEngine

NFFT = 4096          # Fensterlänge der FFT -> Frequenzauflösung samplerate/NFFT
HOP = 512            # Abstand zweier Spalten -> Zeitauflösung HOP/samplerate
REFRESH_MS = 100     # Bildwiederholung
LOG_ROWS = 600       # Bildzeilen bei logarithmischer Frequenzachse
MARKER_COLORS = {"send": "#4fc3f7", "receive": "#ffb74d"}


class SpectrogramBuffer:
    """Berechnet laufend neue Spektrogramm-Spalten aus dem Mikrofon-Ringpuffer
    und hebt die letzten `max_seconds` davon auf."""

    def __init__(self, engine: AudioEngine, max_seconds: float):
        self.engine = engine
        self.sr = engine.samplerate
        self.window = np.hanning(NFFT).astype(np.float32)
        self.scale = self.window.sum() / 2  # Sinus mit Amplitude 1 -> 0 dB
        self.n_bins = NFFT // 2 + 1
        self.capacity = int(max_seconds * self.sr / HOP)
        self.columns = np.full((self.capacity, self.n_bins), -150.0, dtype=np.float32)
        self.next_frame = engine.ring.total // HOP  # absolute Spaltennummer

    def update(self) -> None:
        ring = self.engine.ring
        last_frame = (ring.total - NFFT) // HOP  # letzte vollständig verfügbare Spalte
        if last_frame < self.next_frame:
            return
        first = max(self.next_frame, -(-ring.oldest() // HOP), last_frame - self.capacity + 1)
        samples = ring.read(first * HOP, last_frame * HOP + NFFT)
        n = last_frame - first + 1
        idx = np.arange(n)[:, None] * HOP + np.arange(NFFT)[None, :]
        spectrum = np.abs(np.fft.rfft(samples[idx] * self.window, axis=1)) / self.scale
        db = 20 * np.log10(spectrum + 1e-9)
        self.columns[np.arange(first, last_frame + 1) % self.capacity] = db
        self.next_frame = last_frame + 1

    def latest(self, n: int) -> tuple[np.ndarray, int]:
        """Die neuesten n Spalten (älteste zuerst) und die Nummer der letzten Spalte."""
        n = min(n, self.capacity)
        last = self.next_frame - 1
        idx = np.arange(last - n + 1, last + 1) % self.capacity
        return self.columns[idx], last


class SpectrogramWindow:
    def __init__(self, engine: AudioEngine, activity: queue.Queue, title: str,
                 max_seconds: float = 60.0):
        self.engine = engine
        self.activity = activity
        self.max_seconds = max_seconds
        self.spec = SpectrogramBuffer(engine, max_seconds)
        self.nyquist = engine.samplerate / 2
        self.paused = False
        self.snapshot = None  # (Spalten, letzte Spaltennummer, Marker) beim Pausieren

        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("1100x700")
        self._build_controls()
        self._build_plot()
        self._build_log()
        self.root.after(REFRESH_MS, self._tick)

    # ------------------------------------------------------------------ Aufbau

    def _build_controls(self) -> None:
        bar = ttk.Frame(self.root, padding=6)
        bar.pack(side=tk.TOP, fill=tk.X)

        self.play_button = ttk.Button(bar, text="⏸ Pause", width=10, command=self.toggle_pause)
        self.play_button.pack(side=tk.LEFT, padx=(0, 12))

        self.duration = tk.DoubleVar(value=10)
        self.fmin = tk.DoubleVar(value=0)
        self.fmax = tk.DoubleVar(value=min(8000, self.nyquist))
        self.db_min = tk.DoubleVar(value=-100)
        self.db_max = tk.DoubleVar(value=-20)
        self.show_markers = tk.BooleanVar(value=True)
        self.log_freq = tk.BooleanVar(value=False)

        def spin(label, var, lo, hi, step, width=7):
            ttk.Label(bar, text=label).pack(side=tk.LEFT, padx=(8, 2))
            box = ttk.Spinbox(bar, textvariable=var, from_=lo, to=hi, increment=step,
                              width=width, command=self.redraw)
            box.bind("<Return>", lambda e: self.redraw())
            box.bind("<FocusOut>", lambda e: self.redraw())
            box.pack(side=tk.LEFT)

        spin("Dauer [s]", self.duration, 1, self.max_seconds, 1, width=5)
        spin("f min [Hz]", self.fmin, 0, self.nyquist, 100)
        spin("f max [Hz]", self.fmax, 0, self.nyquist, 100)
        spin("dB min", self.db_min, -160, 0, 5, width=5)
        spin("dB max", self.db_max, -160, 20, 5, width=5)
        ttk.Checkbutton(bar, text="log. Frequenzachse", variable=self.log_freq,
                        command=self.redraw).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Checkbutton(bar, text="Markierungen (blau = senden, orange = empfangen)", variable=self.show_markers,
                        command=self.redraw).pack(side=tk.LEFT, padx=12)

    def _build_plot(self) -> None:
        self.fig = Figure(figsize=(10, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Zeit [s]  (0 = jetzt)")
        self.ax.set_ylabel("Frequenz [Hz]")
        self.image = self.ax.imshow(np.zeros((2, 2)), origin="lower", aspect="auto",
                                    cmap="magma", extent=(-1, 0, 0, 1))
        self.colorbar = self.fig.colorbar(self.image, ax=self.ax, label="Pegel [dBFS]")
        self.marker_patches = []
        self.fig.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.root)
        toolbar = NavigationToolbar2Tk(self.canvas, self.root, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def _build_log(self) -> None:
        frame = ttk.Frame(self.root, padding=(6, 0))
        frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.log_list = tk.Listbox(frame, height=5, font=("Consolas", 9))
        self.log_list.pack(fill=tk.X)

    # ------------------------------------------------------------------ Logik

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            cols, last = self.spec.latest(self.spec.capacity)
            self.snapshot = (cols.copy(), last, self.engine.markers())
            self.play_button.config(text="▶ Weiter")
        else:
            self.snapshot = None
            self.play_button.config(text="⏸ Pause")
        self.redraw()

    def _settings(self):
        """Liest die Eingabefelder; Unsinn wird auf sinnvolle Werte begrenzt."""
        def get(var, default):
            try:
                return float(var.get())
            except (tk.TclError, ValueError):
                return default
        duration = min(max(get(self.duration, 10), 0.5), self.max_seconds)
        fmin = min(max(get(self.fmin, 0), 0), self.nyquist)
        fmax = min(max(get(self.fmax, self.nyquist), 0), self.nyquist)
        if fmax <= fmin:
            fmin, fmax = 0, self.nyquist
        db_min, db_max = get(self.db_min, -100), get(self.db_max, -20)
        if db_max <= db_min:
            db_max = db_min + 1
        return duration, fmin, fmax, db_min, db_max

    def redraw(self) -> None:
        duration, fmin, fmax, db_min, db_max = self._settings()
        n_cols = max(2, int(duration * self.engine.samplerate / HOP))

        if self.snapshot is not None:
            all_cols, last, markers = self.snapshot
            cols = all_cols[-n_cols:]
        else:
            cols, last = self.spec.latest(n_cols)
            markers = self.engine.markers()

        bin_hz = self.engine.samplerate / NFFT
        if self.log_freq.get():
            # Logarithmisch: Bildzeilen gleichmäßig in log10(f) verteilen. Jede Zeile
            # zeigt das lauteste FFT-Bin in ihrem Frequenzbereich (oben umfasst eine
            # Zeile viele Bins, unten wiederholt sich dasselbe Bin über mehrere Zeilen).
            # Die y-Achse rechnet in log10(f); die Beschriftung zeigt wieder Hz.
            # Unterhalb eines Bins (~23 Hz) ist nichts aufgelöst, daher die Untergrenze.
            fmin = max(fmin, bin_hz)
            if fmax <= fmin:
                fmax = self.nyquist
            edges = np.geomspace(fmin, fmax, LOG_ROWS + 1)
            stop = min(self.spec.n_bins, int(round(fmax / bin_hz)) + 1)
            starts = np.clip(np.round(edges[:-1] / bin_hz).astype(int), 0, stop - 1)
            img = np.maximum.reduceat(cols[:, :stop], starts, axis=1).T
            y_lo, y_hi = np.log10(fmin), np.log10(fmax)
            img_y = (y_lo, y_hi)
            self._set_log_ticks(fmin, fmax)
        else:
            # Linear: nur die Frequenz-Zeilen im gewünschten Bereich ausschneiden
            lo = int(fmin / bin_hz)
            hi = min(self.spec.n_bins, int(np.ceil(fmax / bin_hz)) + 1)
            img = cols[:, lo:hi].T
            y_lo, y_hi = fmin, fmax
            img_y = (lo * bin_hz, (hi - 1) * bin_hz)
            self._set_linear_ticks()

        # Zeitachse: Mitte der letzten Spalte = 0 s
        sec_per_col = HOP / self.engine.samplerate
        now_sample = last * HOP + NFFT // 2
        t0 = -len(cols) * sec_per_col
        self.image.set_data(img)
        self.image.set_extent((t0, 0, *img_y))
        self.image.set_clim(db_min, db_max)
        self.ax.set_xlim(-duration, 0)
        self.ax.set_ylim(y_lo, y_hi)

        for p in self.marker_patches:
            p.remove()
        self.marker_patches = []
        if self.show_markers.get():
            height = (y_hi - y_lo) * 0.03
            for m in markers:
                x0 = (m.start - now_sample) / self.engine.samplerate
                x1 = (m.end - now_sample) / self.engine.samplerate
                if x1 < -duration or x0 > 0:
                    continue
                y = y_hi - height * (1.2 if m.kind == "send" else 2.4)
                rect = Rectangle((x0, y), x1 - x0, height, facecolor=MARKER_COLORS[m.kind],
                                 edgecolor="white", linewidth=0.8)
                self.ax.add_patch(rect)
                self.marker_patches.append(rect)

        self.canvas.draw_idle()

    def _set_log_ticks(self, fmin: float, fmax: float) -> None:
        """Ticks bei 20, 50, 100, 200, 500, 1000, ... Hz (y-Achse ist in log10(f))."""
        nice = [m * 10 ** e for e in range(0, 6) for m in (1, 2, 5)]
        ticks = [np.log10(f) for f in nice if fmin <= f <= fmax]
        self.ax.yaxis.set_major_locator(FixedLocator(ticks))
        self.ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{10 ** y:.0f}"))
        self.ax.yaxis.set_minor_locator(NullLocator())

    def _set_linear_ticks(self) -> None:
        if not isinstance(self.ax.yaxis.get_major_locator(), AutoLocator):
            self.ax.yaxis.set_major_locator(AutoLocator())
            self.ax.yaxis.set_major_formatter(ScalarFormatter())

    def _tick(self) -> None:
        start = time.perf_counter()
        try:
            self.spec.update()
            while True:
                self.log_list.insert(tk.END, self.activity.get_nowait())
                self.log_list.see(tk.END)
                if self.log_list.size() > 500:
                    self.log_list.delete(0)
        except queue.Empty:
            pass
        if not self.paused:
            self.redraw()
        # Wenn das Zeichnen lange dauert, seltener aktualisieren statt zu hängen
        spent_ms = int((time.perf_counter() - start) * 1000)
        self.root.after(max(REFRESH_MS - spent_ms, 20), self._tick)

    def run(self, on_close) -> None:
        def close():
            on_close()
            self.root.destroy()
        self.root.protocol("WM_DELETE_WINDOW", close)
        self.root.mainloop()

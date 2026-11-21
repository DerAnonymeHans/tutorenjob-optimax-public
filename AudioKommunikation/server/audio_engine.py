"""Audio-Kern des Servers.

- Ein Mikrofon-Stream läuft die ganze Zeit und schreibt in einen Ringpuffer
  (die letzten `history_seconds` Sekunden). Daraus bedienen sich sowohl
  `receive()` als auch das Spektrogramm-Fenster.
- `send()` hängt Samples an eine Warteschlange an, die der Lautsprecher-Stream
  nacheinander abspielt. Gleichzeitige Sendungen mehrerer Schüler werden also
  hintereinander abgespielt, nicht überlagert.
- Im Simulationsmodus (ohne Audio-Hardware) landet alles, was "abgespielt"
  wird, plus etwas Rauschen direkt wieder im Mikrofon-Puffer.
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass

import numpy as np

BLOCK_SIZE = 1024  # Samples pro Audio-Block


class RingBuffer:
    """Speichert die letzten `capacity` Samples. Jedes Sample hat eine absolute
    Nummer (0, 1, 2, ... seit Serverstart), damit man Zeitpunkte vergleichen kann."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.data = np.zeros(capacity, dtype=np.float32)
        self.total = 0  # Anzahl bisher geschriebener Samples
        self.cond = threading.Condition()

    def write(self, samples: np.ndarray) -> None:
        n = len(samples)
        with self.cond:
            if n >= self.capacity:
                samples = samples[-self.capacity:]
                self.total += n - self.capacity
                n = self.capacity
            start = self.total % self.capacity
            first = min(n, self.capacity - start)
            self.data[start:start + first] = samples[:first]
            self.data[:n - first] = samples[first:]
            self.total += n
            self.cond.notify_all()

    def oldest(self) -> int:
        return max(0, self.total - self.capacity)

    def read(self, start: int, end: int) -> np.ndarray:
        """Liefert die Samples mit absoluter Nummer start..end-1."""
        with self.cond:
            if start < self.oldest() or end > self.total or start > end:
                raise ValueError(f"Bereich {start}..{end} nicht (mehr) im Puffer")
            idx = np.arange(start, end) % self.capacity
            return self.data[idx].copy()

    def wait_until(self, total: int, timeout: float) -> bool:
        with self.cond:
            return self.cond.wait_for(lambda: self.total >= total, timeout)


@dataclass
class Marker:
    """Ein Sende- oder Empfangsvorgang, zum Einzeichnen ins Spektrogramm."""
    kind: str      # "send" oder "receive"
    client: str
    start: int     # absolute Sample-Nummer (Mikrofon-Zeitachse)
    end: int


class _PlayJob:
    def __init__(self, samples: np.ndarray, client: str):
        self.samples = samples
        self.client = client
        self.pos = 0
        self.done = threading.Event()


class AudioEngine:
    def __init__(self, samplerate: int = 48000, input_device=None, output_device=None,
                 history_seconds: float = 60.0, simulate: bool = False):
        self.samplerate = samplerate
        self.input_device = input_device
        self.output_device = output_device
        self.simulate = simulate
        self.ring = RingBuffer(int(history_seconds * samplerate))

        self._jobs: collections.deque[_PlayJob] = collections.deque()
        self._jobs_lock = threading.Lock()
        self._markers: collections.deque[Marker] = collections.deque(maxlen=200)
        self._markers_lock = threading.Lock()

        self._in_stream = None
        self._out_stream = None
        self._sim_thread = None
        self._running = False
        self.output_latency = 0.0

    # ------------------------------------------------------------------ Start/Stop

    def start(self) -> None:
        self._running = True
        if self.simulate:
            self._sim_thread = threading.Thread(target=self._simulate_loop, daemon=True)
            self._sim_thread.start()
            return

        import sounddevice as sd

        self._in_stream = self._open_stream(sd, "input", self.input_device, self._input_callback)
        self._out_stream = self._open_stream(sd, "output", self.output_device,
                                             self._output_callback)
        self.output_latency = float(self._out_stream.latency)
        self._in_stream.start()
        self._out_stream.start()

    def output_devices(self) -> list[tuple[int, str]]:
        """Alle Ausgabegeräte als (Nummer, Anzeigename)."""
        if self.simulate:
            return []
        import sounddevice as sd
        apis = sd.query_hostapis()
        return [(d["index"], f"{d['index']}: {d['name']} ({apis[d['hostapi']]['name']})")
                for d in sd.query_devices() if d["max_output_channels"] > 0]

    def current_output_device(self) -> int | None:
        """Nummer des gerade benutzten Ausgabegeräts."""
        if self.simulate or self._out_stream is None:
            return None
        return int(self._out_stream.device)

    def set_output_device(self, device) -> None:
        """Wechselt den Lautsprecher im laufenden Betrieb. Zuerst wird das neue Gerät
        geöffnet – klappt das nicht, bleibt das alte aktiv (RuntimeError)."""
        if self.simulate:
            return
        import sounddevice as sd
        new_stream = self._open_stream(sd, "output", device, self._output_callback)
        try:
            new_stream.start()
        except Exception as e:  # noqa: BLE001
            new_stream.close()
            raise RuntimeError(f"Lautsprecher ({device}) lässt sich nicht starten: {e}") from None
        old_stream, self._out_stream = self._out_stream, new_stream
        self.output_device = device
        self.output_latency = float(new_stream.latency)
        if old_stream is not None:
            # Eine laufende Sendung spielt einfach auf dem neuen Gerät weiter
            old_stream.abort()
            old_stream.close()

    def _open_stream(self, sd, kind: str, device, callback):
        try:
            info = sd.query_devices(device, kind)
            # Mit der "natürlichen" Kanalzahl öffnen (max. 2) – manche Treiber liefern
            # bei Mono nur Müll. Verwendet wird ohnehin nur ein Kanal.
            channels = min(2, info[f"max_{kind}_channels"])
            cls = sd.InputStream if kind == "input" else sd.OutputStream
            return cls(samplerate=self.samplerate, blocksize=BLOCK_SIZE, device=device,
                       channels=channels, dtype="float32", callback=callback)
        except Exception as e:  # noqa: BLE001 – Fehlertext soll beim Nutzer ankommen
            name = "Mikrofon" if kind == "input" else "Lautsprecher"
            raise RuntimeError(f"{name} ({device if device is not None else 'Standard'}) "
                               f"lässt sich nicht öffnen: {e}") from None

    def stop(self) -> None:
        self._running = False
        for s in (self._in_stream, self._out_stream):
            if s is not None:
                s.stop()
                s.close()
        self._in_stream = self._out_stream = None

    # ------------------------------------------------------------------ Callbacks

    def _input_callback(self, indata, frames, time_info, status) -> None:
        self.ring.write(indata[:, 0])

    def _output_callback(self, outdata, frames, time_info, status) -> None:
        block = self._next_play_block(frames)
        outdata[:] = block[:, None]  # gleiches Signal auf alle Kanäle

    def _simulate_loop(self) -> None:
        """Ersetzt Mikrofon und Lautsprecher: Gesendetes kommt (leicht verrauscht)
        sofort wieder an. Läuft in Echtzeit, damit receive(ms) gleich lange dauert."""
        rng = np.random.default_rng()
        block_time = BLOCK_SIZE / self.samplerate
        next_t = time.perf_counter()
        while self._running:
            played = self._next_play_block(BLOCK_SIZE)
            noise = rng.normal(0, 0.003, BLOCK_SIZE).astype(np.float32)
            self.ring.write(0.5 * played + noise)
            next_t += block_time
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()  # zu langsam: nicht aufholen wollen

    def _next_play_block(self, frames: int) -> np.ndarray:
        """Holt die nächsten `frames` Samples aus der Sende-Warteschlange
        (Stille, wenn nichts ansteht)."""
        out = np.zeros(frames, dtype=np.float32)
        i = 0
        with self._jobs_lock:
            while i < frames and self._jobs:
                job = self._jobs[0]
                if job.pos == 0:
                    # Beginn der Wiedergabe in Mikrofon-Zeit merken (für das Spektrogramm)
                    start = self.ring.total + i + int(self.output_latency * self.samplerate)
                    self._add_marker(Marker("send", job.client, start, start + len(job.samples)))
                take = min(frames - i, len(job.samples) - job.pos)
                out[i:i + take] = job.samples[job.pos:job.pos + take]
                job.pos += take
                i += take
                if job.pos >= len(job.samples):
                    self._jobs.popleft()
                    job.done.set()
        return out

    # ------------------------------------------------------------------ API

    def send(self, samples: np.ndarray, client: str = "?") -> None:
        """Spielt die Samples ab und kehrt erst zurück, wenn sie gespielt wurden."""
        job = _PlayJob(np.asarray(samples, dtype=np.float32), client)
        with self._jobs_lock:
            waiting = sum(len(j.samples) - j.pos for j in self._jobs)
            self._jobs.append(job)
        timeout = (waiting + len(job.samples)) / self.samplerate + 5.0
        if not job.done.wait(timeout):
            raise TimeoutError("Wiedergabe hängt – läuft der Audio-Ausgang?")
        # Die letzten Samples stecken noch im Puffer der Soundkarte
        time.sleep(self.output_latency)

    def receive(self, ms: float, client: str = "?") -> tuple[int, np.ndarray]:
        """Nimmt ab jetzt `ms` Millisekunden auf. Liefert (Start-Sample-Nummer, Samples)."""
        n = int(round(ms * self.samplerate / 1000))
        start = self.ring.total
        self._add_marker(Marker("receive", client, start, start + n))
        if not self.ring.wait_until(start + n, timeout=ms / 1000 + 5.0):
            raise TimeoutError("Keine Mikrofondaten – läuft der Audio-Eingang?")
        return start, self.ring.read(start, start + n)

    def markers(self) -> list[Marker]:
        with self._markers_lock:
            return list(self._markers)

    def _add_marker(self, m: Marker) -> None:
        with self._markers_lock:
            self._markers.append(m)

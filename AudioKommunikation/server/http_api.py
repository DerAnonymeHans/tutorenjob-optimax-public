"""HTTP-Schnittstelle für die Schüler.

Endpunkte (alle Antworten sind JSON):

  GET  /info                 -> {"samplerate": 48000, "max_send_seconds": ..., "max_receive_ms": ...}
  POST /send                 Body: {"samples": [0.0, 0.1, ...]}   (Werte zwischen -1 und 1)
                             -> {"ok": true, "samples": n, "duration_ms": ..., "clipped": k}
                             Antwortet erst, wenn alles abgespielt wurde.
  GET  /receive?ms=1000      Nimmt ab jetzt 1000 ms auf
                             -> {"samplerate": 48000, "start_sample": ..., "samples": [...]}
"""

from __future__ import annotations

import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

import numpy as np

from audio_engine import AudioEngine

MAX_SEND_SECONDS = 10.0
MAX_RECEIVE_MS = 10_000


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def make_server(engine: AudioEngine, host: str, port: int,
                log: Callable[[str], None]) -> ThreadingHTTPServer:

    class Handler(BaseHTTPRequestHandler):
        # ---------------------------------------------------------- Routing

        def do_GET(self):
            url = urlparse(self.path)
            routes = {"/": self.handle_info, "/info": self.handle_info,
                      "/receive": self.handle_receive}
            self.dispatch(routes.get(url.path), parse_qs(url.query))

        def do_POST(self):
            url = urlparse(self.path)
            routes = {"/send": self.handle_send}
            self.dispatch(routes.get(url.path), parse_qs(url.query))

        def do_OPTIONS(self):  # erlaubt Aufrufe aus dem Browser (CORS)
            self.send_response(204)
            self.send_cors_headers()
            self.end_headers()

        def dispatch(self, handler, query):
            if handler is None:
                self.reply(404, {"error": f"Unbekannter Pfad: {self.path}"})
                return
            try:
                self.reply(200, handler(query))
            except ApiError as e:
                self.reply(e.status, {"error": str(e)})
            except TimeoutError as e:
                self.reply(503, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                log(f"FEHLER bei {self.path}: {e!r}")
                self.reply(500, {"error": f"Interner Fehler: {e}"})

        # ---------------------------------------------------------- Endpunkte

        def handle_info(self, query):
            return {"samplerate": engine.samplerate,
                    "max_send_seconds": MAX_SEND_SECONDS,
                    "max_receive_ms": MAX_RECEIVE_MS}

        def handle_send(self, query):
            body = self.read_json()
            samples = body.get("samples") if isinstance(body, dict) else body
            if not isinstance(samples, list) or len(samples) == 0:
                raise ApiError(400, 'Erwartet JSON wie {"samples": [0.0, 0.5, ...]}')
            max_n = int(MAX_SEND_SECONDS * engine.samplerate)
            if len(samples) > max_n:
                raise ApiError(400, f"Zu lang: höchstens {max_n} Samples "
                                    f"({MAX_SEND_SECONDS:g} s) pro Aufruf")
            try:
                x = np.array(samples, dtype=np.float64)
            except (TypeError, ValueError):
                raise ApiError(400, "Alle Samples müssen Zahlen sein") from None
            if x.ndim != 1 or not np.all(np.isfinite(x)):
                raise ApiError(400, "Samples müssen eine flache Liste endlicher Zahlen sein")

            clipped = int(np.count_nonzero(np.abs(x) > 1.0))
            x = np.clip(x, -1.0, 1.0)
            duration_ms = 1000 * len(x) / engine.samplerate
            log(f"{self.client_name()} sendet {duration_ms:.0f} ms"
                + (f"  ({clipped} Samples begrenzt!)" if clipped else ""))
            engine.send(x, client=self.client_name())
            return {"ok": True, "samples": len(x), "duration_ms": duration_ms, "clipped": clipped}

        def handle_receive(self, query):
            try:
                ms = float(query.get("ms", [""])[0])
            except ValueError:
                raise ApiError(400, "Parameter ms fehlt, z.B. /receive?ms=1000") from None
            if not (0 < ms <= MAX_RECEIVE_MS) or math.isnan(ms):
                raise ApiError(400, f"ms muss zwischen 0 und {MAX_RECEIVE_MS} liegen")
            log(f"{self.client_name()} empfängt {ms:.0f} ms")
            start, samples = engine.receive(ms, client=self.client_name())
            return {"samplerate": engine.samplerate, "start_sample": start,
                    "samples": np.round(samples.astype(np.float64), 5).tolist()}

        # ---------------------------------------------------------- Hilfen

        def read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ApiError(400, "Leerer Request-Body")
            if length > 50_000_000:
                raise ApiError(413, "Request zu groß")
            try:
                return json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ApiError(400, "Body ist kein gültiges JSON") from None

        def reply(self, status: int, obj) -> None:
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(data)

        def send_cors_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def client_name(self) -> str:
            return self.client_address[0]

        def log_message(self, format, *args):  # Standard-Logging auf stderr abschalten
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server

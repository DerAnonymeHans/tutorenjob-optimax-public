"""AudioKommunikation-Server.

Start:
    python main.py                      # Standard-Mikrofon und -Lautsprecher, Port 8000
    python main.py --list-devices       # Audio-Geräte anzeigen
    python main.py --input 3 --output 5 # bestimmte Geräte (Nummer oder Teil des Namens)
    python main.py --simulate           # ohne Hardware: Gesendetes kommt verrauscht zurück
"""

from __future__ import annotations

import argparse
import queue
import socket
import sys
import threading
import time

from audio_engine import AudioEngine
from http_api import make_server
from spectrogram_window import SpectrogramWindow


def device_arg(value: str):
    """Gerätenummer oder Teil des Gerätenamens."""
    return int(value) if value.isdigit() else value


def local_ip() -> str:
    """IP-Adresse im lokalen Netz (es wird dabei nichts gesendet)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Audio-Übertragungsstrecke für den Unterricht")
    parser.add_argument("--host", default="0.0.0.0", help="Adresse (Standard: alle Netzwerke)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--samplerate", type=int, default=48000)
    parser.add_argument("--input", type=device_arg, default=None, help="Mikrofon (Nummer/Name)")
    parser.add_argument("--output", type=device_arg, default=None, help="Lautsprecher (Nummer/Name)")
    parser.add_argument("--history", type=float, default=60, help="Sekunden im Speicher")
    parser.add_argument("--simulate", action="store_true", help="ohne Audio-Hardware testen")
    parser.add_argument("--list-devices", action="store_true", help="Audio-Geräte auflisten")
    args = parser.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    activity: queue.Queue[str] = queue.Queue()

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        print(line, flush=True)
        activity.put(line)

    engine = AudioEngine(samplerate=args.samplerate, input_device=args.input,
                         output_device=args.output, history_seconds=args.history,
                         simulate=args.simulate)
    try:
        engine.start()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Audio konnte nicht gestartet werden: {e}\n"
                 f"Tipp: --list-devices, dann --input/--output angeben, oder --simulate.")

    try:
        server = make_server(engine, args.host, args.port, log)
    except OSError as e:
        engine.stop()
        sys.exit(f"Port {args.port} nicht verfügbar: {e}")
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://{local_ip()}:{args.port}"
    mode = "SIMULATION" if args.simulate else "Audio-Hardware"
    log(f"Server läuft ({mode}, {args.samplerate} Hz) – Schüler verbinden sich mit {url}")

    window = SpectrogramWindow(engine, activity, title=f"AudioKommunikation – {url}",
                               max_seconds=args.history)

    def shutdown():
        server.shutdown()
        engine.stop()

    window.run(on_close=shutdown)


if __name__ == "__main__":
    main()

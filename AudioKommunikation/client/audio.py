"""Helfer für die Verbindung zum Audio-Server.

Diese Datei neben dein eigenes Notebook legen und so benutzen:

    import audio
    audio.SERVER = "http://192.168.178.122:8000"   # Adresse vom Tutor

    audio.send([0.0, 0.5, 1.0, 0.5, 0.0, ...])     # Samples abspielen
    samples = audio.receive(1000)                  # 1000 ms aufnehmen

    audio.plot(samples)                            # Signal über der Zeit
    audio.plot_spectrum(samples)                   # welche Frequenzen stecken drin?
    audio.plot_spectrogram(samples)                # Frequenzen über der Zeit

Samples sind Zahlen zwischen -1 und 1. Pro Sekunde braucht man
audio.samplerate() Samples (meist 48000).
"""

import json
import urllib.error
import urllib.request

import matplotlib.pyplot as plt
import numpy as np

SERVER = "http://localhost:8000"
DEFAULT_RATE = 48000  # wird benutzt, wenn der Server nicht erreichbar ist


def _request(path, data=None):
    url = SERVER.rstrip("/") + path
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        # Der Server schickt eine verständliche Fehlermeldung mit
        message = json.loads(e.read()).get("error", str(e))
        raise RuntimeError(f"Server meldet: {message}") from None
    except urllib.error.URLError as e:
        raise ConnectionError(f"Server {SERVER} nicht erreichbar ({e.reason}). "
                              f"Stimmt audio.SERVER?") from None


_rate_cache = {}


def samplerate():
    """Samples pro Sekunde, mit denen der Server arbeitet."""
    if SERVER not in _rate_cache:
        _rate_cache[SERVER] = _request("/info")["samplerate"]
    return _rate_cache[SERVER]


def send(samples):
    """Spielt die Samples über den Lautsprecher ab.
    Kehrt erst zurück, wenn alles abgespielt wurde."""
    samples = [float(x) for x in samples]  # klappt auch mit numpy-Arrays
    answer = _request("/send", {"samples": samples})
    if answer["clipped"] > 0:
        print(f"Achtung: {answer['clipped']} Samples lagen außerhalb von -1..1 "
              f"und wurden abgeschnitten.")


def receive(ms):
    """Nimmt ab jetzt `ms` Millisekunden mit dem Mikrofon auf
    und gibt die Samples als numpy-Array zurück."""
    return np.array(_request(f"/receive?ms={ms}")["samples"])


# ---------------------------------------------------------------------- Plotten

def _rate(rate):
    """Samplerate für die Achsenbeschriftung: angegeben, sonst vom Server, sonst 48000."""
    if rate is not None:
        return rate
    try:
        return samplerate()
    except ConnectionError:
        return DEFAULT_RATE


def plot(samples, rate=None, start_ms=None, end_ms=None, title=None):
    """Zeichnet das Signal über der Zeit (in Millisekunden).

    Mit start_ms / end_ms kann man einen Ausschnitt anzeigen, z.B.
    audio.plot(samples, start_ms=100, end_ms=105). Sind nur wenige Samples
    zu sehen, werden sie als Punkte markiert.
    """
    rate = _rate(rate)
    x = np.asarray(samples, dtype=float)
    t_ms = np.arange(len(x)) * 1000 / rate

    first = 0 if start_ms is None else max(0, int(start_ms * rate / 1000))
    last = len(x) if end_ms is None else min(len(x), int(np.ceil(end_ms * rate / 1000)) + 1)

    fig, ax = plt.subplots(figsize=(10, 3.5))
    shown = slice(first, last)
    marker = "o" if last - first <= 200 else None
    ax.plot(t_ms[shown], x[shown], marker=marker, markersize=4)
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_xlabel("Zeit [ms]")
    ax.set_ylabel("Wert")
    ax.set_ylim(min(-1.05, x[shown].min() * 1.05), max(1.05, x[shown].max() * 1.05))
    ax.set_title(title or f"{len(x)} Samples, {rate} Hz")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    plt.show()


def plot_spectrum(samples, rate=None, fmin=0, fmax=None, db=False, title=None):
    """Zeichnet, welche Frequenzen im Signal stecken (FFT):
    oben wie stark (Amplitude), unten mit welcher Phase.

    Ein Sinus mit Amplitude A, der die ganze Aufnahme ausfüllt, ergibt eine
    Spitze der Höhe ungefähr A (läuft er nur in einem Teil, entsprechend weniger).
    db=True zeigt die Stärke logarithmisch in Dezibel (gut für leise Anteile).

    Die Phase bezieht sich auf den Anfang der Aufnahme: cos ergibt 0°,
    sin ergibt -90°. Genau stimmt sie nur, wenn eine ganze Zahl von
    Schwingungen in die Aufnahme passt (Frequenz = Vielfaches von
    rate / len(samples)). Gezeichnet wird sie nur an den Spitzen des
    Spektrums (über 1 % der größten Amplitude) – beim Rauschen und an den
    Flanken einer Spitze wäre der Wert nicht aussagekräftig.
    """
    rate = _rate(rate)
    x = np.asarray(samples, dtype=float)
    window = np.hanning(len(x))
    spectrum = np.fft.rfft(x * window)
    amplitude = 2 * np.abs(spectrum) / window.sum()
    phase = np.degrees(np.angle(spectrum))
    freqs = np.fft.rfftfreq(len(x), 1 / rate)
    # Spitzen: mindestens so hoch wie beide Nachbarn und nicht zu leise
    padded = np.concatenate(([0], amplitude, [0]))
    peaks = ((amplitude >= padded[:-2]) & (amplitude >= padded[2:])
             & (amplitude > 0.01 * amplitude.max()))

    fig, (ax_amp, ax_phase) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    if db:
        ax_amp.plot(freqs, 20 * np.log10(amplitude + 1e-12))
        ax_amp.set_ylabel("Stärke [dB]")
        ax_amp.set_ylim(bottom=max(-140, 20 * np.log10(amplitude.max() + 1e-12) - 100))
    else:
        ax_amp.plot(freqs, amplitude)
        ax_amp.set_ylabel("Amplitude")
    ax_amp.set_title(title or "Spektrum")

    ax_phase.plot(freqs[peaks], phase[peaks], "o", markersize=5)
    ax_phase.set_ylim(-200, 200)
    ax_phase.set_yticks([-180, -90, 0, 90, 180])
    ax_phase.set_ylabel("Phase [°]")
    ax_phase.set_xlabel("Frequenz [Hz]")

    for ax in (ax_amp, ax_phase):
        ax.set_xlim(fmin, fmax if fmax is not None else rate / 2)
        ax.grid(alpha=0.3)
    fig.tight_layout()
    plt.show()


def plot_spectrogram(samples, rate=None, fmin=0, fmax=None, title=None):
    """Zeichnet ein Spektrogramm: Zeit nach rechts, Frequenz nach oben,
    Farbe = Stärke. Wie das Fenster am Server, nur für deine Samples."""
    rate = _rate(rate)
    x = np.asarray(samples, dtype=float)
    nfft = min(2048, max(16, len(x) // 4))  # bei kurzen Signalen kleineres Fenster

    fig, ax = plt.subplots(figsize=(10, 4))
    _, _, _, image = ax.specgram(x, NFFT=nfft, Fs=rate, noverlap=nfft * 3 // 4,
                                 cmap="magma", scale="dB")
    # Zeitachse in ms statt s
    image.set_extent([e * 1000 if k < 2 else e for k, e in enumerate(image.get_extent())])
    ax.set_xlim(0, len(x) * 1000 / rate)
    ax.set_ylim(fmin, fmax if fmax is not None else rate / 2)
    ax.set_xlabel("Zeit [ms]")
    ax.set_ylabel("Frequenz [Hz]")
    ax.set_title(title or "Spektrogramm")
    fig.colorbar(image, ax=ax, label="Stärke [dB]")
    fig.tight_layout()
    plt.show()


# Kleiner Test: python audio.py
if __name__ == "__main__":
    import math
    import threading

    rate = samplerate()
    print("Verbunden, Samplerate:", rate)

    # 0,5 s Ton mit 1000 Hz
    ton = [0.5 * math.sin(2 * math.pi * 1000 * i / rate) for i in range(rate // 2)]

    # Gleichzeitig aufnehmen (in einem Thread) und senden
    ergebnis = []
    aufnahme = threading.Thread(target=lambda: ergebnis.extend(receive(1000)))
    aufnahme.start()
    send(ton)
    aufnahme.join()

    print(f"{len(ergebnis)} Samples empfangen, lautestes: {max(abs(x) for x in ergebnis):.3f}")

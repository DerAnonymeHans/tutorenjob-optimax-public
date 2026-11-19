# AudioKommunikation

Ziel: eine **Übertragungsstrecke Lautsprecher → Luft → Mikrofon** bauen. Die Schüler
entwickeln Sender (Modulation, z.B. Töne für 0 und 1) und Empfänger (Demodulation),
brauchen dafür aber keine eigene Audio-Hardware.

Es gibt **einen** Lautsprecher und **ein** Mikrofon, beide am Tutor-Laptop. Dort läuft
ein Server, über den alle Schüler senden und empfangen. Ein Fenster zeigt laufend das
Spektrogramm des Mikrofons – so sieht die ganze Gruppe, was gerade übertragen wird.

```
Schüler-Laptop ──HTTP──►  Server (Tutor-Laptop)  ──►  Lautsprecher
                                                          │  Schall
Schüler-Laptop ◄─HTTP──   Server (Tutor-Laptop)  ◄──  Mikrofon
```

## Server starten (Tutor-Laptop)

Python 3.10+ und die Pakete aus `requirements.txt`:

```
pip install -r requirements.txt
cd server
python main.py
```

Im Terminal und im Fenstertitel steht die Adresse für die Schüler, z.B.
`http://192.168.178.122:8000`.

| Option | Bedeutung |
|---|---|
| `--list-devices` | Audio-Geräte mit Nummern auflisten |
| `--input N` / `--output N` | Mikrofon / Lautsprecher wählen (Nummer oder Teil des Namens) |
| `--port 8000` | HTTP-Port |
| `--samplerate 48000` | Abtastrate (gilt für Senden und Empfangen) |
| `--history 60` | Wie viele Sekunden Mikrofon-Daten gespeichert werden (= max. Spektrogramm-Dauer) |
| `--simulate` | Ohne Hardware: Gesendetes kommt leiser und leicht verrauscht sofort wieder an |

**Checkliste im Unterricht**

- Alle Geräte im selben WLAN. Beim ersten Start fragt die Windows-Firewall –
  Python für **private Netzwerke** erlauben (bei Schul-WLAN ggf. auch öffentliche).
- Lautstärke am Laptop vorher einstellen; im Spektrogramm sollte ein Testton deutlich
  über dem Rauschen liegen, aber nicht übersteuern.
- Sendungen mehrerer Schüler werden **nacheinander** abgespielt (Warteschlange), nicht
  gemischt. Empfangen können beliebig viele gleichzeitig – alle bekommen dasselbe Mikrofonsignal.

## Spektrogramm-Fenster

- **Dauer**, **f min / f max** und **dB min / dB max** (Farbskala) sind oben einstellbar
  (Enter oder Pfeile übernehmen den Wert).
- **Pause** friert das Bild ein. Mit der Lupe in der Werkzeugleiste unten kann man dann
  hineinzoomen, mit dem Haus zurück.
- Balken oben im Bild: **blau** = hier wurde gesendet, **orange** = hier hat jemand empfangen.
  (Der Sendebalken ist um die geschätzte Ausgabelatenz verschoben – nur ungefähr.)
- Unten: Protokoll, wer wann gesendet/empfangen hat.

## HTTP-Schnittstelle

Alle Antworten sind JSON. Samples sind Zahlen zwischen **-1 und 1** (Werte außerhalb
werden begrenzt und in `clipped` gezählt). Eine Sekunde Audio = `samplerate` Samples.

| Aufruf | Antwort |
|---|---|
| `GET /info` | `{"samplerate": 48000, "max_send_seconds": 10, "max_receive_ms": 10000}` |
| `POST /send` mit Body `{"samples": [0.0, 0.3, ...]}` | `{"ok": true, "samples": n, "duration_ms": ..., "clipped": 0}` – antwortet erst, wenn alles abgespielt ist |
| `GET /receive?ms=1000` | `{"samplerate": 48000, "start_sample": ..., "samples": [...]}` – nimmt **ab dem Aufruf** 1000 ms auf |

`start_sample` ist die fortlaufende Nummer des ersten Samples seit Serverstart; damit
lassen sich mehrere Aufnahmen zeitlich einordnen.

Zum Testen eines eigenen Senders gleichzeitig `receive` (z.B. in einem Thread) starten
und dann `send` aufrufen.

## Aufbau des Servers (`server/`)

| Datei | Inhalt |
|---|---|
| `main.py` | Kommandozeile, startet alles |
| `audio_engine.py` | Mikrofon-Ringpuffer, Sende-Warteschlange, Simulationsmodus |
| `http_api.py` | die HTTP-Endpunkte (nur Standardbibliothek) |
| `spectrogram_window.py` | Tkinter-Fenster mit Matplotlib-Spektrogramm (FFT 2048, Schritt 512) |

## Für Schüler (`client/`)

Der Ordner `client/` wird an die Schüler weitergegeben: `audio.py` (Senden, Empfangen,
Plotten), `requirements.txt` (numpy, scipy, matplotlib, Jupyter) und eine kurze
Anleitung in `client/README.md`. `python audio.py` macht einen Verbindungstest.

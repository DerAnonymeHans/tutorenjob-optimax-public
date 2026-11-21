# Audiokommunikation – Start

## 1. Einmalig: Pakete installieren

Python 3.10 oder neuer muss installiert sein. Dann in einem Terminal in diesem Ordner:

```
pip install -r requirements.txt
```

## 2. Jupyter starten

Im selben Ordner:

```
jupyter notebook
```

Im Browser öffnet sich Jupyter. Dort **Neu → Notebook** wählen. Das Notebook muss im
selben Ordner wie `audio.py` liegen.

(Mit VS Code geht es auch: einfach eine neue `.ipynb`-Datei in diesem Ordner anlegen.)

## 3. Aufgaben

Die Aufgaben für Woche 1 stehen in `Woche_1_Senden.ipynb`, einfach in Jupyter öffnen.
Für eigene Versuche könnt ihr auch ein neues Notebook anlegen:

## 4. Erste Zelle

```python
import numpy as np
import audio

audio.SERVER = "http://192.168.178.122:8000"   # Adresse steht an der Tafel
rate = audio.samplerate()
print(rate)
```

## 5. Ausprobieren

```python
# 0,5 Sekunden Ton mit 1000 Hz
t = np.arange(rate // 2) / rate
ton = 0.5 * np.sin(2 * np.pi * 1000 * t)

audio.plot(ton, end_ms=5)        # die ersten 5 ms ansehen
audio.send(ton)                  # abspielen

samples = audio.receive(1000)    # 1 Sekunde aufnehmen
audio.plot(samples)
audio.plot_spectrum(samples, fmax=5000)
audio.plot_spectrogram(samples, fmax=5000)
```

## Alle Funktionen

| Funktion | Was sie macht |
|---|---|
| `audio.samplerate()` | Samples pro Sekunde (meist 48000) |
| `audio.send(samples)` | Samples (Werte von -1 bis 1) über den Lautsprecher abspielen |
| `audio.receive(ms)` | ab jetzt `ms` Millisekunden aufnehmen, gibt ein numpy-Array zurück |
| `audio.plot(samples, start_ms=…, end_ms=…)` | Signal über der Zeit, optional nur ein Ausschnitt |
| `audio.plot_spectrum(samples, fmax=…, db=False)` | welche Frequenzen wie stark vorkommen (oben) und mit welcher Phase (unten) |
| `audio.plot_spectrogram(samples, fmax=…)` | Frequenzen über der Zeit |
| `audio.iq(samples)` | macht aus den Samples komplexe IQ-Werte (I = Signal, Q = um 90° verschoben) |
| `audio.plot_amplitude_phase(signal)` | Amplitude und Phase des Drehzeigers über der Zeit |
| `audio.plot_iq(signal, symbol_ms=…)` | IQ-Diagramm: jedes Sample in der komplexen Ebene, Farbe = Zeit; nimmt auch komplexe Werte |

Senden und gleichzeitig aufnehmen (zum Testen der eigenen Übertragung):

```python
import threading

ergebnis = {}
aufnahme = threading.Thread(target=lambda: ergebnis.update(s=audio.receive(1500)))
aufnahme.start()
audio.send(ton)
aufnahme.join()
audio.plot_spectrogram(ergebnis["s"], fmax=5000)
```

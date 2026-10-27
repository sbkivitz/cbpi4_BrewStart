# cbpi4_BrewStart

CraftBeerPi4-Plugin mit zwei Bausteinen:

1. **`BrewStart`** – Mash-Step: Brauvorgang zu einer geplanten Startzeit starten  
2. **`StepStatusActor`** – virtueller Actor: ON, solange ein passender Step (z.B. MashStep) aktiv ist — nutzbar als Abhängigkeit mit [cbpi4-DependentActor](https://github.com/PiBrewing/cbpi4-DependentActor)

Als erster Mash-Step in deinem Rezept wartet `BrewStart` bis zur eingestellten Uhrzeit (optional mit Datum), zeigt einen Countdown an und startet danach automatisch den nächsten Step — oder wartet auf deine Bestätigung.

## Installation

Direkt vom GitHub-Repo (auf dem Pi):

```bash
pipx runpip cbpi4 install https://github.com/braubunker/cbpi4_BrewStart/archive/main.zip
sudo systemctl restart craftbeerpi
```

Repo: https://github.com/braubunker/cbpi4_BrewStart

Lokal / editierbar:

```bash
pipx runpip cbpi4 install .
# oder:
pipx runpip cbpi4 install -e /pfad/zu/cbpi4_BrewStart
```

Danach CraftBeerPi4 neu starten (`cbpi start` bzw. Service neu starten).

## Verwendung

1. Rezept / Mash Profile öffnen
2. Als **ersten Step** einen neuen Step vom Typ **`BrewStart`** hinzufügen
3. Parameter setzen:

| Parameter | Bedeutung |
|-----------|-----------|
| **Mode** | `Today` = heute zur Uhrzeit (wenn schon vorbei → morgen). `DateTime` = festes Datum + Uhrzeit. `Daily` = nächste passende Uhrzeit. |
| **StartDate** | Nur bei `DateTime`: z.B. `2026-10-11` oder komplett `2026-10-11 06:00` / `11.10.2026 06:00` |
| **StartTime** | Uhrzeit als `HH:MM`, z.B. `06:00`. Mit `Timing = Ready by` die Fertig-Zeit |
| **Timing** | `Start at` = der nächste Step startet zur Zeit oben (wie bisher). `Ready by` = das Wasser soll zur Zeit oben fertig sein; der Heizbeginn wird berechnet (siehe unten) |
| **NotifyBefore** | Minuten vorher warnen (Standard gedanklich 15; `0` = aus) |
| **AutoNext** | `Yes` = nach Erreichen der Zeit sofort weiter. `No` = Notification mit Button „Next Step“ |
| **Campden_Reminder** | `Yes` = Erinnerung mit Dosis: 1 Campden-Tablette pro 20 gal / 75 L Einmaischwasser |

### Ready by – Wasser zur Uhrzeit fertig

Mit `Timing = Ready by` ist die Zeit oben der Zeitpunkt, an dem das Wasser
`Ready_Temp` erreicht haben soll. Der Step misst die Wassertemperatur über die
Sensoren der Kessel und rechnet zurück, wann geheizt werden muss – und plant
jede Minute neu, damit Wasser, das über Nacht abkühlt, früher startet statt zu
spät fertig zu sein.

| Parameter | Bedeutung |
|-----------|-----------|
| **Heat_Kettle** | Kessel mit dem Heizstab (HERMS: HLT); sein Sensor liefert die Starttemperatur |
| **Volume** | Wasser im Heat_Kettle |
| **Strike_Kettle** | optional: Maischebottich, wenn das Einmaischwasser über die HERMS-Spirale geheizt wird |
| **Strike_Volume** | optional: Einmaischwasser – dessen Aufheizzeit kommt hinzu |
| **Ready_Temp** | Zieltemperatur (HERMS: Einmaischtemperatur) |
| **Volume_Unit** | `gal` oder `L`; leer = nach Temperatureinheit (F: gal) |
| **Heater_Watts** | Leistung des Heizstabs in Watt, am besten gemessen (Volt × Ampere) |
| **Efficiency_Pct** | Anteil der Leistung, der im Wasser ankommt (leer = 90 %, an einem 20-gal-HLT gemessen). Kleiner machen, wenn das Aufheizen länger dauerte als geplant |
| **Margin_Minutes** | Reserve in Minuten (leer = 10) |

Ohne gültigen Messwert plant der Step, als hätte das Wasser 2 °C / 35.6 °F –
also früher statt zu spät – und meldet es.

Im Simulator (cbpi4-simvessel, `SIM_TIME_SCALE`) gelten die Zeiten auf der Uhr
der Simulation, nach der auch das Wasser heizt: bei 60× kommt der Heizbeginn
60-mal so schnell.

**In English.** With `Timing = Ready by`, the time above is when the water
must be at `Ready_Temp`. The step reads the water's temperature from the
kettles' sensors and works back to when heating must start, from the volumes
and the element's power: `Heat_Kettle` is the kettle with the element (the
HLT on a HERMS) and `Volume` its water; `Strike_Kettle` and `Strike_Volume`
add strike water heated through a HERMS coil. It re-plans every minute, so
water that cools overnight starts earlier. With no usable reading it plans
from 2 °C / 35.6 °F and says so. `Efficiency_Pct` (blank = 90, measured on a
20 gal HLT) is the setting to calibrate after a test run. In a simulation
(cbpi4-simvessel, `SIM_TIME_SCALE`) the times are on the simulation's clock,
which the water heats by.

4. Brauprozess starten — der Step wartet und zeigt z.B.  
   `Start 11.10.2026 06:00 · noch 7h 12m 05s`
5. Zur Startzeit geht es weiter mit dem nächsten Mash-/Boil-Step

### Typischer Ablauf

```
BrewStart (warten bis 06:00)
  → MashIn / Aufheizen
  → Maischschritte
  → ...
```

So kannst du abends alles vorbereiten, den Prozess starten und morgens beginnt das eigentliche Brauen zur gewünschten Zeit.

## Step-Status als Actor (Mash / Boil / Whirlpool)

In CraftBeerPi4 können Abhängigkeiten nur über **Actors** laufen (nicht direkt über Steps).  
Diese virtuellen Actors sind **ON**, solange der passende Step aktiv ist:

| Actor-Typ | ON wenn … |
|-----------|-----------|
| **`MashStatusActor`** | MashIn / MashStep (Typ oder Name) |
| **`BoilStatusActor`** | BoilStep / BM_BoilStep |
| **`WhirlpoolStatusActor`** | Step-Typ oder **-Name** enthält Whirlpool (z.B. WaitStep „Whirlpool“) |
| **`StepStatusActor`** | frei konfigurierbar (`StepKind`: Mash / Boil / Whirlpool / Kombinationen / Custom) |

### Einrichten

1. Hardware → Actor → z.B. **`BoilStatusActor`** oder **`WhirlpoolStatusActor`** anlegen (kein Extra-Parameter nötig)
2. Für Whirlpool den Step im Rezept z.B. **WaitStep** nennen: `Whirlpool`
3. Optional DependentActor:

```bash
pipx runpip cbpi4 install https://github.com/PiBrewing/cbpi4-DependentActor/archive/main.zip
```

4. DependentActor:
   - **Base** = echte Pumpe / Ventil / Heizstab  
   - **ActorDependency** = `MashStatusActor` / `BoilStatusActor` / `WhirlpoolStatusActor`  
   - **DependencyType** = `Prerequisite`

Im Kettle / Dashboard den **DependentActor** verwenden.

## Hinweise

- Die Systemzeit des Raspberry Pi / Hosts muss stimmen (Zeitzone prüfen: `timedatectl`).
- Der Step muss **aktiv** sein (Brauprozess gestartet). Im gestoppten Zustand läuft kein Countdown.
- Bei `AutoNext = No` bleibt der Step nach Erreichen der Zeit stehen, bis du in der Notification auf **Next Step** tippst.
- `StepStatusActor` lässt sich nicht manuell schalten — der Status kommt nur vom aktiven Step.

## Entwicklung / Tests

Die Zeit- und Match-Logik ist ohne laufendes CraftBeerPi testbar:

```bash
python -m pip install pytest
python -m pytest -q
```

## Changelog

* **1.2.0.post3** – Im Simulator folgt der Step der Uhr der Simulation (`SIM_TIME_SCALE`) statt der PC-Uhr. Vorher wartete er bei 60× echte Minuten auf einen Start, der simulierte Minuten entfernt war, und das Wasser war lange vor der Fertig-Zeit heiß. Am Rig ändert sich nichts: dort ist die Uhr der Simulation die echte.
* **1.2.0.post2** – `Timing = Ready by`: Heizbeginn aus Wassertemperatur, Wassermenge und Heizleistung, mit Einmaischwasser über eine HERMS-Spirale; Campden-Erinnerung. Der Step prüft die Uhr selbst, ohne Timer-Neubau: eine Uhrkorrektur löste vorher jede Sekunde eine Meldung aus (24 Meldungen für eine Korrektur um 100 s) und ließ einen Countdown weiterlaufen.
* **1.2.0** – Mash-/Boil-/Whirlpool-StatusActors + StepKind-Presets (Whirlpool auch per Step-Name)
* **1.1.1** – BrewStart-Fix: Timer wie DatetimeWaitStep, robustere Props, keine kaputte cbpi-Dependency
* **1.1.0** – `StepStatusActor`: aktiver Mash-/Rezept-Step als Actor-Status für DependentActor
* **1.0.0** – Erste Version: Modes Today / DateTime / Daily, Countdown, Vorwarnung, AutoNext

## Troubleshooting BrewStart

Wenn der Step fehlt oder nicht weitergeht:

```bash
# neu installieren
pipx runpip cbpi4 uninstall cbpi4_BrewStart || true
pipx runpip cbpi4 install -e ~/cbpi4_BrewStart
# Plugin geladen?
pipx runpip cbpi4 list | grep -i brew
# Logs
journalctl -u craftbeerpi -n 100 --no-pager
# oder:
cbpi start
```

Im Step müssen **Mode** und **StartTime** (z.B. `06:30`) gesetzt sein.  
DateTime-Modus: Datum in **StartDate** als `11.10.26-06:30` oder `2026-10-11 06:30`.

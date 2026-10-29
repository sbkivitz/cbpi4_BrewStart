# cbpi4_BrewStart

A CraftBeerPi 4 plugin with two parts:

1. **`BrewStart`** – a mash step that starts the brew at a set time, or has the water hot by then.
2. **`StepStatusActor`** – a virtual actor that is ON while a matching step (a mash step, say) is running. Usable as a dependency with [cbpi4-DependentActor](https://github.com/PiBrewing/cbpi4-DependentActor).

Put first in a mash profile, `BrewStart` waits until the time you set, shows a countdown, and then starts the next step by itself - or waits for you to confirm.

## Installation

From the GitHub repository (on the Pi):

```bash
pipx runpip cbpi4 install https://github.com/braubunker/cbpi4_BrewStart/archive/main.zip
sudo systemctl restart craftbeerpi
```

Original repository: https://github.com/braubunker/cbpi4_BrewStart

From a local copy:

```bash
pipx runpip cbpi4 install /path/to/cbpi4_BrewStart
```

Not with `-e`: CraftBeerPi finds plugins with `pkgutil`, which does not see editable installs, so an editable install is never loaded. Restart CraftBeerPi afterwards (`cbpi start`, or restart the service).

## Use

1. Open the recipe or the **Mash Profile**.
2. Add a step of type **`BrewStart`** as the **first step**.
3. Fill in its settings. The form shows only the ones that apply: StartDate only for a Date, the heating settings only for Ready by.

| Setting | What it does |
|---------|--------------|
| **Mode** | `Today`, `Tomorrow`, or a `Date` you pick. Today and Tomorrow count from when you start the brew. Blank: Today. |
| **StartDate** | With `Date` only: the day, picked with the browser's date picker. |
| **StartTime** | Picked with the browser's time picker. With `Start at`, when the next step starts; with `Ready by`, when the water must be hot. |
| **Timing** | `Start at`: the next step starts at StartTime. `Ready by`: the water reaches Ready_Temp by StartTime, and the step works out when heating has to start (below). |
| **Campden_Reminder** | `Yes`: when you save the step, a notification gives the Campden dose for Volume - 1 tablet per 20 gal / 75 L, rounded up to a quarter tablet. |
| **NotifyBefore** | A heads-up notification this many minutes before the start - with Ready by, before heating starts. Blank: 15. `0`: none. |
| **AutoNext** | `Yes`: go on to the next step at the time, by itself. `No`: wait for you to press **Next Step** in the notification. |

The date and time pickers need this fork's CraftBeerPi (4.7.7.post15 or later) and its interface (cbpi4gui 0.5.1.a3.post3 or later). On an older server the two fields are text, with the format in their description: `YYYY-MM-DD` and 24-hour `HH:MM`.

### A time that has already passed

If the time you asked for is already over when you start the brew - a date left from an earlier brew, or `Today` chosen in the evening when you meant the next morning - nothing starts by itself, whatever AutoNext says. A notification says so, with a **Next Step** button to go on now. To start the next day instead, stop the brew, set Mode to `Tomorrow` and start it again.

The step keeps the date and time it worked out. If the server restarts while it waits - after a power cut it asks **Resume brew?** - Resume waits for that same moment: a `Tomorrow` worked out again at 05:30 on brew day would otherwise mean the day after. If the moment passed while the server was down, Resume goes on at once. Resetting the profile forgets it.

### Ready by – hot water at a set time

With `Timing = Ready by`, StartTime is when the water must be at `Ready_Temp`. The step reads the water's temperature from the kettle's sensor and works back to when heating has to start. It re-plans every minute, so water that cools overnight starts heating earlier rather than being ready late.

| Setting | What it does |
|---------|--------------|
| **Heat_Kettle** | The kettle with the element - the HLT, holding all the strike and sparge water. Its sensor gives the water's starting temperature. |
| **Ready_Temp** | The temperature the water must reach by StartTime. |
| **Volume** | All the water in Heat_Kettle, strike and sparge together. |
| **Volume_Unit** | `gal` or `L`. Blank: gal with °F, L with °C. |
| **Heater_Watts** | The element's power in watts - volts × amps, measured if you can. |

The plan assumes 90 % of the element's power ends up in the water (measured on a 20 gal HLT) and adds 10 spare minutes. Neither is a setting. With no usable reading the step plans as if the water were at 2 °C / 35.6 °F - so heating starts early rather than late - and says so.

In a simulation (cbpi4-simvessel, `SIM_TIME_SCALE`) the times are on the simulation's clock, which the water heats by: at 60× the heating start comes 60 times as fast.

4. Start the brew. The step waits and shows, for example,
   `Start Sat Oct 24, 06:00 · 7h 12m 05s to go`
5. At the time, the next mash or boil step begins.

### Typical profile

```
BrewStart (wait until 06:00)
  → MashIn / heating
  → mash steps
  → ...
```

Set everything up in the evening, start the brew, and brewing proper begins at the time you chose.

### Language

The forms and messages are in one language, chosen in **Settings** under `BREWSTART_LANGUAGE`: English (the default) or Deutsch. Messages follow a change at once; the forms show it after a restart.

## Step status as an actor (Mash / Boil / Whirlpool)

CraftBeerPi 4 can only make things depend on **actors**, not on steps. These virtual actors are **ON** while the matching step is running:

| Actor type | ON while … |
|------------|------------|
| **`MashStatusActor`** | a MashIn or Mash step runs (by type or name) |
| **`BoilStatusActor`** | a BoilStep / BM_BoilStep runs |
| **`WhirlpoolStatusActor`** | a step whose type or **name** contains Whirlpool runs (a WaitStep named "Whirlpool", say) |
| **`StepStatusActor`** | you choose (`StepKind`: Mash / Boil / Whirlpool / combinations / Custom, with MatchMode and MatchValue for Custom) |

### Setting up

1. Hardware → Actor → add, for example, **`BoilStatusActor`** or **`WhirlpoolStatusActor`** (no settings needed).
2. For a whirlpool, name the step in the recipe - a **WaitStep**, say - `Whirlpool`.
3. Optionally, DependentActor:

```bash
pipx runpip cbpi4 install https://github.com/PiBrewing/cbpi4-DependentActor/archive/main.zip
```

4. In the DependentActor:
   - **Base** = the real pump, valve or element
   - **ActorDependency** = `MashStatusActor` / `BoilStatusActor` / `WhirlpoolStatusActor`
   - **DependencyType** = `Prerequisite`

Use the **DependentActor** in the kettle and on the dashboard.

## Notes

- The Raspberry Pi's clock and time zone must be right (`timedatectl`).
- The step must be **running** - the brew started. Nothing counts down while it is stopped.
- With `AutoNext = No` the step stays put at the time until you press **Next Step** in the notification.
- `StepStatusActor` cannot be switched by hand; only the running step sets it.

## Development / tests

The time and matching logic can be tested without a running CraftBeerPi; the step's own tests need CraftBeerPi importable and are skipped without it:

```bash
python -m pip install pytest
python -m pytest -q
```

## Changelog

* **1.2.0.post4** – The form asks only for what applies, in one language (`BREWSTART_LANGUAGE`, English by default): Mode is `Today` / `Tomorrow` / `Date`; StartDate and StartTime use the browser's own date and time pickers; StartDate shows only for a Date and the heating settings only for Ready by. Ready by plans all the water in the HLT: Strike_Kettle, Strike_Volume, Efficiency_Pct and Margin_Minutes are gone from the form, and values saved by an older form are ignored. The Campden reminder comes when the step is saved, with the dose for Volume. A time already passed when the brew starts waits for Next Step instead of starting at once; `Today` no longer moves a passed time to tomorrow (that is what `Tomorrow` is for), and a blank StartTime is an error instead of 06:00. The moment worked out is kept with the step, so Resume after a power cut waits for the same moment. Dates in English messages read "Sat Oct 24, 06:00". Profiles saved with `DateTime` or `Daily` still work.
* **1.2.0.post3** – In a simulation the step follows the simulation's clock (`SIM_TIME_SCALE`) instead of the PC's. Before, at 60× it waited real minutes for a start a few simulated minutes away, and the water was hot long before the ready time. On a rig nothing changes: there the simulation's clock is the real one.
* **1.2.0.post2** – `Timing = Ready by`: the heating start from the water's temperature, its volume and the element's power, with strike water heated through a HERMS coil; Campden reminder. The step checks the clock itself, with no Timer rebuilt: a clock correction used to raise a notification every second (24 for one 100 s correction) and leave a countdown running.
* **1.2.0** – Mash, Boil and Whirlpool status actors, and StepKind presets (Whirlpool also by step name).
* **1.1.1** – BrewStart fix: timer like DatetimeWaitStep, sturdier settings, no broken cbpi dependency.
* **1.1.0** – `StepStatusActor`: the running mash or recipe step as an actor's state, for DependentActor.
* **1.0.0** – First version: modes Today / DateTime / Daily, countdown, heads-up, AutoNext.

## Troubleshooting BrewStart

If the step is missing or does not go on:

```bash
# reinstall
pipx runpip cbpi4 uninstall cbpi4_BrewStart || true
pipx runpip cbpi4 install ~/cbpi4_BrewStart
# loaded?
pipx runpip cbpi4 list | grep -i brew
# logs
journalctl -u craftbeerpi -n 100 --no-pager
# or:
cbpi start
```

The step needs **Mode** and **StartTime** (e.g. `06:30`) set, and with Mode `Date` a **StartDate** too.

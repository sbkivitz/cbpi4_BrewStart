# cbpi4_BrewStart

A CraftBeerPi 4 plugin with two parts:

1. **`BrewStart`** – a mash step that starts the brew at a set time, or has the water hot by then.
2. **`StepStatusActor`** – a virtual actor that is ON while a matching step (a mash step, say) is running. Usable as a dependency with [cbpi4-DependentActor](https://github.com/PiBrewing/cbpi4-DependentActor).

Put first in a mash profile, `BrewStart` waits until the time you set, shows a countdown, and then starts the next step by itself - or waits for you to confirm. With **Ready by** it heats the HLT itself, holds the water hot, and asks you to move the strike water to the mash tun; only your **Next** starts the next step (from 1.3.0.post1, [below](#ready-by--hot-water-at-a-set-time)).

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
| **Timing** | `Start at`: the next step starts at StartTime. `Ready by`: the water reaches Ready_Temp by StartTime; the step works out when heating has to start, heats Heat_Kettle itself, holds the water and asks you to move it (below). |
| **Campden_Reminder** | `Yes`: when you save the step, a notification gives the Campden dose for Volume - 1 tablet per 20 gal / 75 L, rounded up to a quarter tablet. |
| **NotifyBefore** | A heads-up notification this many minutes before the start - with Ready by, before heating starts. Blank: 15. `0`: none. |
| **AutoNext** | `Start at` only. `Yes`: go on to the next step at the time, by itself. `No`: wait for you to press **Next Step** in the notification. |

The date and time pickers need this fork's CraftBeerPi (4.7.7.post15 or later) and its interface (cbpi4gui 0.5.1.a3.post3 or later). On an older server the two fields are text, with the format in their description: `YYYY-MM-DD` and 24-hour `HH:MM`.

### A time that has already passed

If the time you asked for is already over when you start the brew - a date left from an earlier brew, or `Today` chosen in the evening when you meant the next morning - nothing starts by itself, whatever AutoNext says. A notification says so, with a **Next Step** button to go on now - with Ready by, a **Heat now** button instead, which heats the water now: Next Step would move on to the mash tun with the water cold. To start the next day instead, stop the brew, set Mode to `Tomorrow` and start it again.

The step keeps the date and time it worked out, so a `Tomorrow` worked out again at 05:30 on brew day, after a restart, does not mean the day after. Resetting the profile forgets it.

### A new time while it waits

From 1.2.0.post10. CraftBeerPi doesn't change the settings of a step that is running, and a step waiting for its time is running. So BrewStart has an action, **Change time** (`change_time`), which takes a new day and time while it waits, without stopping the brew. It is how Brew Day sets the time the night before:

```
POST /step2/action/<step id>
{"action": "change_time", "parameter": {"Mode": "Date", "StartDate": "2026-11-23", "StartTime": "06:30"}}
```

* `Mode` is `Today`, `Tomorrow` or `Date`, and `StartDate` is for a `Date` only. A setting left out stays as it is. Timing and the water can't change this way: Timing can't change while the step waits, and CraftBeerPi's strike water helper changes the water's temperature.
* While the step waits, it plans again as starting the brew does: from the water as it is now, with Today and Tomorrow counted from the change. Its notification says so: "Time changed, was Sun Nov 22, 07:00. Ready by Mon Nov 23, 06:30: heating starts about …". It keeps the new moment, so after a power cut it waits for the new time.
* A ready time too soon for the heating starts the heating at once, as starting the brew would.
* Once Ready by has begun heating (1.3.0.post1), a new time only changes the ready time: the heating carries on as it is, and the notification says so: "Time changed, was Sat Oct 24, 07:00. Ready by Sat Oct 24, 07:30: the HLT carries on heating to 152.0 °F." If the new time comes before the water is hot, BrewStart says the water is late, as for the time it replaced; after a power cut it carries on until two hours after the new time.
* Before the brew starts, or while it is stopped, the settings are saved, and starting the brew plans from them.
* Refused, with nothing changed: a time that has passed, a day or time that can't be read, and a step whose time has come (with Start at), that waits for **Next Step** or **Heat now**, or that has finished. Every screen is told why in an info notification, and the call answers 400 - from CraftBeerPi 4.7.7.post61 with the reason: `{"error": "action refused", "detail": "<the notification's words>"}`.

A page can tell a BrewStart that has this action from an older one by the step types CraftBeerPi sends (`GET /step2/` and `/system/`): BrewStart's `actions` include one whose `method` is `change_time`.

### After a power cut

The brew carries on by itself (from 1.2.0.post5, with this fork's CraftBeerPi 4.7.7.post21 or later). If the server stops while the step waits, while it heats or holds the water (1.3.0.post1), or while the step it handed over to is heating, that step picks up again when the server is back - with nobody pressing anything, and a notification saying so:

* It carries on for up to **two hours after the time you set** (the ready time, or the start time). Waiting, it plans again from the water's temperature; if its start time passed while the power was off, it starts heating at once. Heating, it heats again; holding, it holds again, and doesn't ask a second time to move the same water - **Next** on the brew-day screen moves on.
* Later than that it does not start heating by itself. The server asks **Resume brew?**, as it does for every other step, and the step waits for **Next Step**.
* A profile you stopped before the power went off stays stopped.
* A Raspberry Pi has no battery clock and boots with the time it last saved. The server waits, up to ten minutes, for the time to be set from the internet before deciding anything; if it cannot confirm it, it asks instead of heating.

An older server asks **Resume brew?** after a restart, and Resume waits for the same moment; if the moment passed while the server was down, Resume goes on at once.

### Ready by – hot water at a set time

With `Timing = Ready by`, StartTime is when the water must be at `Ready_Temp`. The step reads the water's temperature from the kettle's sensor and works back to when heating has to start. It re-plans every minute, so water that cools overnight starts heating earlier rather than being ready late.

| Setting | What it does |
|---------|--------------|
| **Heat_Kettle** | The kettle with the element - the HLT, holding all the strike and sparge water. Its sensor gives the water's starting temperature, and BrewStart switches its AUTO on to heat it (1.3.0.post1). |
| **Ready_Temp** | The temperature the water must reach by StartTime. |
| **Volume** | All the water in Heat_Kettle, strike and sparge together. |
| **Volume_Unit** | `gal` or `L`. Blank: gal with °F, L with °C. |
| **Grain_Temp** | The grain's temperature, if it spends the night somewhere other than with the boil kettle, in the brewery's unit. Blank: the boil kettle's probe. BrewStart doesn't use it itself: CraftBeerPi's strike water helper (4.7.7.post55 or later) works its forecast out from it. |
| **Heater_Watts** | The element's power in watts - volts × amps, measured if you can. |

The plan assumes 90 % of the element's power ends up in the water (measured on a 20 gal HLT) and adds 10 spare minutes. From 1.3.0.post3 the 90 % is CraftBeerPi's `BREWSTART_EFFICIENCY` setting when it's filled in (see [Efficiency from past brews](#efficiency-from-past-brews)); the spare minutes are not a setting. With no usable reading the step plans as if the water were at 2 °C / 35.6 °F - so heating starts early rather than late - and says so.

From 1.3.0.post5 the plan also counts Heat_Kettle's own metal, which heats with the water, when the kettle's figures on CraftBeerPi's Hardware page give it (4.7.7.post65 or later): what it weighs empty, and what it's made of - stainless steel at 0.12 cal/g °C, aluminium at 0.22, another material at its own specific heat, and a weight with no material taken as stainless steel. A 33 lb stainless HLT adds about 3 % to the heating time of 15 gal. Without a usable weight - blank, 0, unreadable, or a material alone - or with a material it doesn't know, the plan is exactly as before.

In a simulation (cbpi4-simvessel, `SIM_TIME_SCALE`) the times are on the simulation's clock, which the water heats by: at 60× the heating start comes 60 times as fast.

When `SIM_TIME_SCALE` changes, CraftBeerPi (4.7.7.post63 or later) sets its clock back to the computer's time. From 1.3.0.post4 BrewStart then goes on as after a restart. A Ready by that is heating or holding carries on: the HLT's AUTO and target stay, and a question already asked stays up and isn't asked again. Its ready time is worked out again from its settings, as Start does, with Today and Tomorrow counted from the clock as it now is: `Heating to 152.0 °F · ready by …` shows the new time, and if that time has already passed the water is late and BrewStart says so. A waiting step plans again from its settings, as before. A step stopped while heating still heats again on Start. Never on a rig, where the clock always runs at 1×.

#### Heating, holding, and moving the strike water (1.3.0.post1)

At the heating start BrewStart heats Heat_Kettle itself: it sets the kettle's target to Ready_Temp and switches its AUTO on, so the kettle's own logic heats the water and then holds it there. A ready time too soon for the heating starts it at once. Then:

* Its line reads `Heating to 152.0 °F · ready by Sat Oct 24, 07:00`, and once the water reads Ready_Temp (within CraftBeerPi's 0.5 °F / 0.3 °C) `Holding 152.0 °F · move the strike water`.
* At Ready_Temp it asks, once, from the step's name: **"Move the strike water to the Mash Tun, then press Next"**, with one button, **Next Step**. The question waits on every page, a page opened later included, until it is answered or the step stops; the phone alert plugin sends it as a question.
* It never moves on by itself, whatever AutoNext says: only **Next Step**, or **Next** on the brew-day screen, does. Then BrewStart switches Heat_Kettle's AUTO off **before** the next step starts. On a HERMS the mash tun's logic drives the HLT's element too, and two logics must never drive one element at once. **Stop** switches it off too; **Start** heats again, or holds, without a second question.
* If the ready time comes before the water is hot, it carries on heating and says so, with the reading: "The water isn't ready by Sat Oct 24, 07:00: the HLT reads 140.0 °F, short of 152.0 °F. …".
* If the water stops warming - the element selector left on the boil kettle, say - it warns as a MashIn step does: "Heating is not working", with Keep waiting and Stop the profile.
* The HLT pump runs with the HLT's AUTO as `HLT_PUMP_WITH_HEAT` says, as before. BrewStart switches no pump or element itself, the mash tun's included.
* CraftBeerPi's strike water helper (4.7.7.post61 or later) takes such a BrewStart as the strike water step: **USE** and **BACK TO** change its Ready_Temp, and while it heats or holds the HLT's target too. A question already asked is taken down, and asked again once the water reads the new temperature.
* Before a recipe made for an older BrewStart - the next step on a kettle is a MashIn on Heat_Kettle, a strike water step that heats the water and asks itself - BrewStart doesn't heat or ask: it hands over at the heating start, as before 1.3.0.post1, so the water isn't heated or asked about twice.

4. Start the brew. The step waits and shows, for example,
   `Start Sat Oct 24, 06:00 · 7h 12m 05s to go`, or with Ready by `Ready Sat Oct 24, 07:00 · heating from Sat Oct 24, 06:02 · water 64.0 °F · 7h 12m 05s to go`
5. At the time, with Start at, the next mash or boil step begins. With Ready by the water heats, and you press Next once it is in the mash tun.

### Typical profile

```
BrewStart (Ready by 07:00, 152 °F: heats the HLT, holds it, asks for the strike water)
  → MashIn on the mash tun (asks for the malt)
  → mash steps
  → ...
```

Set everything up in the evening, start the brew, and the strike water is hot at the time you chose.

### Language

The forms and messages are in one language, chosen in **Settings** under `BREWSTART_LANGUAGE`: English (the default) or Deutsch. Messages follow a change at once; the forms show it after a restart.

### Times of day

From 1.3.0.post2 BrewStart writes times of day as CraftBeerPi's `CLOCK_FORMAT` setting says (CraftBeerPi 4.7.7.post60 or later): 12-hour, `Sat Nov 21, 7:00 AM`, or 24-hour, `Sat Nov 21, 07:00`; in German `21.11.2026 7:00 AM` or `21.11.2026 07:00`. That's in its notifications, its status line and the reasons it gives. A change shows at once. The setting missing, blank or anything else is 24-hour, as on an older CraftBeerPi. The time you type stays `HH:MM`, 24-hour, and durations (`8h 56m 10s to go`, `about 27 min`) don't change.

### Efficiency from past brews

From 1.3.0.post3 BrewStart adds a setting, `BREWSTART_EFFICIENCY`: "The share of the HLT element's power that heats the water, in %. Blank: the built-in 90 %. USE on Figures sets it from past brews." It's blank at first, and a value already saved is kept when BrewStart starts.

* A Ready by plans the heating with it, 10 to 100 % (`85`, or `0.85`). Blank, unreadable or out of range is the built-in 90 %.
* **USE** on CraftBeerPi's Figures page sets it from what past brews measured, and **BACK TO** blanks it. A BrewStart that's waiting plans again with the new figure within a minute.
* When it's filled in, BrewStart says so wherever it says how long the water takes: "66.0 °F to 152.0 °F takes about 47 min, at 85 % from past brews, plus 10 min spare."
* It can be typed in **Settings** too, on any CraftBeerPi.

### What BrewStart records for Figures

From 1.3.0.post3 a Ready by writes what it planned, and when the water was ready, into CraftBeerPi's logs (`cbpi.log.log_control_data`, which writes `logs/sensor_<name>.log`), each row stamped on CraftBeerPi's clock:

| Name | When | Value |
|------|------|-------|
| `brewstart.planned_min` | as the heating starts | the minutes planned for heating, from the water then |
| `brewstart.ready_by` | as the heating starts | the ready time, in seconds on CraftBeerPi's clock |
| `brewstart.litres` | as the heating starts | the water, in litres |
| `brewstart.watts` | as the heating starts | Heater_Watts |
| `brewstart.efficiency` | as the heating starts | the efficiency planned with, as a fraction (0.9 built in) |
| `brewstart.steel_j_k` | as the heating starts, when it counted Heat_Kettle's metal (1.3.0.post5) | the J/K it counted |
| `brewstart.ready` | the first time the water reads Ready_Temp | the water's reading; the row's time is when it was ready |

"As the heating starts" is once a brew: BrewStart switching Heat_Kettle's AUTO on, or Heat now, or, before a recipe's own strike water step on Heat_Kettle, handing over to it. Nothing is written again after a Stop and Start, or a restart, or when USE changes Ready_Temp and the question is asked again. CraftBeerPi's CSV log keeps a series' rows in memory until its next row or a minute later, so BrewStart has each row written to the disk two seconds after it. On a CraftBeerPi without `log_control_data` nothing is written, and the brew goes on.

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
- With `AutoNext = No` a Start at stays put at the time until you press **Next Step** in the notification. A Ready by always waits for Next once the water is hot.
- `StepStatusActor` cannot be switched by hand; only the running step sets it.

## Development / tests

The time and matching logic can be tested without a running CraftBeerPi; the step's own tests need CraftBeerPi importable and are skipped without it:

```bash
python -m pip install pytest
python -m pytest -q
```

## Changelog

* **1.3.0.post5** – The heating plan counts Heat_Kettle's own metal from its figures on CraftBeerPi's Hardware page (4.7.7.post65 or later): its empty weight at the specific heat of what it's made of, as CraftBeerPi's strike water model reads them. Without a usable figure the plan is exactly as before. When it counted the metal, the record has `brewstart.steel_j_k` too.
* **1.3.0.post4** – A simulator's change of speed leaves the heating alone. CraftBeerPi 4.7.7.post63 sets its clock back to the computer's time whenever `SIM_TIME_SCALE` changes, and stopped, reset and started again every BrewStart, so each planned again from its settings. That took a Ready by heating or holding the HLT for one waiting for its time: Stop switched the HLT's AUTO off, reset forgot it was heating and had asked, and the water was left to cool until a second heating start, with the question asked a second time. BrewStart now handles the reset itself (`clock_moved`): heating or holding, it carries on, with its ready time worked out again from its settings; waiting, it plans again as before (see [Ready by](#ready-by--hot-water-at-a-set-time)).
* **1.3.0.post3** – For CraftBeerPi's Figures page. A new setting, `BREWSTART_EFFICIENCY`, in %: blank is the built-in 90 %, and USE on Figures sets it from past brews. A Ready by plans with it, a waiting one within a minute of a change, and says "at 85 % from past brews" when it's filled in. A Ready by records what it planned as the heating starts (`brewstart.planned_min`, `.ready_by`, `.litres`, `.watts`, `.efficiency`) and the reading the first time the water reads Ready_Temp (`brewstart.ready`), in CraftBeerPi's logs. See [Efficiency from past brews](#efficiency-from-past-brews) and [What BrewStart records for Figures](#what-brewstart-records-for-figures).
* **1.3.0.post2** – Times of day follow CraftBeerPi's `CLOCK_FORMAT` (4.7.7.post60): `Sat Nov 21, 7:00 AM` in 12-hour, `Sat Nov 21, 07:00` in 24-hour as before, and `21.11.2026 7:00 AM` in German - in the notifications, the status line and the reasons for a refused change. Missing or anything but `12-hour` is 24-hour. The status line keeps its shape, so Brew Day still reads a waiting BrewStart's heating start from it. Durations and the typed `HH:MM` don't change (see [Times of day](#times-of-day)).
* **1.3.0.post1** – **Ready by heats the HLT itself.** Before, Ready by handed over to the next step by itself at the heating start, and only planned from Ready_Temp: before a MashIn on the mash tun, that started the mash tun's step - its pump on in an empty tun, heating to the mash-in temperature. Now, at the heating start, BrewStart sets Heat_Kettle's target to Ready_Temp and switches its AUTO on; once the water reads it, holds it and asks "Move the strike water to the Mash Tun, then press Next"; and moves on only on Next, switching Heat_Kettle's AUTO off before the next step starts (Stop switches it off too). It says when the water is late, warns when heating stalls, carries on heating or holding after a restart without asking twice, and a time already passed offers **Heat now** instead of Next Step. `change_target` and `forget_wait` let CraftBeerPi's strike water helper (4.7.7.post61) take it as the strike water step for USE and BACK TO. Once heating has begun, `change_time` moves only the ready time. Before a MashIn on Heat_Kettle (a recipe made for an older BrewStart) it hands over at the heating start as before. See [Heating, holding, and moving the strike water](#heating-holding-and-moving-the-strike-water-130post1).
* **1.2.0.post10** – **Change time** (`change_time`), an action that takes a new day and time while the step waits, so the time can change the night before without stopping the brew. The step plans again, says the new start, and keeps it for a restart. A time that has passed, or a step whose time has come, is refused with the reason in an info notification (see [A new time while it waits](#a-new-time-while-it-waits)).
* **1.2.0.post9** – A new step's form starts on **Ready by**, not Start at. A step saved with no Timing at all still runs as Start at, as it did before Timing existed.
* **1.2.0.post8** – A **Grain temperature** setting beside Water volume (`Grain_Temp`), blank by default, for grain that spends the night away from the boil kettle; CraftBeerPi's strike water helper reads it, and BrewStart itself doesn't. `campden_dose()` gives the Campden tablets for the water volume, rounded as the reminder rounds them, for the same helper to show.
* **1.2.0.post7** – Comments describe the code. No change in behaviour.
* **1.2.0.post6** – Comments describe the code. No change in behaviour.
* **1.2.0.post5** – After a power cut the brew carries on by itself, until two hours after the time set: the step allows the server (CraftBeerPi 4.7.7.post21 or later) to pick it up again, and the step it hands over to. Past those two hours it never starts heating by itself - a Pi corrected to the right time after the wait had resumed included - and waits for Next Step.
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

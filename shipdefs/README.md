# Den of Wolves — ADAMA Discord Bot: Start Here

This folder holds the starting state of every ship in the **Den of Wolves** megagame, as JSON. These files feed **ADAMA**, a Discord bot that runs the game online. Read this file first, then read the design doc.

## Source of truth

**Design doc (Google Doc):** https://docs.google.com/document/d/1_uCQ0_cebQRJUcO71Wx3xVSSTrLO4JYSzdhE4mjfU9g/edit

If this README and the doc disagree, the doc wins. Its tabs are:

| Tab | What it holds |
|---|---|
| Meeting Notes 7/24 | Early brainstorm. **Historical only**: some ideas there were dropped, e.g. "submit res to morale roll". |
| Discord vs Adama | What Discord roles track vs what the bot tracks. Private info must be tracked by ADAMA. |
| Game Implementation | Channel layout, lobby flow, the game loop, PDAs, control channels and ship channels. |
| Technical Implementation → Names | Resources, ration levels, unit names with crew keywords, and shuttle names. |
| Technical Implementation → Containers | The data model: Galaxy, Coordinates, Ship, Player, Station, Shuttle, Unit and Action Card, with every flag. |
| Technical Implementation → IDs | The ID scheme (summarised below). |
| Unanswered Questions | Older open questions. Most have been resolved in the command list. |
| **Command List** (+ 11 subtabs) | **Every command.** The parent tab has the conventions and the index. Each subtab gives each command's signature, Restrictions, Description (step by step), Errors and Other notes. |

The rules themselves come from the *Den of Wolves Rules Handbook* (a PDF the game organiser has). The online version deliberately differs in places. Where it does, the command list wins.

## Files in this folder

One file per ship. Each file is a single object keyed by the ship's ID:

| File | Ship ID | Notes |
|---|---|---|
| aegis.json | 0100 | Military flagship. Has the Brig, Marines and 9 fighters. Its damage modifies morale per 3 damage taken. |
| dione.json | 0200 | Home of the Council Chamber. Its Luxury Zones give +3 morale. |
| icebreaker.json | 0300 | Mining Facilities (multi-mode). |
| quellon.json | 0400 | |
| refinery124.json | 0500 | Fuel Refineries (multi-mode, with morale penalties). Has no Hangar Bay station (it still has a hangar channel). |
| lucas.json | 0600 | Six H-1 Collector 3.0s. A damaged Fuel Storage stops it jumping. |
| shepherd.json | 0700 | Six Food Synthesiser 3.0s. |
| endeavour.json | 0800 | Small ship. Its Laboratories make research points; control tracks them. |
| vulcan.json | 0900 | Small prison ship. Has special Cells and Guard Quarters rules (control handles them). |
| salvador.json | 1000 | Small hospital ship with 5 Sickbays. |
| staralpha.json | 1100 | Small ship. The President's ship. |

Every ship starts at coordinates `12903` with morale 10 and Normal rations. Its starting resources, units and shuttles come from the organiser's *starting units and resources* sheet.

The bot loads these files directly: attach them to `/startinitialization` or `/loadship` (up to 10 per message). Every file is validated first (see `src/adama/shipfiles.py`), and nothing is loaded if any file has a problem. To fix a file during initialization, `/unloadship` the ship and `/loadship` the corrected file. `jsontemplates.json` in the repo root is a blank ship in this format, to start new ships from.

## JSON shape

All 11 files use exactly the same keys in the same order. A trimmed example:

```jsonc
{
  "0100": {                                  // ship ID (2-digit prefix + "00")
    "name": "Aegis",
    "coordinates": "12903",                  // always 5 digits
    "next_coordinates": "",                  // set by /setcoords
    "sabotage_coordinates": "",              // "subbed" coords set by /subcoords; crew never sees them
    "food_ration_level": "normal",           // luxurious|increased|normal|low|minimum|critical|none
    "water_ration_level": "normal",
    "isolated": false, "destroyed": false, "jumping": false,
    "morale_level": 10,                      // clamp 0..10; at 0, alert control
    "morale_rolled_this_turn": false,
    "damage_taken_this_turn": 0,             // only used by the Aegis (-1 morale per 3)
    "morale_roll_modifiers": { "temp_morale_roll_modifier": 0, "perm_morale_roll_modifier": 0 },
    "resources": { "food": 8, "fuel": 8, "materials": 6, "ore": 0, "water": 6 },
    "ration_levels": { "luxurious": {"food": 14, "water": 17}, /* ... */ "none": {"food": 0, "water": 0} },
    "fuel_consumption": { "short": 7, "medium": 10, "long": 14 },
    "players": {},                           // filled at /startgame
    "stations": {
      "0124": {
        "name": "Weapon Batteries", "description": "...",
        "usable": true,          // false = can't be activated (Hull Plating, Storage, Bridge...)
        "reusable": false,       // true = can be activated many times per turn (Hangar Bay)
        "startofturn": true,     // remind the bridge at turn start; not enforced
        "exhausted": false, "damaged": false, "damaged_description": "",
        "alert_gm_on_use": false, "alert_gm_on_damage": false,
        "riot": false,
        "input":  { "food":0,"fuel":0,"materials":0,"ore":0,"water":0, "crew_keyword":"team", "crew_amount":1 },
        "output": { "food":0,"fuel":0,"materials":0,"ore":0,"water":0 },
        "repair_cost": { "food":0,"fuel":0,"materials":2,"ore":0,"water":0, "crew_keyword":"engineer", "crew_amount":1 },
        "modes": [],             // see "Station modes" below
        "units_used_this_turn": []
      }
    },
    "shuttles": { "0101": { "name": "Cyclops", "intercepted": false, "clearance": false, "launching": false,
                            "resources": { /* 5 resources */ }, "units": {} } },
    "units":    { "0170": { "name": "Work Team", "affiliation": "0100", "damaged": false, "exhausted": false } },
    "action_cards": { "0140": { "name": "...", "affiliation": "0100", "affiliation_restricted": true,
                                "retained": true, "once_per_turn": false, "exhausted": false, "description": "..." } }
  }
}
```

### Key rules encoded in the data

- **Crew keywords:** a unit can crew a station if its name contains the station's `crew_keyword`, case-insensitive.

  | Keyword | Matches |
  |---|---|
  | `team` | Any team: Work, Engineering, Medical, Piloting, Science. Trained crew can do untrained work. |
  | `engineer` | Engineering Team |
  | `medic` | Medical Team |
  | `pilot` | Piloting Team |
  | `scien` | Science Team |

  Unusable stations have `crew_keyword: ""` and `crew_amount: 0`.
- **On-foot units** are "Marine Forces" (Aegis only) and "Security Forces". **Squadrons** are "Fighter Squadron". None of these may contain "team", or they could be spent as crew. /disperse accepts any unit whose name contains "Marine" or "Security".
- **Station modes:** for stations with a choice of output. When `modes` isn't empty:
  - The **crew** still comes from the station's `input`.
  - The **resources** come from the chosen mode's `input` and `output`.
  - The mode's `morale_modifier` is added to the ship's `temp_morale_roll_modifier`, so it stacks.
  - If there's exactly one mode, it's used automatically. Otherwise /activate needs a mode name or number.

  Current users:
  - Mining Facility: `materials` (→3 materials) or `ore` (→4 ore).
  - Fuel Refinery: `10 fuel` (2 ore), `12 fuel` (3 ore, −1 morale) or `14 fuel` (4 ore, −2 morale).
  - Luxury Zone: `luxury` (1 food + 1 water, +3 morale).
- **Ration levels set to `null`** are unavailable on that ship: Low and Minimum on the four small ships. /setrationlevel rejects them. The morale roll falls through to the next lower level the ship can afford.
- **Affiliation** is stored separately from the ID, because /defectunit, /cardaffiliation and mid-game (99-prefix) objects would otherwise break it.
- **Action cards:** `affiliation_restricted: false` means the card can be given to anyone (Personal Bodyguard Detail, Prisoner Labour). Any card can only be *taken* from a ship by crew of its affiliated ship.
- **alert_gm_on_damage / alert_gm_on_use:** set whenever a station has an effect the bot doesn't apply itself, e.g. "discard 3 food", a morale penalty, or a Sickbay heal. The bot posts to #control-chat and control applies the effect.
- **Sickbays** are found by the station name `"Sickbay"`. That name is used by /wound and by end-of-turn healing.

## ID scheme

- IDs are 4 digits, globally unique: a 2-digit **prefix** plus a 2-digit **postfix**.
- Ships use prefixes 01–11; a ship's own ID is `XX00`.
- Prefix `00` is for players. Prefix `99` is for anything control conjures after `/startgame`. Before /startgame, conjured objects use their ship's prefix.
- Postfix ranges:

  | Postfix | Used for |
  |---|---|
  | 01–09 | Shuttles, plus **overflow** for any category whose own range is full |
  | 10–39 | Stations |
  | 40–69 | Action cards |
  | 70–99 | Units. Unit IDs are never reused. |

## Things the bot does NOT automate (control handles them)

These are all deliberate decisions:
- Wolf attacks and the battlemap (the bot only opens and closes the #combat channels).
- The Pursuit Track and Destination cards.
- Passive income and losses.
- Research points.
- Vulcan's Cells and Guard Quarters rules.
- Prisoner Labour as crew.
- Most action-card effects. /playcard just alerts control.
- Ongoing damage effects (Team Phase discards, morale penalties for ships other than the Aegis).
- Jumping with a damaged Jump Drive or Fuel Storage: players ask control.
- Phases (action phase vs team phase) are not enforced.

## Suggested first steps

1. Read the Command List parent tab, especially its **conventions** section, then each subtab.
2. Read the Containers tab and compare it against one of these JSON files.
3. The game state is loaded from these JSON files directly (decided; the setup-script idea was dropped).
4. Ask the game organiser before guessing at any rule that isn't written down. They prefer more questions to fewer.

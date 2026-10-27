"""Validating and parsing ship JSON files (the shipdefs format), as /loadship does."""

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from adama import ids
from adama.models.galaxy import Galaxy
from adama.models.resources import RESOURCE_NAMES
from adama.models.ship import DISTANCES, RATION_LEVELS, Ship

COORDINATES_PATTERN = re.compile(r"\d{5}")
SHIP_ID_PATTERN = re.compile(r"\d\d00")
OBJECT_ID_PATTERN = re.compile(r"\d{4}")

# On-foot units and squadrons must never contain "team", or they could be spent as crew.
NON_CREW_UNIT_WORDS = ("marine", "security", "fighter")


def is_coordinates(value: Any) -> bool:
	return isinstance(value, str) and COORDINATES_PATTERN.fullmatch(value) is not None


@dataclass
class ShipFile:
	filename: str
	content: bytes


# A tiny schema language: a leaf is a (description, predicate) pair, a dict is an object
# with exactly those keys, MapOf is an object of ID -> spec, ListOf is an array of spec.
Leaf = tuple[str, Callable[[Any], bool]]


@dataclass
class MapOf:
	spec: Any


@dataclass
class ListOf:
	spec: Any


def _is_int(value: Any) -> bool:
	return isinstance(value, int) and not isinstance(value, bool)


INT: Leaf = ("an integer", _is_int)
NONNEG: Leaf = ("a non-negative integer", lambda v: _is_int(v) and v >= 0)
NULLABLE_NONNEG: Leaf = (
	"a non-negative integer or null",
	lambda v: v is None or NONNEG[1](v),
)
BOOL: Leaf = ("true or false", lambda v: isinstance(v, bool))
STR: Leaf = ("a string", lambda v: isinstance(v, str))
COORDS: Leaf = ("exactly 5 digits", is_coordinates)
OPT_COORDS: Leaf = ('"" or exactly 5 digits', lambda v: v == "" or is_coordinates(v))
RATION_LEVEL: Leaf = (
	f"one of {', '.join(RATION_LEVELS)}",
	lambda v: v in RATION_LEVELS,
)
MORALE: Leaf = ("an integer from 0 to 10", lambda v: _is_int(v) and 0 <= v <= 10)
EMPTY_OBJECT: Leaf = (
	"empty ({}); players are created at /startgame",
	lambda v: isinstance(v, dict) and not v,
)

RESOURCES = {name: NONNEG for name in RESOURCE_NAMES}
COST = {**RESOURCES, "crew_keyword": STR, "crew_amount": NONNEG}
UNIT = {"name": STR, "affiliation": STR, "damaged": BOOL, "exhausted": BOOL}

SHIP_SCHEMA = {
	"name": STR,
	"coordinates": COORDS,
	"next_coordinates": OPT_COORDS,
	"sabotage_coordinates": OPT_COORDS,
	"food_ration_level": RATION_LEVEL,
	"water_ration_level": RATION_LEVEL,
	"isolated": BOOL,
	"destroyed": BOOL,
	"combat_ship": BOOL,
	"jumping": BOOL,
	"morale_level": MORALE,
	"morale_rolled_this_turn": BOOL,
	"damage_taken_this_turn": NONNEG,
	"morale_roll_modifiers": {
		"temp_morale_roll_modifier": INT,
		"perm_morale_roll_modifier": INT,
	},
	"resources": RESOURCES,
	"ration_levels": {
		level: {"food": NULLABLE_NONNEG, "water": NULLABLE_NONNEG}
		for level in RATION_LEVELS
	},
	"fuel_consumption": {distance: NONNEG for distance in DISTANCES},
	"players": EMPTY_OBJECT,
	"stations": MapOf(
		{
			"name": STR,
			"description": STR,
			"usable": BOOL,
			"reusable": BOOL,
			"startofturn": BOOL,
			"exhausted": BOOL,
			"damaged": BOOL,
			"damaged_description": STR,
			"alert_gm_on_use": BOOL,
			"alert_gm_on_damage": BOOL,
			"riot": BOOL,
			"input": COST,
			"output": RESOURCES,
			"repair_cost": COST,
			"modes": ListOf(
				{
					"name": STR,
					"input": RESOURCES,
					"output": RESOURCES,
					"morale_modifier": INT,
				}
			),
			"units_used_this_turn": ListOf(STR),
		}
	),
	"shuttles": MapOf(
		{
			"name": STR,
			"intercepted": BOOL,
			"clearance": BOOL,
			"launching": BOOL,
			"resources": RESOURCES,
			"units": MapOf(UNIT),
		}
	),
	"units": MapOf(UNIT),
	"action_cards": MapOf(
		{
			"name": STR,
			"affiliation": STR,
			"affiliation_restricted": BOOL,
			"retained": BOOL,
			"once_per_turn": BOOL,
			"exhausted": BOOL,
			"description": STR,
		}
	),
}


def _check_schema(value: Any, spec: Any, path: str, problems: list[str]) -> None:
	if isinstance(spec, dict):
		if not isinstance(value, dict):
			problems.append(f"{path} must be an object")
			return
		for key in spec:
			if key not in value:
				problems.append(f"{path}.{key} is missing")
		for key in value:
			if key not in spec:
				problems.append(f"{path}.{key} is not a known field")
		for key, sub in spec.items():
			if key in value:
				_check_schema(value[key], sub, f"{path}.{key}", problems)
	elif isinstance(spec, MapOf):
		if not isinstance(value, dict):
			problems.append(f"{path} must be an object")
			return
		for key, item in value.items():
			_check_schema(item, spec.spec, f"{path}.{key}", problems)
	elif isinstance(spec, ListOf):
		if not isinstance(value, list):
			problems.append(f"{path} must be a list")
			return
		for index, item in enumerate(value):
			_check_schema(item, spec.spec, f"{path}[{index}]", problems)
	else:
		description, predicate = spec
		if not predicate(value):
			problems.append(f"{path} must be {description} (got {json.dumps(value)})")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
	result: dict[str, Any] = {}
	for key, value in pairs:
		if key in result:
			raise ValueError(f'duplicate key "{key}"')
		result[key] = value
	return result


def _check_ids(ship_id: str, body: dict[str, Any], problems: list[str]) -> list[str]:
	"""Checks every object ID's prefix and range. Returns all object IDs found."""
	prefix = ship_id[:2]
	found: list[str] = []

	def check(kind: str, path: str, object_id: str) -> None:
		found.append(object_id)
		if OBJECT_ID_PATTERN.fullmatch(object_id) is None:
			problems.append(f"{path}: ID {object_id} must be exactly 4 digits")
			return
		if object_id[:2] != prefix:
			problems.append(
				f"{path}: ID {object_id} must start with the ship's prefix {prefix}"
			)
		if not ids.postfix_allowed(kind, int(object_id[2:])):
			problems.append(
				f"{path}: ID {object_id} is outside the {kind} range and the 01-09 overflow"
			)

	for station_id in body["stations"]:
		check(ids.STATION, f"stations.{station_id}", station_id)
	for card_id in body["action_cards"]:
		check(ids.ACTION_CARD, f"action_cards.{card_id}", card_id)
	for unit_id in body["units"]:
		check(ids.UNIT, f"units.{unit_id}", unit_id)
	for shuttle_id, shuttle in body["shuttles"].items():
		check(ids.SHUTTLE, f"shuttles.{shuttle_id}", shuttle_id)
		for unit_id in shuttle["units"]:
			check(ids.UNIT, f"shuttles.{shuttle_id}.units.{unit_id}", unit_id)

	for object_id, count in _count(found).items():
		if count > 1:
			problems.append(f"ID {object_id} is used more than once")
	return found


def _check_rules(body: dict[str, Any], problems: list[str]) -> None:
	"""Rules the schema can't express."""
	rations = body["ration_levels"]
	if rations["none"] != {"food": 0, "water": 0}:
		problems.append("ration_levels.none must always consume 0 food and 0 water")
	for resource in ("food", "water"):
		level = body[f"{resource}_ration_level"]
		if rations[level][resource] is None:
			problems.append(
				f"{resource}_ration_level is {level}, which is unavailable (null) on this ship"
			)

	all_units = list(body["units"].items())
	for shuttle in body["shuttles"].values():
		all_units.extend(shuttle["units"].items())
	for unit_id, unit in all_units:
		if SHIP_ID_PATTERN.fullmatch(unit["affiliation"]) is None:
			problems.append(f"units.{unit_id}.affiliation must be a ship ID like 0100")
		name = unit["name"].casefold()
		if "team" in name and any(word in name for word in NON_CREW_UNIT_WORDS):
			problems.append(
				f'units.{unit_id}: "{unit["name"]}" is an on-foot unit or squadron, so it must not '
				'contain "team" (it could be spent as crew)'
			)
	for card_id, card in body["action_cards"].items():
		if SHIP_ID_PATTERN.fullmatch(card["affiliation"]) is None:
			problems.append(
				f"action_cards.{card_id}.affiliation must be a ship ID like 0100"
			)

	for station_id, station in body["stations"].items():
		for field in ("input", "repair_cost"):
			cost = station[field]
			if cost["crew_amount"] > 0 and not cost["crew_keyword"]:
				problems.append(
					f"stations.{station_id}.{field} needs crew but has no crew_keyword"
				)
		mode_names = [mode["name"].casefold() for mode in station["modes"]]
		if any(not name for name in mode_names):
			problems.append(f"stations.{station_id}: every mode needs a name")
		if len(set(mode_names)) != len(mode_names):
			problems.append(f"stations.{station_id}: mode names must be unique")

	shuttle_names = [
		shuttle["name"].casefold() for shuttle in body["shuttles"].values()
	]
	if len(set(shuttle_names)) != len(shuttle_names):
		problems.append("shuttle names must be unique")


def _count(items: list[str]) -> dict[str, int]:
	counts: dict[str, int] = {}
	for item in items:
		counts[item] = counts.get(item, 0) + 1
	return counts


def parse_ship(content: bytes | str) -> tuple[Ship | None, list[str]]:
	"""Parses one ship file on its own. Returns the ship, or None and every problem found."""
	problems: list[str] = []
	try:
		data = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
	except (ValueError, UnicodeDecodeError) as e:
		return None, [f"not valid JSON: {e}"]

	if not isinstance(data, dict) or len(data) != 1:
		return None, ["must hold exactly one ship, keyed by its ship ID"]
	ship_id, body = next(iter(data.items()))
	if SHIP_ID_PATTERN.fullmatch(ship_id) is None or ship_id in ("0000", "9900"):
		problems.append(
			f"ship ID {ship_id} must be 4 digits ending in 00, with a prefix from 01 to 98"
		)

	_check_schema(body, SHIP_SCHEMA, ship_id, problems)
	if problems:
		# Later checks assume the structure is right.
		return None, problems

	_check_ids(ship_id, body, problems)
	_check_rules(body, problems)
	if problems:
		return None, [
			f"{ship_id}: {p}" if not p.startswith(ship_id) else p for p in problems
		]
	return Ship.from_dict(ship_id, body), []


def _shuttle_names(ship: Ship) -> set[str]:
	return {shuttle.name.casefold() for shuttle in ship.shuttles.values()}


def _object_ids(ship: Ship) -> set[str]:
	found = (
		set(ship.stations)
		| set(ship.shuttles)
		| set(ship.units)
		| set(ship.action_cards)
	)
	for shuttle in ship.shuttles.values():
		found.update(shuttle.units)
	return found


def validate_ship_files(
	files: list[ShipFile], galaxy: Galaxy
) -> tuple[list[Ship], list[str]]:
	"""Validates a batch of files against each other and the ships already loaded.

	All or nothing: if any file has a problem, no ships are returned.
	"""
	problems: list[str] = []
	ships: list[Ship] = []
	taken_ids = galaxy.all_ids() | galaxy.used_unit_ids
	taken_names = galaxy.all_ship_names()
	taken_shuttles = {shuttle.name.casefold() for _, shuttle in galaxy.iter_shuttles()}

	for file in files:
		if not file.filename.lower().endswith(".json"):
			problems.append(f"{file.filename}: not a .json file")
			continue
		ship, file_problems = parse_ship(file.content)
		problems.extend(f"{file.filename}: {p}" for p in file_problems)
		if ship is None:
			continue

		clashes = False
		if ship.id in taken_ids:
			problems.append(f"{file.filename}: ship ID {ship.id} is already loaded")
			clashes = True
		if ship.name.casefold() in taken_names:
			problems.append(
				f'{file.filename}: a ship named "{ship.name}" is already loaded'
			)
			clashes = True
		for object_id in sorted(_object_ids(ship) & taken_ids):
			problems.append(
				f"{file.filename}: ID {object_id} clashes with an ID already loaded"
			)
			clashes = True
		for name in sorted(_shuttle_names(ship) & taken_shuttles):
			problems.append(f'{file.filename}: a shuttle named "{name}" already exists')
			clashes = True
		if clashes:
			continue

		taken_ids |= {ship.id} | _object_ids(ship)
		taken_names.add(ship.name.casefold())
		taken_shuttles |= _shuttle_names(ship)
		ships.append(ship)

	if problems:
		return [], problems
	return ships, []

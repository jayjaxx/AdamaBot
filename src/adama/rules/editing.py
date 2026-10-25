"""Control's editing commands: /unit, /station and /ship."""

from adama import ids
from adama.models.galaxy import Galaxy
from adama.models.resources import RESOURCE_NAMES, StationCost
from adama.models.ship import DISTANCES, RATION_LEVELS, Ship
from adama.models.station import Station, StationMode
from adama.models.unit import Unit
from adama.rules import BRIDGE, CONTROL, HANGAR, GameError, Notice
from adama.rules.jumps import check_coordinates
from adama.rules.shuttles import cancel_launches_for_ship
from adama.rules.stations import destroy_or_damage_unit, find_station


def _new_id(galaxy: Galaxy, kind: str, ship: Ship) -> str:
	try:
		return ids.next_object_id(galaxy, kind, ids.conjure_prefix(galaxy, ship.prefix))
	except ids.NoFreeIDError as error:
		raise GameError(str(error)) from error


def _amount(num: int) -> int:
	if num < 0:
		raise GameError("The amount can't be negative.")
	return num


# Units


def find_unit(galaxy: Galaxy, unit_id: str) -> tuple[dict[str, Unit], Unit]:
	"""The unit and the ship's or shuttle's units it is in."""
	unit_id = unit_id.strip()
	for ship in galaxy.ships.values():
		for holder in (ship.units, *(s.units for s in ship.shuttles.values())):
			if unit_id in holder:
				return holder, holder[unit_id]
	raise GameError(f"There is no unit `{unit_id}`.")


def _unit_bridge(unit: Unit, text: str) -> list[Notice]:
	return [Notice(BRIDGE, text, unit.affiliation)]


def conjure_unit(galaxy: Galaxy, name: str, ship: Ship) -> Unit:
	name = name.strip()
	if not name:
		raise GameError("The unit needs a name.")
	unit = Unit(id=_new_id(galaxy, ids.UNIT, ship), name=name, affiliation=ship.id)
	ship.units[unit.id] = unit
	galaxy.used_unit_ids.add(unit.id)
	return unit


def damage_unit(galaxy: Galaxy, unit_id: str) -> tuple[str, list[Notice]]:
	"""Damages the unit, or destroys it if it was already damaged."""
	_, unit = find_unit(galaxy, unit_id)
	what = destroy_or_damage_unit(galaxy, unit.id)
	assert what is not None
	return f"{what}.", _unit_bridge(unit, f"{what}.")


def heal_unit(galaxy: Galaxy, unit_id: str) -> tuple[str, list[Notice]]:
	_, unit = find_unit(galaxy, unit_id)
	if not unit.damaged:
		raise GameError(f"`{unit.id}` {unit.name} isn't damaged.")
	unit.damaged = False
	text = f"`{unit.id}` {unit.name} has been repaired."
	return text, _unit_bridge(unit, text)


def defect_unit(galaxy: Galaxy, unit_id: str, ship: Ship) -> str:
	_, unit = find_unit(galaxy, unit_id)
	unit.affiliation = ship.id
	return f"`{unit.id}` {unit.name} is now affiliated with the {ship.name}."


def rename_unit(galaxy: Galaxy, unit_id: str, name: str) -> str:
	_, unit = find_unit(galaxy, unit_id)
	name = name.strip()
	if not name:
		raise GameError("The unit needs a name.")
	old, unit.name = unit.name, name
	return f"`{unit.id}` {old} is now called {name}."


def set_unit_exhausted(galaxy: Galaxy, unit_id: str, exhausted: bool) -> str:
	_, unit = find_unit(galaxy, unit_id)
	unit.exhausted = exhausted
	state = "exhausted" if exhausted else "ready"
	return f"`{unit.id}` {unit.name} is now {state}."


# Stations


def conjure_station(galaxy: Galaxy, ship: Ship, name: str) -> Station:
	name = name.strip()
	if not name:
		raise GameError("The station needs a name.")
	station = Station(id=_new_id(galaxy, ids.STATION, ship), name=name)
	ship.stations[station.id] = station
	return station


def _resource_or_crew(what: str) -> tuple[str, bool]:
	"""The resource or crew keyword, and whether it is a resource."""
	what = what.strip().casefold()
	if not what:
		raise GameError("Give a resource name or crew keyword.")
	return what, what in RESOURCE_NAMES


def set_io(galaxy: Galaxy, station_id: str, direction: str, num: int, what: str) -> str:
	_, station = find_station(galaxy, station_id)
	num = _amount(num)
	what, is_resource = _resource_or_crew(what)
	if direction == "in":
		if is_resource:
			station.input.set(what, num)
		else:
			station.input.crew_keyword, station.input.crew_amount = what, num
	elif direction == "out":
		if not is_resource:
			raise GameError("Outputs can't be crew.")
		station.output.set(what, num)
	else:
		raise GameError("Direction must be in or out.")
	return f"`{station.id}` {station.name}: {direction}put {what} set to {num}."


def set_description(galaxy: Galaxy, station_id: str, text: str, damaged: bool) -> str:
	_, station = find_station(galaxy, station_id)
	if damaged:
		station.damaged_description = text.strip()
		return f"Set the damaged description of `{station.id}` {station.name}."
	station.description = text.strip()
	return f"Set the description of `{station.id}` {station.name}."


def set_repair_cost(galaxy: Galaxy, station_id: str, num: int, what: str) -> str:
	_, station = find_station(galaxy, station_id)
	num = _amount(num)
	what, is_resource = _resource_or_crew(what)
	cost: StationCost = station.repair_cost
	if is_resource:
		cost.set(what, num)
	else:
		cost.crew_keyword, cost.crew_amount = what, num
	return f"`{station.id}` {station.name}: repair cost {what} set to {num}."


CALL_GM = {
	"damaged": (False, True),
	"used": (True, False),
	"any": (True, True),
	"never": (False, False),
}


def set_call_gm(galaxy: Galaxy, station_id: str, when: str) -> str:
	_, station = find_station(galaxy, station_id)
	if when not in CALL_GM:
		raise GameError("Must be damaged, used, any or never.")
	station.alert_gm_on_use, station.alert_gm_on_damage = CALL_GM[when]
	return f"`{station.id}` {station.name} now calls control: {when}."


STATION_FLAGS = ("usable", "reusable", "startofturn")


def set_flag(galaxy: Galaxy, station_id: str, flag: str, value: bool) -> str:
	_, station = find_station(galaxy, station_id)
	if flag not in STATION_FLAGS:
		raise GameError(f"Unknown flag: use one of {', '.join(STATION_FLAGS)}.")
	setattr(station, flag, value)
	return f"`{station.id}` {station.name}: {flag} set to {str(value).lower()}."


MODE_ACTIONS = ("in", "out", "morale", "remove")


def set_mode(
	galaxy: Galaxy,
	station_id: str,
	mode_name: str,
	action: str,
	num: int | None,
	resource: str | None,
) -> str:
	_, station = find_station(galaxy, station_id)
	mode_name = mode_name.strip()
	folded = mode_name.casefold()
	mode = next((m for m in station.modes if m.name.casefold() == folded), None)
	label = f"`{station.id}` {station.name}"
	if action == "remove":
		if mode is None:
			raise GameError(f'{label} has no mode "{mode_name}".')
		station.modes.remove(mode)
		return f'Removed mode "{mode.name}" from {label}.'
	if action not in MODE_ACTIONS:
		raise GameError("Must be in, out, morale or remove.")
	if num is None:
		raise GameError("Give the amount.")
	if mode is None:
		mode = StationMode(name=mode_name)
		station.modes.append(mode)
	if action == "morale":
		mode.morale_modifier = num
		return f'{label} mode "{mode.name}": morale modifier set to {num:+d}.'
	if resource is None or resource.casefold() not in RESOURCE_NAMES:
		raise GameError(f"Give a resource: one of {', '.join(RESOURCE_NAMES)}.")
	resource = resource.casefold()
	target = mode.input if action == "in" else mode.output
	target.set(resource, _amount(num))
	return f'{label} mode "{mode.name}": {action}put {resource} set to {num}.'


def destroy_station(galaxy: Galaxy, station_id: str) -> tuple[str, list[Notice]]:
	ship, station = find_station(galaxy, station_id)
	del ship.stations[station.id]
	text = f"`{station.id}` {station.name} has been destroyed."
	return text, [Notice(BRIDGE, text, ship.id)]


# Ships


def conjure_ship(galaxy: Galaxy, name: str, coordinates: str) -> Ship:
	"""A new empty ship. The cog creates its channels and crew role."""
	name = name.strip()
	if not name:
		raise GameError("The ship needs a name.")
	if galaxy.ship_by_name(name) is not None:
		raise GameError(f'A ship named "{name}" already exists.')
	if galaxy.find_shuttle(name) is not None:
		raise GameError(f'"{name}" is already the name of a shuttle.')
	coordinates = check_coordinates(coordinates)
	try:
		ship_id = ids.next_ship_id(galaxy)
	except ids.NoFreeIDError as error:
		raise GameError(str(error)) from error
	ship = Ship(id=ship_id, name=name, coordinates=coordinates)
	galaxy.add_ship(ship)
	return ship


def destroy_ship(galaxy: Galaxy, ship: Ship) -> list[Notice]:
	"""Marks the ship destroyed. Nothing is deleted: control moves people as needed."""
	if ship.destroyed:
		raise GameError(f"The {ship.name} is already destroyed.")
	reason = f"the {ship.name} has been destroyed."
	notices = cancel_launches_for_ship(galaxy, ship, reason)
	galaxy.jumps.pop(ship.id, None)
	ship.jumping = False
	for other in galaxy.ships_at(ship.coordinates):
		notices.append(
			Notice(HANGAR, f"**The {ship.name} has been destroyed.**", other.id)
		)
	for coords in galaxy.coordinates.values():
		if coords.locked_down and coords.commanding_ship == ship.id:
			notices.append(
				Notice(
					CONTROL,
					f"The {ship.name} was commanding the lockdown at "
					f"{coords.coordinates}: use /transfercommand to give command to "
					"another ship.",
					claim=True,
				)
			)
	ship.destroyed = True
	return notices


def set_ration_consumption(
	ship: Ship, level: str, num: int | None, resource: str
) -> str:
	"""`num` None makes the level unavailable on this ship."""
	if level not in RATION_LEVELS:
		raise GameError(f"Unknown ration level: use one of {', '.join(RATION_LEVELS)}.")
	if resource not in ("food", "water"):
		raise GameError("The resource must be food or water.")
	if num is not None:
		_amount(num)
	setattr(ship.ration_levels[level], resource, num)
	shown = "unavailable" if num is None else str(num)
	return f"The {ship.name}'s {level} {resource} ration is now {shown}."


def set_fuel_consumption(ship: Ship, distance: str, num: int) -> str:
	if distance not in DISTANCES:
		raise GameError("Distance must be short, medium or long.")
	setattr(ship.fuel_consumption, distance, _amount(num))
	return f"The {ship.name} now uses {num} fuel for a {distance} jump."

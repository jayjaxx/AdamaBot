"""Stations: activating, repairing, damage and riots."""

import random

from adama.models.galaxy import Galaxy
from adama.models.resources import RESOURCE_NAMES, ResourcesContainer
from adama.models.ship import Ship
from adama.models.station import Station, StationMode
from adama.models.unit import Unit
from adama.rules import BRIDGE, CONTROL, GameError, Notice

CREW_KEYWORDS = ("team", "engineer", "medic", "pilot", "scien")
ON_FOOT_WORDS = ("marine", "security")
AEGIS = "Aegis"
CELLS = "Cells"
SICKBAY = "Sickbay"


def matches(unit: Unit, keyword: str) -> bool:
	"""A unit can crew a station if its name contains the crew keyword."""
	return keyword.casefold() in unit.name.casefold()


def training(unit: Unit) -> int:
	"""How many crew keywords a unit matches: a plain Work Team matches only "team"."""
	return sum(1 for keyword in CREW_KEYWORDS if matches(unit, keyword))


def is_on_foot(unit: Unit) -> bool:
	return any(word in unit.name.casefold() for word in ON_FOOT_WORDS)


def available_crew(ship: Ship, keyword: str, *, own_only: bool) -> list[Unit]:
	"""Units aboard the ship that could crew for this keyword right now, least trained
	first so trained crew is saved."""
	units = [
		unit
		for unit in ship.units.values()
		if matches(unit, keyword)
		and not unit.exhausted
		and not unit.damaged
		and (unit.affiliation == ship.id or not own_only)
	]
	return sorted(units, key=lambda u: (training(u), u.id))


def pick_crew(
	ship: Ship, keyword: str, amount: int, unit_ids: list[str] | None
) -> list[Unit]:
	"""The crew for an activation or repair: exactly the designated units (any
	affiliation), or picked automatically from the ship's own units."""
	if amount == 0:
		return []
	if unit_ids is None:
		crew = available_crew(ship, keyword, own_only=True)
		if len(crew) < amount:
			raise GameError(
				f'Not enough available crew matching "{keyword}": need {amount}, have {len(crew)}.'
			)
		return crew[:amount]

	if len(unit_ids) != amount or len(set(unit_ids)) != amount:
		raise GameError(f'This needs exactly {amount} different "{keyword}" crew.')
	crew = []
	for unit_id in unit_ids:
		unit = ship.units.get(unit_id)
		if unit is None:
			raise GameError(f"Unit `{unit_id}` isn't aboard the {ship.name}.")
		if unit.exhausted:
			raise GameError(f"`{unit_id}` {unit.name} is exhausted.")
		if unit.damaged:
			raise GameError(f"`{unit_id}` {unit.name} is damaged.")
		if not matches(unit, keyword):
			raise GameError(
				f'`{unit_id}` {unit.name} can\'t crew this: it needs "{keyword}".'
			)
		crew.append(unit)
	return crew


def find_station(galaxy: Galaxy, station_id: str) -> tuple[Ship, Station]:
	for ship, station in galaxy.iter_stations():
		if station.id == station_id:
			return ship, station
	raise GameError(f"There is no station `{station_id}`.")


def station_on(ship: Ship, station_id: str) -> Station:
	station = ship.stations.get(station_id)
	if station is None:
		raise GameError(f"There is no station `{station_id}` on the {ship.name}.")
	return station


def choose_mode(station: Station, mode: str | None) -> StationMode | None:
	"""The mode to use, by name or number. A single mode is used automatically."""
	if not station.modes:
		return None
	if mode is None:
		if len(station.modes) == 1:
			return station.modes[0]
		listed = ", ".join(
			f"{n}. {m.name}" for n, m in enumerate(station.modes, start=1)
		)
		raise GameError(f"Choose a mode for {station.name}: {listed}.")
	if mode.isdigit() and 1 <= int(mode) <= len(station.modes):
		return station.modes[int(mode) - 1]
	for candidate in station.modes:
		if candidate.name.casefold() == mode.casefold():
			return candidate
	listed = ", ".join(m.name for m in station.modes)
	raise GameError(f'{station.name} has no mode "{mode}". Its modes are: {listed}.')


def _resources_text(resources: ResourcesContainer) -> str:
	nonzero = resources.nonzero()
	return (
		", ".join(f"{amount} {name}" for name, amount in nonzero.items()) or "nothing"
	)


def _check_affordable(ship: Ship, cost: ResourcesContainer) -> None:
	for name in RESOURCE_NAMES:
		if ship.resources.get(name) < cost.get(name):
			raise GameError(
				f"Not enough {name}: need {cost.get(name)}, the {ship.name} has {ship.resources.get(name)}."
			)


def _pay(ship: Ship, cost: ResourcesContainer) -> None:
	for name in RESOURCE_NAMES:
		ship.resources.set(name, ship.resources.get(name) - cost.get(name))


def _gain(ship: Ship, gain: ResourcesContainer) -> None:
	for name in RESOURCE_NAMES:
		ship.resources.set(name, ship.resources.get(name) + gain.get(name))


def _crew_text(crew: list[Unit]) -> str:
	return ", ".join(f"`{u.id}` {u.name}" for u in crew) or "no crew"


def activate(
	ship: Ship,
	station_id: str,
	mode: str | None,
	unit_ids: list[str] | None,
	by: str,
	*,
	late: set[str] | None = None,
) -> list[Notice]:
	"""/activate: spends inputs and crew, gains outputs. While the ship's coordinates
	are in lockdown the cog passes the galaxy's late activations: a start-of-turn
	station activated then is added to them and control is alerted."""
	station = station_on(ship, station_id)
	if not station.usable:
		raise GameError(f"{station.name} can't be activated.")
	if station.damaged:
		raise GameError(
			f"{station.name} is damaged. If its description says it can still be used, ask control."
		)
	if station.riot:
		raise GameError(f"There is a riot in {station.name}.")
	if station.exhausted and not station.reusable:
		raise GameError(f"{station.name} has already been used this turn.")
	chosen = choose_mode(station, mode)
	cost = chosen.input if chosen else station.input
	gain = chosen.output if chosen else station.output
	_check_affordable(ship, cost)
	crew = pick_crew(
		ship, station.input.crew_keyword, station.input.crew_amount, unit_ids
	)

	_pay(ship, cost)
	for unit in crew:
		unit.exhausted = True
		station.units_used_this_turn.append(unit.id)
	_gain(ship, gain)
	if chosen and chosen.morale_modifier:
		ship.morale_roll_modifiers.temp += chosen.morale_modifier
	if not station.reusable:
		station.exhausted = True

	text = f"{by} activated `{station.id}` **{station.name}**" + (
		f" ({chosen.name})" if chosen else ""
	)
	text += f" with {_crew_text(crew)}. Spent {_resources_text(cost)}, gained {_resources_text(gain)}."
	if chosen and chosen.morale_modifier:
		text += f" {chosen.morale_modifier:+d} to this turn's morale roll."
	if station.description:
		text += f"\n> {station.description}"
	notices = [Notice(BRIDGE, text, ship.id)]
	if station.alert_gm_on_use:
		notices.append(
			Notice(
				CONTROL,
				f"{by} used `{station.id}` {station.name} on the {ship.name}: {station.description}",
				claim=True,
			)
		)
	if station.startofturn and late is not None:
		late.add(station.id)
		notices.append(
			Notice(
				CONTROL,
				f"{by} activated the start-of-turn station `{station.id}` "
				f"{station.name} on the {ship.name} during the lockdown at "
				f"{ship.coordinates}.",
				claim=True,
			)
		)
	return notices


def repair(
	ship: Ship, station_id: str, unit_ids: list[str] | None, by: str
) -> list[Notice]:
	"""/repair: pays the repair cost. The repaired station can't be used this turn."""
	station = station_on(ship, station_id)
	if not station.damaged:
		raise GameError(f"{station.name} isn't damaged.")
	cost = station.repair_cost
	_check_affordable(ship, cost)
	crew = pick_crew(ship, cost.crew_keyword, cost.crew_amount, unit_ids)

	_pay(ship, cost)
	for unit in crew:
		unit.exhausted = True
	station.damaged = False
	station.exhausted = True
	notices = [
		Notice(
			BRIDGE,
			f"{by} repaired `{station.id}` **{station.name}** with {_crew_text(crew)}, "
			f"spending {_resources_text(cost)}. It can be used again next turn.",
			ship.id,
		)
	]
	if station.alert_gm_on_damage:
		notices.append(
			Notice(
				CONTROL,
				f"`{station.id}` {station.name} on the {ship.name} has been repaired (end its ongoing damage effects).",
				claim=True,
			)
		)
	return notices


def destroy_or_damage_unit(galaxy: Galaxy, unit_id: str) -> str | None:
	"""Damages a unit, or destroys it if already damaged. Returns what happened."""
	for ship in galaxy.ships.values():
		holders = [ship.units, *(shuttle.units for shuttle in ship.shuttles.values())]
		for holder in holders:
			unit = holder.get(unit_id)
			if unit is None:
				continue
			if unit.damaged:
				del holder[unit_id]
				return f"`{unit.id}` {unit.name} was destroyed"
			unit.damaged = True
			return f"`{unit.id}` {unit.name} was damaged"
	return None


def disperse(
	ship: Ship, station_id: str, unit_id: str, rng: random.Random, by: str
) -> list[Notice]:
	"""/disperse: an on-foot unit tries to end a riot. Win or lose, both are exhausted."""
	station = station_on(ship, station_id)
	if not station.riot:
		raise GameError(f"There is no riot in {station.name}.")
	unit = ship.units.get(unit_id)
	if unit is None:
		raise GameError(f"Unit `{unit_id}` isn't aboard the {ship.name}.")
	if not is_on_foot(unit):
		raise GameError(
			f"{unit.name} isn't an on-foot unit (Marine or Security Forces)."
		)
	if unit.exhausted:
		raise GameError(f"`{unit.id}` {unit.name} is exhausted.")

	unit.exhausted = True
	station.exhausted = True
	roll = rng.randint(1, 6)
	if roll == 1:
		if unit.damaged:
			del ship.units[unit.id]
			result = f"`{unit.id}` {unit.name} was destroyed. The riot remains."
		else:
			unit.damaged = True
			result = f"`{unit.id}` {unit.name} was damaged. The riot remains."
	elif roll <= 3:
		result = "No effect. The riot remains."
	else:
		station.riot = False
		result = "The riot is dispersed."
	text = f"{by} sent `{unit.id}` {unit.name} to disperse the riot in `{station.id}` {station.name}. Rolled **{roll}**: {result}"
	return [Notice(BRIDGE, text, ship.id)]


def _random_station(
	ship: Ship, rng: random.Random, *, without_riot: bool
) -> Station | None:
	candidates = [
		s
		for s in ship.stations.values()
		if not s.damaged and not (without_riot and s.riot)
	]
	return rng.choice(candidates) if candidates else None


def damage(galaxy: Galaxy, target: str, rng: random.Random) -> list[Notice]:
	"""/damage: a station by ID, or a random undamaged station (usable or not) on a ship."""
	if target.isdigit():
		ship, station = find_station(galaxy, target)
		if station.damaged:
			raise GameError(f"`{station.id}` {station.name} is already damaged.")
	else:
		found = galaxy.ship_by_name(target)
		if found is None:
			raise GameError(f'There is no station or ship "{target}".')
		ship = found
		picked = _random_station(ship, rng, without_riot=False)
		if picked is None:
			return [
				Notice(CONTROL, f"Every station on the {ship.name} is already damaged.")
			]
		station = picked

	station.damaged = True
	hurt = [
		r
		for r in (
			destroy_or_damage_unit(galaxy, u) for u in station.units_used_this_turn
		)
		if r
	]
	station.units_used_this_turn.clear()
	if ship.name == AEGIS:
		ship.damage_taken_this_turn += 1
	text = f"`{station.id}` **{station.name}** has been damaged."
	if station.damaged_description:
		text += f"\n> {station.damaged_description}"
	if hurt:
		text += "\nCrew who worked it this turn: " + "; ".join(hurt) + "."
	notices = [Notice(BRIDGE, text, ship.id)]
	if station.alert_gm_on_damage:
		notices.append(
			Notice(
				CONTROL,
				f"`{station.id}` {station.name} on the {ship.name} was damaged: {station.damaged_description}",
				claim=True,
			)
		)
	return notices


def riot_at(ship: Ship, station: Station) -> list[Notice]:
	station.riot = True
	notices = [
		Notice(
			BRIDGE,
			f"A riot has broken out in `{station.id}` **{station.name}**!",
			ship.id,
		)
	]
	if station.name == CELLS:
		notices.append(
			Notice(
				CONTROL,
				f"A riot broke out in the Cells `{station.id}` on the {ship.name} (Vulcan special rule).",
				claim=True,
			)
		)
	return notices


def random_riot(ship: Ship, rng: random.Random) -> list[Notice]:
	"""A riot on a random undamaged station without one (usable or not)."""
	station = _random_station(ship, rng, without_riot=True)
	if station is None:
		return [
			Notice(
				CONTROL,
				f"A riot should break out on the {ship.name}, but it has no undamaged station without a riot.",
			)
		]
	return riot_at(ship, station)


def start_riot(galaxy: Galaxy, target: str, rng: random.Random) -> list[Notice]:
	"""/startriot: a station by ID, or a random one on a ship."""
	if target.isdigit():
		ship, station = find_station(galaxy, target)
		if station.riot:
			raise GameError(
				f"There is already a riot in `{station.id}` {station.name}."
			)
		return riot_at(ship, station)
	named = galaxy.ship_by_name(target)
	if named is None:
		raise GameError(f'There is no station or ship "{target}".')
	if _random_station(named, rng, without_riot=True) is None:
		raise GameError(f"The {named.name} has no undamaged station without a riot.")
	return random_riot(named, rng)


def clear_riot(galaxy: Galaxy, station_id: str) -> list[Notice]:
	ship, station = find_station(galaxy, station_id)
	if not station.riot:
		raise GameError(f"There is no riot in `{station.id}` {station.name}.")
	station.riot = False
	station.exhausted = True
	return [
		Notice(
			BRIDGE,
			f"The riot in `{station.id}` **{station.name}** has been dispersed.",
			ship.id,
		)
	]


def undamage(galaxy: Galaxy, station_id: str) -> list[Notice]:
	ship, station = find_station(galaxy, station_id)
	if not station.damaged:
		raise GameError(f"`{station.id}` {station.name} isn't damaged.")
	station.damaged = False
	return [
		Notice(BRIDGE, f"`{station.id}` **{station.name}** has been repaired.", ship.id)
	]


def set_exhausted(galaxy: Galaxy, station_id: str, exhausted: bool) -> Station:
	_, station = find_station(galaxy, station_id)
	station.exhausted = exhausted
	return station

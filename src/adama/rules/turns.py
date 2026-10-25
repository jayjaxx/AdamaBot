"""The turn loop: rations, the morale roll, and ending the turn."""

import random
from dataclasses import dataclass, field

from adama.models.galaxy import Galaxy
from adama.models.player import Player
from adama.models.ship import RATION_LEVELS, Ship
from adama.rules import BRIDGE, BROADCAST, CONTROL, PDA, GameError, Notice
from adama.rules.players import start_of_turn_reminder
from adama.rules.stations import AEGIS, SICKBAY, random_riot

RATION_MODIFIERS = {
	"luxurious": 4,
	"increased": 2,
	"normal": 0,
	"low": -1,
	"minimum": -2,
	"critical": -4,
	"none": -6,
}
MORALE_MIN, MORALE_MAX = 0, 10


def clamp_morale(level: int) -> int:
	return max(MORALE_MIN, min(MORALE_MAX, level))


def morale_change(total: int) -> int:
	"""13+: +2, 7 to 12: +1, 1 to 6: none, 0 to -3: -1, -4 or less: -2."""
	if total >= 13:
		return 2
	if total >= 7:
		return 1
	if total >= 1:
		return 0
	if total >= -3:
		return -1
	return -2


def riot_threshold(morale: int) -> int:
	"""The highest d6 roll that starts a riot at this morale level (0 = no riot roll)."""
	if morale in (4, 5):
		return 1
	if morale in (2, 3):
		return 2
	if morale == 1:
		return 3
	return 0


def set_ration_level(ship: Ship, resource: str, level: str) -> list[Notice]:
	resource, level = resource.casefold(), level.casefold()
	if resource not in ("food", "water"):
		raise GameError("Rations are set for food or water.")
	if level not in RATION_LEVELS:
		raise GameError(f"The ration level must be one of: {', '.join(RATION_LEVELS)}.")
	amount = getattr(ship.ration_levels[level], resource)
	if amount is None:
		raise GameError(
			f"{level.capitalize()} rations aren't available on the {ship.name}."
		)
	setattr(ship, f"{resource}_ration_level", level)
	return [
		Notice(
			BRIDGE,
			f"{resource.capitalize()} rations set to **{level}**: {amount} {resource} per morale roll.",
			ship.id,
		)
	]


def eat(ship: Ship, resource: str) -> tuple[str, int, int]:
	"""Consumes rations of one resource, falling to the highest level at or below the set
	one that the ship can afford. Returns (level achieved, amount used, modifier)."""
	start = RATION_LEVELS.index(getattr(ship, f"{resource}_ration_level"))
	stock = ship.resources.get(resource)
	for level in RATION_LEVELS[start:]:
		amount = getattr(ship.ration_levels[level], resource)
		if amount is not None and amount <= stock:
			ship.resources.set(resource, stock - amount)
			return level, amount, RATION_MODIFIERS[level]
	raise AssertionError("'none' rations always cost 0")


@dataclass
class MoraleRoll:
	roll: int
	total: int
	change: int
	morale: int
	modifiers: dict[str, int] = field(default_factory=dict)
	rations: dict[str, tuple[str, int]] = field(default_factory=dict)
	riot_roll: int | None = None

	def summary(self) -> str:
		return f"rolled {self.roll}, total {self.total:+d}, morale {self.change:+d} → {self.morale}"


def morale_roll(
	galaxy: Galaxy, ship: Ship, rng: random.Random, force: bool = False
) -> tuple[MoraleRoll, list[Notice]]:
	"""/moraleroll for one ship. `force` (control only) allows another roll this turn."""
	rerolled = ship.morale_rolled_this_turn
	if rerolled and not force:
		raise GameError(f"The {ship.name} has already made its morale roll this turn.")
	notices: list[Notice] = []
	if rerolled:
		notices.append(
			Notice(
				CONTROL,
				f"The {ship.name}'s morale roll was forced again this turn.",
			)
		)

	# Uncontested riots spread: a riot whose station wasn't exhausted (no dispersal tried).
	spread = []
	for station in [s for s in ship.stations.values() if s.riot and not s.exhausted]:
		propagation = rng.randint(1, 6)
		spread.append(f"`{station.id}` {station.name} rolled {propagation}")
		if propagation <= 3:
			notices.extend(random_riot(ship, rng))

	result = MoraleRoll(roll=0, total=0, change=0, morale=ship.morale_level)
	for resource in ("food", "water"):
		level, amount, modifier = eat(ship, resource)
		result.rations[resource] = (level, amount)
		result.modifiers[f"{resource} ({level})"] = modifier
	if ship.name == AEGIS:
		result.modifiers["damage taken"] = -(ship.damage_taken_this_turn // 3)
	result.modifiers["ship (temporary)"] = ship.morale_roll_modifiers.temp
	result.modifiers["ship (permanent)"] = ship.morale_roll_modifiers.perm
	result.modifiers["fleet (temporary)"] = galaxy.fleet_temp_morale_roll_modifier
	result.modifiers["fleet (permanent)"] = galaxy.fleet_perm_morale_roll_modifier

	result.roll = rng.randint(1, 6)
	result.total = result.roll + sum(result.modifiers.values())
	result.change = morale_change(result.total)
	ship.morale_level = clamp_morale(ship.morale_level + result.change)
	result.morale = ship.morale_level

	riot_text = ""
	if ship.morale_level == 0:
		notices.append(
			Notice(CONTROL, f"The {ship.name}'s morale has reached **0**.", claim=True)
		)
		riot_text = "Morale is 0: control has been alerted, and there is no riot roll."
	elif riot_threshold(ship.morale_level):
		result.riot_roll = rng.randint(1, 6)
		if result.riot_roll <= riot_threshold(ship.morale_level):
			riot_text = f"Riot roll: **{result.riot_roll}**, a riot breaks out!"
			notices.extend(random_riot(ship, rng))
		else:
			riot_text = f"Riot roll: **{result.riot_roll}**, no riot."
	ship.morale_rolled_this_turn = True
	galaxy.morale_results[ship.id] = f"turn {galaxy.turn_number}: {result.summary()}"

	lines = [
		f"## {ship.name} morale roll" + (" (forced by control)" if rerolled else "")
	]
	if spread:
		lines.append("Riot spread: " + "; ".join(spread) + " (1-3 spreads).")
	for resource, (level, amount) in result.rations.items():
		lines.append(f"{resource.capitalize()}: {level} rations, {amount} consumed.")
	applied = ", ".join(
		f"{name} {value:+d}" for name, value in result.modifiers.items() if value
	)
	lines.append(f"Modifiers: {applied or 'none'}.")
	lines.append(
		f"Roll **{result.roll}**, total **{result.total:+d}**: morale {result.change:+d}, now **{ship.morale_level}/10**."
	)
	if riot_text:
		lines.append(riot_text)
	notices.insert(0, Notice(BRIDGE, "\n".join(lines), ship.id))
	notices.append(Notice(CONTROL, f"{ship.name} morale roll: {result.summary()}."))
	return result, notices


@dataclass
class EndTurn:
	ended: bool
	notices: list[Notice]
	healed: list[Player] = field(default_factory=list)
	unsealed: bool = False


def end_turn(galaxy: Galaxy) -> EndTurn:
	"""/endturn. With ships yet to roll, the first attempt is refused; a second one forces
	the turn to end. Control is alerted both times."""
	if not galaxy.started:
		raise GameError("No game in progress.")
	unrolled = [
		s.name
		for s in galaxy.ships.values()
		if not s.destroyed and not s.morale_rolled_this_turn
	]
	notices: list[Notice] = []
	if unrolled and not galaxy.end_turn_warned:
		galaxy.end_turn_warned = True
		return EndTurn(
			False,
			[
				Notice(
					CONTROL,
					f"/endturn refused: {', '.join(unrolled)} haven't made their morale roll. Run /endturn again to end the turn anyway.",
				)
			],
		)
	if unrolled:
		notices.append(
			Notice(
				CONTROL,
				f"Turn {galaxy.turn_number} forced to end: {', '.join(unrolled)} never made their morale roll.",
			)
		)

	notices.append(
		Notice(
			BROADCAST,
			f"**Turn {galaxy.turn_number} has ended.** Ships with a passive income or loss should alert control.",
		)
	)
	for ship in galaxy.ships.values():
		for station in ship.stations.values():
			station.exhausted = False
			station.units_used_this_turn.clear()
		ship.morale_roll_modifiers.temp = 0
		ship.damage_taken_this_turn = 0
		ship.morale_rolled_this_turn = False
	for unit in galaxy.iter_units():
		unit.exhausted = False
	for card in galaxy.iter_action_cards():
		if card.once_per_turn:
			card.exhausted = False
	galaxy.fleet_temp_morale_roll_modifier = 0
	unsealed = galaxy.council_sealed
	galaxy.council_sealed = False
	galaxy.end_turn_warned = False

	healed = []
	for player in galaxy.players.values():
		aboard = galaxy.ships.get(player.location)
		if (
			player.wounded
			and aboard is not None
			and any(
				s.name == SICKBAY and not s.damaged for s in aboard.stations.values()
			)
		):
			player.wounded = False
			healed.append(player)
			notices.append(
				Notice(
					PDA,
					f"You have been healed in the {aboard.name}'s sickbay.",
					player.id,
				)
			)

	galaxy.turn_number += 1
	for ship in galaxy.ships.values():
		reminder = start_of_turn_reminder(ship)
		if reminder is not None and not ship.destroyed:
			notices.append(reminder)
	return EndTurn(True, notices, healed, unsealed)


def add_morale(galaxy: Galaxy, target: str, amount: int) -> list[Notice]:
	"""/addmorale for one ship, or every ship with "fleet"."""
	if target.casefold() == "fleet":
		ships = [s for s in galaxy.ships.values() if not s.destroyed]
	else:
		ship = galaxy.ship_by_name(target)
		if ship is None:
			raise GameError(f'There is no ship named "{target}".')
		ships = [ship]
	notices = []
	for ship in ships:
		ship.morale_level = clamp_morale(ship.morale_level + amount)
		notices.append(
			Notice(
				BRIDGE,
				f"Morale changed by {amount:+d}: now **{ship.morale_level}/10**.",
				ship.id,
			)
		)
		if ship.morale_level == 0:
			notices.append(
				Notice(
					CONTROL, f"The {ship.name}'s morale has reached **0**.", claim=True
				)
			)
	return notices


def add_modifier(galaxy: Galaxy, target: str | None, amount: int, duration: str) -> str:
	"""/shipmoralemodifier (target = ship name) or /fleetmoralemodifier (target None)."""
	if duration not in ("temp", "perm"):
		raise GameError("Choose temp or perm.")
	if target is None:
		if duration == "temp":
			galaxy.fleet_temp_morale_roll_modifier += amount
			value = galaxy.fleet_temp_morale_roll_modifier
		else:
			galaxy.fleet_perm_morale_roll_modifier += amount
			value = galaxy.fleet_perm_morale_roll_modifier
		return f"Fleet {duration} morale roll modifier is now {value:+d}."
	ship = galaxy.ship_by_name(target)
	if ship is None:
		raise GameError(f'There is no ship named "{target}".')
	mods = ship.morale_roll_modifiers
	if duration == "temp":
		mods.temp += amount
		value = mods.temp
	else:
		mods.perm += amount
		value = mods.perm
	return f"The {ship.name}'s {duration} morale roll modifier is now {value:+d}."


def team_phase() -> list[Notice]:
	return [
		Notice(
			BROADCAST,
			"**The team phase has started.** Everyone should return to their ships.",
		)
	]

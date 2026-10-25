"""Jumping between coordinates, and isolating ships."""

from dataclasses import dataclass, field

from adama.models.galaxy import Galaxy, Jump
from adama.models.player import LOCATION_COMBAT, Player
from adama.models.ship import DISTANCES, Ship
from adama.rules import (
	BRIDGE,
	CONTROL,
	HANGAR,
	PDA,
	GameError,
	Notice,
	isolation,
	sentence,
)
from adama.rules.shuttles import cancel_launches_for_ship
from adama.shipfiles import is_coordinates

JUMP_SECONDS = 15
JUMP_DRIVE = "Jump Drive"
FUEL_STORAGE = "Fuel Storage"
EMERGENCY_JUMP = "To perform an emergency jump, contact control with /control."


class JumpRefused(GameError):
	"""A refused jump that control must still hear about."""

	def __init__(self, message: str, notices: list[Notice]):
		super().__init__(message)
		self.notices = notices


@dataclass
class ShipMoved:
	notices: list[Notice] = field(default_factory=list)
	# Players whose Discord access must be updated (first responders left in combat).
	resync: list[Player] = field(default_factory=list)


def check_coordinates(coordinates: str) -> str:
	coordinates = coordinates.strip()
	if not is_coordinates(coordinates):
		raise GameError("Coordinates must be exactly 5 digits.")
	return coordinates


def _damaged(ship: Ship, name: str) -> bool:
	return any(s.name == name and s.damaged for s in ship.stations.values())


def set_coords(ship: Ship, coordinates: str) -> list[Notice]:
	coordinates = check_coordinates(coordinates)
	if ship.jumping:
		raise GameError("The ship is jumping. /abort the jump first.")
	ship.next_coordinates = coordinates
	return [Notice(BRIDGE, f"Jump coordinates set to **{coordinates}**.", ship.id)]


def check_coords(ship: Ship) -> str:
	"""Never reveals subbed coordinates."""
	if not ship.next_coordinates:
		return "No jump coordinates are set."
	return f"Jump coordinates: **{ship.next_coordinates}**."


def _destination(ship: Ship) -> str:
	return ship.sabotage_coordinates or ship.next_coordinates


def _fuel_needed(galaxy: Galaxy, ship: Ship, destination: str) -> tuple[str, int]:
	distance = galaxy.get_or_create_coordinates(destination).distance
	return distance, ship.fuel_consumption.get(distance)


def start_jump(galaxy: Galaxy, ship: Ship) -> tuple[Jump, list[Notice]]:
	"""/jump: starts the countdown. The cog finishes it with complete_jump."""
	if not ship.next_coordinates:
		raise GameError("No jump coordinates are set. Use /setcoords.")
	drives = [s for s in ship.stations.values() if s.name == JUMP_DRIVE]
	if not drives:
		raise GameError(f"The {ship.name} has no Jump Drive.")
	if not any(s.exhausted for s in drives):
		raise GameError("The Jump Drive hasn't been activated this turn.")
	if ship.isolated:
		raise GameError(f"{sentence(isolation(ship.name))} Until then it can't jump.")
	if ship.jumping:
		raise GameError(f"The {ship.name} is already jumping.")
	destination = _destination(ship)
	distance, fuel = _fuel_needed(galaxy, ship, destination)
	if ship.resources.fuel < fuel:
		raise JumpRefused(
			f"Not enough fuel: the jump needs {fuel}, the {ship.name} has {ship.resources.fuel}. "
			+ EMERGENCY_JUMP,
			[
				Notice(
					CONTROL,
					f"The {ship.name} tried to jump without enough fuel ({ship.resources.fuel} of {fuel}).",
					claim=True,
				)
			],
		)

	ship.jumping = True
	jump = Jump(ship.id)
	galaxy.jumps[ship.id] = jump
	alert = f"The {ship.name} is preparing to jump to {ship.next_coordinates} ({distance}, {fuel} fuel)"
	if ship.sabotage_coordinates:
		alert += f", subbed to **{ship.sabotage_coordinates}**"
	damaged = [n for n in (JUMP_DRIVE, FUEL_STORAGE) if _damaged(ship, n)]
	if damaged:
		alert += f". Damaged: {', '.join(damaged)}"
	return jump, [
		Notice(
			BRIDGE,
			f"Jump countdown started: jumping in {JUMP_SECONDS} seconds. Use /abort to stop it.",
			ship.id,
		),
		Notice(CONTROL, alert + "."),
	]


def _stop_jump(galaxy: Galaxy, ship: Ship) -> bool:
	running = galaxy.jumps.pop(ship.id, None) is not None or ship.jumping
	ship.jumping = False
	return running


def abort_jump(galaxy: Galaxy, ship: Ship) -> list[Notice]:
	if not ship.jumping:
		raise GameError(f"The {ship.name} isn't jumping.")
	_stop_jump(galaxy, ship)
	return [
		Notice(BRIDGE, "The jump has been aborted.", ship.id),
		Notice(CONTROL, f"The {ship.name}'s jump was aborted."),
	]


def _left_lockdown_alert(
	galaxy: Galaxy, ship: Ship, origin: str, destination: str, left: list[Player]
) -> Notice:
	"""Nobody is pulled out of the combat: control decides what happens to the first
	responders who came from the ship or are its crew. `left`: the commanding ship's
	first responders, left behind in combat."""
	in_combat = [
		p
		for p in galaxy.players.values()
		if p.location == LOCATION_COMBAT and p.combat_origin_ship == ship.id
	]
	crew = [
		p
		for p in galaxy.players.values()
		if p.first_responder and not p.dead and ship.id in p.crew_affiliations
	]

	def names(players: list[Player]) -> str:
		return ", ".join(f"<@{p.discord_user_id}>" for p in players) or "none"

	text = (
		f"The {ship.name} jumped from {origin} to {destination} during the lockdown "
		f"at {origin}. First responders in combat from the {ship.name}: "
		f"{names(in_combat)}. First responders who are its crew: {names(crew)}."
	)
	coords = galaxy.coordinates[origin]
	if coords.commanding_ship == ship.id:
		text += (
			f" **The {ship.name} was commanding the lockdown:** its first responders "
			f"aboard were left behind in combat ({names(left)}). Use /transfercommand "
			"to give command to another ship."
		)
	return Notice(CONTROL, text, claim=True)


def _leave_commanders_behind(galaxy: Galaxy, ship: Ship, origin: str) -> list[Player]:
	"""The commanding ship's first-responder crew aboard stay in the combat it left."""
	left = [
		p
		for p in galaxy.players.values()
		if p.first_responder
		and not p.dead
		and p.location == ship.id
		and ship.id in p.crew_affiliations
	]
	for player in left:
		player.location = LOCATION_COMBAT
		player.combat_origin_ship = ship.id
		player.combat_coordinates = origin
		player.clearances.clear()
	return left


def move_ship(galaxy: Galaxy, ship: Ship, destination: str, reason: str) -> ShipMoved:
	"""Moves a ship with everything aboard it. Shared by jumps and /teleportship."""
	moved = ShipMoved(cancel_launches_for_ship(galaxy, ship, reason))
	origin = ship.coordinates
	leaving = galaxy.coordinates.get(origin)
	if leaving is not None and leaving.locked_down:
		if leaving.commanding_ship == ship.id:
			moved.resync = _leave_commanders_behind(galaxy, ship, origin)
			moved.notices += [
				Notice(
					PDA,
					f"The {ship.name} jumped away from the lockdown at {origin}: you "
					"stayed behind in the combat.",
					p.id,
				)
				for p in moved.resync
			]
		moved.notices.append(
			_left_lockdown_alert(galaxy, ship, origin, destination, moved.resync)
		)
	galaxy.get_or_create_coordinates(destination)
	ship.coordinates = destination
	for other in galaxy.ships_at(origin):
		moved.notices.append(
			Notice(HANGAR, f"The {ship.name} has jumped away.", other.id)
		)
	for other in galaxy.ships_at(destination):
		if other.id != ship.id:
			moved.notices.append(
				Notice(HANGAR, f"The {ship.name} has arrived.", other.id)
			)
	return moved


def complete_jump(galaxy: Galaxy, jump: Jump) -> ShipMoved | None:
	"""Ends the countdown. None if the jump was aborted or replaced in the meantime."""
	if galaxy.jumps.get(jump.ship_id) is not jump:
		return None
	del galaxy.jumps[jump.ship_id]
	ship = galaxy.ships.get(jump.ship_id)
	if ship is None or not ship.jumping:
		return None
	ship.jumping = False
	destination = _destination(ship)
	_, fuel = _fuel_needed(galaxy, ship, destination)
	if ship.resources.fuel < fuel:
		return ShipMoved(
			[
				Notice(
					BRIDGE,
					f"The jump failed: not enough fuel. {EMERGENCY_JUMP}",
					ship.id,
				),
				Notice(
					CONTROL,
					f"The {ship.name}'s jump failed: not enough fuel ({ship.resources.fuel} of {fuel}).",
					claim=True,
				),
			]
		)
	ship.resources.fuel -= fuel
	moved = move_ship(galaxy, ship, destination, f"the {ship.name} jumped.")
	ship.next_coordinates = ""
	ship.sabotage_coordinates = ""
	done = f"Jump complete: the {ship.name} is now at **{destination}**."
	moved.notices.append(Notice(HANGAR, done, ship.id))
	moved.notices.append(
		Notice(BRIDGE, f"{done} {fuel} fuel used, {ship.resources.fuel} left.", ship.id)
	)
	moved.notices.append(Notice(CONTROL, f"The {ship.name} jumped to {destination}."))
	return moved


def teleport_ship(galaxy: Galaxy, ship: Ship, coordinates: str) -> ShipMoved:
	"""/teleportship: no fuel and no Jump Drive needed."""
	coordinates = check_coordinates(coordinates)
	if ship.coordinates == coordinates:
		raise GameError(f"The {ship.name} is already at {coordinates}.")
	stopped = _stop_jump(galaxy, ship)
	moved = move_ship(galaxy, ship, coordinates, f"control moved the {ship.name}.")
	if stopped:
		moved.notices.append(
			Notice(BRIDGE, "The jump countdown was cancelled by control.", ship.id)
		)
	moved.notices.append(
		Notice(HANGAR, f"The {ship.name} is now at **{coordinates}**.", ship.id)
	)
	return moved


def sub_coords(ship: Ship, coordinates: str) -> str:
	ship.sabotage_coordinates = check_coordinates(coordinates)
	return f"The {ship.name}'s next jump will go to {ship.sabotage_coordinates}. The crew isn't told."


def clear_subbed_coords(ship: Ship) -> str:
	if not ship.sabotage_coordinates:
		raise GameError(f"The {ship.name} has no subbed coordinates.")
	ship.sabotage_coordinates = ""
	return f"Cleared the {ship.name}'s subbed coordinates."


def set_jump_distance(galaxy: Galaxy, coordinates: str, distance: str) -> str:
	coordinates = check_coordinates(coordinates)
	distance = distance.casefold()
	if distance not in DISTANCES:
		raise GameError(f"Distance must be one of: {', '.join(DISTANCES)}.")
	galaxy.get_or_create_coordinates(coordinates).distance = distance
	return f"{coordinates} is now a {distance} jump."


def isolate(galaxy: Galaxy, ship: Ship) -> list[Notice]:
	if ship.isolated:
		raise GameError(f"The {ship.name} is already isolated.")
	ship.isolated = True
	notices = cancel_launches_for_ship(galaxy, ship, isolation(ship.name))
	if _stop_jump(galaxy, ship):
		notices.append(
			Notice(BRIDGE, f"The jump was cancelled: {isolation(ship.name)}", ship.id)
		)
	return notices


def unisolate(ship: Ship) -> None:
	if not ship.isolated:
		raise GameError(f"The {ship.name} isn't isolated.")
	ship.isolated = False

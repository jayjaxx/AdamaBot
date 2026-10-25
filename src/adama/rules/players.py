"""Starting the game, adding players and crews, and moving players between ships."""

from dataclasses import dataclass, field

from adama import ids
from adama.channels import crew_role_name
from adama.config import RolesConfig
from adama.models.galaxy import STATE_IN_PROGRESS, STATE_INITIALIZING, Galaxy
from adama.models.player import LOCATION_BRIG, LOCATION_COMBAT, Player
from adama.models.ship import Ship
from adama.rules import (
	BRIDGE,
	BROADCAST,
	CONTROL,
	HANGAR,
	PDA,
	GameError,
	Notice,
	isolation,
	sentence,
)


def mention(player: Player) -> str:
	return f"<@{player.discord_user_id}>"


@dataclass(frozen=True)
class LobbyMember:
	user_id: int
	role_names: frozenset[str] = field(default_factory=frozenset)


def current_ship(galaxy: Galaxy, player: Player) -> Ship | None:
	"""The ship the player is aboard; in combat, the ship they entered it from; in the
	brig, the brig ship."""
	if player.location == LOCATION_COMBAT:
		ship_id = player.combat_origin_ship
	elif player.location == LOCATION_BRIG:
		ship_id = galaxy.brig_ship
	else:
		ship_id = player.location
	return galaxy.ships.get(ship_id) if ship_id else None


def start_of_turn_reminder(ship: Ship) -> Notice | None:
	stations = [s for s in ship.stations.values() if s.startofturn]
	if not stations:
		return None
	listed = ", ".join(f"`{s.id}` {s.name}" for s in stations)
	return Notice(
		BRIDGE,
		f"New turn. Activate your start-of-turn stations promptly: {listed}.",
		ship.id,
	)


def start_game(
	galaxy: Galaxy, members: list[LobbyMember], roles: RolesConfig
) -> tuple[list[Player], list[int], list[Notice]]:
	"""Creates a player for every lobby member with at least one game role.

	Returns the new players, the user IDs left out (no game role), and the notices.
	"""
	if galaxy.state == STATE_IN_PROGRESS:
		raise GameError("The game has already started.")
	if galaxy.state != STATE_INITIALIZING:
		raise GameError("No game is being initialized. Run /startinitialization first.")
	if not galaxy.ships:
		raise GameError("No ships are loaded. Add them with /loadship.")

	crew_roles = {crew_role_name(ship.name): ship.id for ship in galaxy.ships.values()}
	offices = {roles.PRESIDENT, roles.COUNCILLOR, roles.FIRST_RESPONDER, roles.PRESS}
	players: list[Player] = []
	left_out: list[int] = []
	notices: list[Notice] = []

	for member in members:
		crews = sorted(
			crew_roles[name] for name in member.role_names if name in crew_roles
		)
		if not crews and not member.role_names & offices:
			left_out.append(member.user_id)
			continue
		player = Player(
			id=ids.next_player_id(galaxy),
			discord_user_id=member.user_id,
			crew_affiliations=crews,
			location=crews[0] if crews else "",
			president=roles.PRESIDENT in member.role_names,
			councillor=roles.COUNCILLOR in member.role_names,
			first_responder=roles.FIRST_RESPONDER in member.role_names,
		)
		galaxy.players[player.id] = player
		players.append(player)
		if not crews:
			notices.append(
				Notice(
					CONTROL,
					f"{mention(player)} has no crew role, so they have no location. "
					"Place them with /addteam or /teleportplayer.",
				)
			)

	galaxy.state = STATE_IN_PROGRESS
	galaxy.turn_number = 1
	notices.append(Notice(BROADCAST, "**The game has started!** Turn 1 begins now."))
	for ship in galaxy.ships.values():
		reminder = start_of_turn_reminder(ship)
		if reminder is not None:
			notices.append(reminder)
	if galaxy.council_chambers_ship is None:
		notices.append(
			Notice(
				CONTROL,
				"Warning: no loaded ship has a Council Chamber, so there are no council chambers.",
			)
		)
	if galaxy.brig_ship is None:
		notices.append(
			Notice(CONTROL, "Warning: no loaded ship has a Brig, so there is no brig.")
		)
	return players, left_out, notices


def add_player(galaxy: Galaxy, user_id: int) -> Player:
	if not galaxy.started:
		raise GameError("No game in progress.")
	if galaxy.player_by_user(user_id) is not None:
		raise GameError(f"<@{user_id}> is already a player.")
	player = Player(id=ids.next_player_id(galaxy), discord_user_id=user_id)
	galaxy.players[player.id] = player
	return player


def add_team(galaxy: Galaxy, player: Player, ship: Ship) -> list[Notice]:
	if ship.id in player.crew_affiliations:
		raise GameError(f"{mention(player)} is already crew of the {ship.name}.")
	player.crew_affiliations.append(ship.id)
	notices = [Notice(PDA, f"You are now crew of the {ship.name}.", player.id)]
	if not player.location:
		player.location = ship.id
		notices.append(Notice(HANGAR, f"{mention(player)} has arrived.", ship.id))
	return notices


def remove_team(galaxy: Galaxy, player: Player, ship: Ship) -> list[Notice]:
	if ship.id not in player.crew_affiliations:
		raise GameError(f"{mention(player)} isn't crew of the {ship.name}.")
	player.crew_affiliations.remove(ship.id)
	return [Notice(PDA, f"You are no longer crew of the {ship.name}.", player.id)]


def _arrive(player: Player, ship: Ship, notices: list[Notice]) -> None:
	player.location = ship.id
	player.combat_origin_ship = None
	player.clearances.clear()
	notices.append(Notice(HANGAR, f"{mention(player)} has arrived.", ship.id))


def _enter_combat(player: Player, origin: Ship, notices: list[Notice]) -> None:
	player.location = LOCATION_COMBAT
	player.combat_origin_ship = origin.id
	player.clearances.clear()
	notices.append(
		Notice(
			PDA,
			f"You are in combat. You can /travel back to any ship at {origin.coordinates}.",
			player.id,
		)
	)


def travel(galaxy: Galaxy, player: Player, destination: str) -> list[Notice]:
	"""/travel: moves the player to a ship at the same coordinates, or into combat."""
	if player.wounded:
		raise GameError("You're wounded and can't travel.")
	if any(launch.player_id == player.id for launch in galaxy.launches.values()):
		raise GameError("You're launching a shuttle.")
	in_combat = player.location == LOCATION_COMBAT
	origin = current_ship(galaxy, player)
	if origin is None or player.location == LOCATION_BRIG:
		raise GameError("You aren't aboard a ship.")
	coords = galaxy.get_or_create_coordinates(origin.coordinates)
	notices: list[Notice] = []

	if destination.casefold() == LOCATION_COMBAT:
		if in_combat:
			raise GameError("You're already in combat.")
		if not (coords.locked_down and player.first_responder):
			raise GameError(
				"Combat can only be entered by first responders during a lockdown."
			)
		if origin.isolated:
			raise GameError(sentence(isolation(origin.name)))
		notices.append(
			Notice(HANGAR, f"{mention(player)} is leaving for combat.", origin.id)
		)
		_enter_combat(player, origin, notices)
		return notices

	dest = galaxy.ship_by_name(destination)
	if dest is None:
		raise GameError(f'There is no ship named "{destination}".')
	if dest.id == player.location:
		raise GameError(f"You're already aboard the {dest.name}.")
	if dest.destroyed:
		raise GameError(f"The {dest.name} has been destroyed.")
	if dest.coordinates != origin.coordinates:
		raise GameError(f"The {dest.name} isn't at your coordinates.")
	if dest.isolated:
		raise GameError(sentence(isolation(dest.name)))
	# A player in combat may travel to any ship at those coordinates.
	if not in_combat:
		if origin.isolated:
			raise GameError(sentence(isolation(origin.name)))
		if coords.locked_down:
			raise GameError(
				f"{origin.coordinates} is in lockdown: nobody can travel between ships."
			)
		notices.append(
			Notice(
				HANGAR, f"{mention(player)} is leaving for the {dest.name}.", origin.id
			)
		)
	_arrive(player, dest, notices)
	return notices


def _drop_launch(galaxy: Galaxy, player: Player, notices: list[Notice]) -> None:
	"""Cancels any shuttle launch the player is flying."""
	for shuttle_id, launch in list(galaxy.launches.items()):
		if launch.player_id != player.id:
			continue
		del galaxy.launches[shuttle_id]
		origin = galaxy.ships.get(launch.origin_id)
		shuttle = origin.shuttles.get(shuttle_id) if origin else None
		if shuttle is not None:
			shuttle.launching = False
			notices.append(
				Notice(
					HANGAR,
					f"The launch of {shuttle.name} was aborted: its pilot was moved by control.",
					launch.origin_id,
				)
			)


def teleport(galaxy: Galaxy, player: Player, destination: str) -> list[Notice]:
	"""/teleportplayer: moves a player anywhere, ignoring every travel restriction."""
	if player.dead:
		raise GameError(f"{mention(player)} is dead.")
	if player.brigged:
		raise GameError(f"{mention(player)} is in the brig. Use /unbrig.")
	origin = current_ship(galaxy, player)
	notices: list[Notice] = []

	if destination.casefold() == LOCATION_COMBAT:
		if origin is None:
			raise GameError(
				f"{mention(player)} isn't anywhere, so has no coordinates to fight at."
			)
		coords = galaxy.get_or_create_coordinates(origin.coordinates)
		if not coords.locked_down:
			raise GameError(f"There is no lockdown at {origin.coordinates}.")
		if player.location == LOCATION_COMBAT:
			raise GameError(f"{mention(player)} is already in combat.")
		_drop_launch(galaxy, player, notices)
		notices.append(Notice(HANGAR, f"{mention(player)} is leaving.", origin.id))
		_enter_combat(player, origin, notices)
		return notices

	dest = galaxy.ship_by_name(destination)
	if dest is None:
		raise GameError(f'There is no ship named "{destination}".')
	if dest.id == player.location:
		raise GameError(f"{mention(player)} is already aboard the {dest.name}.")
	_drop_launch(galaxy, player, notices)
	if origin is not None and player.location != LOCATION_COMBAT:
		notices.append(Notice(HANGAR, f"{mention(player)} is leaving.", origin.id))
	_arrive(player, dest, notices)
	return notices


def locate(galaxy: Galaxy, player: Player, target: Player) -> str:
	"""/locate: which ship a player at the same coordinates is on."""
	here = current_ship(galaxy, player)
	there = current_ship(galaxy, target)
	if (
		target.dead
		or here is None
		or there is None
		or here.coordinates != there.coordinates
	):
		raise GameError(f"Unable to locate {mention(target)}.")
	if target.location == LOCATION_COMBAT:
		return f"{mention(target)} is in combat."
	if target.location == LOCATION_BRIG:
		return f"{mention(target)} is in the brig aboard the {there.name}."
	return f"{mention(target)} is aboard the {there.name}."

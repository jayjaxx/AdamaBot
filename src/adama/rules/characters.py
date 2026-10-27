"""Wounds, death and the brig."""

from typing import Any

from adama.models.galaxy import Galaxy
from adama.models.player import LOCATION_BRIG, LOCATION_NONE, Player
from adama.models.ship import Ship
from adama.rules import BRIG, CONTROL, HANGAR, PDA, GameError, Notice
from adama.rules.players import current_ship, healing_ship, mention
from adama.rules.shuttles import cancel_player_launch
from adama.rules.stations import SICKBAY
from adama.status import location_name

# What /kill stores so /resurrect can put it back. Discord roles are added by the cog.
STORED_FIELDS = (
	"crew_affiliations",
	"president",
	"councillor",
	"first_responder",
	"battlemap_observer",
	"permitted",
	"location",
	"combat_origin_ship",
	"combat_coordinates",
)
OFFICE_FLAGS = (
	"president",
	"councillor",
	"first_responder",
	"battlemap_observer",
	"permitted",
)


def _alive(player: Player) -> None:
	if player.dead:
		raise GameError(f"{mention(player)} is dead.")


def sickbays_near(galaxy: Galaxy, player: Player) -> list[Ship]:
	"""Ships at the player's coordinates with an undamaged Sickbay."""
	here = current_ship(galaxy, player)
	if here is None:
		return []
	return [
		ship
		for ship in galaxy.ships_at(here.coordinates)
		if any(s.name == SICKBAY and not s.damaged for s in ship.stations.values())
	]


def wound(galaxy: Galaxy, player: Player) -> list[Notice]:
	_alive(player)
	if player.wounded:
		raise GameError(f"{mention(player)} is already wounded.")
	player.wounded = True
	ships = ", ".join(s.name for s in sickbays_near(galaxy, player)) or "none"
	wounded = (
		"**You are wounded.** You can't travel, launch or intercept until you are "
		"healed. "
	)
	if healing_ship(galaxy, player) is not None:
		return [
			Notice(
				PDA,
				wounded + "There is a working Sickbay where you are: you'll be healed "
				"at the end of the turn if you stay.",
				player.id,
			)
		]
	return [
		Notice(
			PDA,
			wounded + "There is no working Sickbay where you are. Control has been "
			"alerted and should soon come to move you to a ship with one. If nobody "
			"arrives within a couple of minutes, use /control.",
			player.id,
		),
		Notice(
			CONTROL,
			f"{mention(player)} was wounded at {location_name(galaxy, player)}, which "
			f"has no working Sickbay. Move them to one with /teleportplayer. Sickbays "
			f"at their coordinates: {ships}.",
			claim=True,
			links=((PDA, player.id),),
		),
	]


def heal(player: Player) -> list[Notice]:
	if not player.wounded:
		raise GameError(f"{mention(player)} isn't wounded.")
	player.wounded = False
	return [Notice(PDA, "You have been healed.", player.id)]


def kill(galaxy: Galaxy, player: Player) -> list[Notice]:
	"""Stores everything /resurrect restores, then strips the player of it. Their action
	cards stay with them (their 'ghost') until control moves them."""
	_alive(player)
	notices = cancel_player_launch(galaxy, player, "its pilot has died.")
	player.clearances.clear()
	stored: dict[str, Any] = {
		name: list(value) if isinstance(value, list) else value
		for name in STORED_FIELDS
		for value in [getattr(player, name)]
	}
	stored["brigged"] = player.brigged
	stored["roles"] = []
	player.stored_state = stored
	player.dead = True
	player.wounded = False
	player.brigged = False
	player.crew_affiliations = []
	for flag in OFFICE_FLAGS:
		setattr(player, flag, False)
	player.location = LOCATION_NONE
	player.combat_origin_ship = None
	player.combat_coordinates = None
	notices.append(
		Notice(
			PDA,
			"**You have died.** You can only use /control and /help.",
			player.id,
		)
	)
	return notices


def resurrect(galaxy: Galaxy, player: Player, ship: Ship | None) -> list[Notice]:
	"""Restores what /kill stored and places the player in a hangar: the given ship, or
	their first crew ship."""
	if not player.dead:
		raise GameError(f"{mention(player)} isn't dead.")
	stored = player.stored_state or {}
	crews = [c for c in stored.get("crew_affiliations", []) if c in galaxy.ships]
	if ship is None:
		ship = galaxy.ships.get(crews[0]) if crews else None
		if ship is None:
			raise GameError(
				f"{mention(player)} isn't crew of any ship: choose where to resurrect them."
			)
	player.dead = False
	player.wounded = False
	player.crew_affiliations = crews
	for flag in OFFICE_FLAGS:
		setattr(player, flag, bool(stored.get(flag, False)))
	player.location = ship.id
	player.combat_origin_ship = None
	player.combat_coordinates = None
	return [
		Notice(
			PDA, f"**You have been resurrected** aboard the {ship.name}.", player.id
		),
		Notice(HANGAR, f"{mention(player)} has arrived.", ship.id),
	]


def stored_roles(player: Player) -> list[str]:
	return list((player.stored_state or {}).get("roles", []))


def clear_stored_state(player: Player) -> None:
	player.stored_state = None


def _brig_ship(galaxy: Galaxy) -> Ship:
	ship = galaxy.ships.get(galaxy.brig_ship or "")
	if ship is None:
		raise GameError("There is no brig. Use /movebrig to put it on a ship.")
	return ship


def brig(galaxy: Galaxy, player: Player) -> list[Notice]:
	_alive(player)
	if player.brigged:
		raise GameError(f"{mention(player)} is already in the brig.")
	ship = _brig_ship(galaxy)
	notices = cancel_player_launch(galaxy, player, "its pilot was arrested.")
	origin = current_ship(galaxy, player)
	if origin is not None and player.location != LOCATION_BRIG:
		notices.append(Notice(HANGAR, f"{mention(player)} is leaving.", origin.id))
	player.clearances.clear()
	player.brigged = True
	player.location = LOCATION_BRIG
	player.combat_origin_ship = None
	player.combat_coordinates = None
	notices.append(
		Notice(
			PDA,
			f"**You have been put in the brig** aboard the {ship.name}. You can only use "
			"/status, /roll, /control and /help, and talk in the brig channels.",
			player.id,
		)
	)
	notices.append(Notice(BRIG, f"{mention(player)} has been brought to the brig."))
	return notices


def unbrig(galaxy: Galaxy, player: Player) -> list[Notice]:
	if not player.brigged:
		raise GameError(f"{mention(player)} isn't in the brig.")
	ship = _brig_ship(galaxy)
	player.brigged = False
	player.location = ship.id
	return [
		Notice(BRIG, f"{mention(player)} has been released."),
		Notice(HANGAR, f"{mention(player)} has arrived.", ship.id),
		Notice(
			PDA,
			f"You have been released from the brig aboard the {ship.name}.",
			player.id,
		),
	]


def move_brig(galaxy: Galaxy, ship: Ship) -> list[Notice]:
	"""Brigged players' location is "brig", so they move with it."""
	if galaxy.brig_ship == ship.id:
		raise GameError(f"The brig is already on the {ship.name}.")
	galaxy.brig_ship = ship.id
	return [Notice(BRIG, f"The brig has been moved to the {ship.name}.")]


# Wolves: GM-only, with no effect on the rules. Nobody is told, not even the player.


def set_wolf(player: Player, wolf: bool) -> str:
	if wolf and player.wolf:
		raise GameError(f"{mention(player)} is already a wolf.")
	if not wolf and not player.wolf:
		raise GameError(f"{mention(player)} isn't a wolf.")
	player.wolf = wolf
	return f"{mention(player)} is {'now' if wolf else 'no longer'} a wolf."


def set_lobby_wolf(galaxy: Galaxy, user_id: int, wolf: bool) -> str:
	"""Before /startgame: remembered and applied when the player is created."""
	if user_id not in galaxy.discord.admitted:
		raise GameError(f"<@{user_id}> isn't in the pregame lobby.")
	if wolf and user_id in galaxy.lobby_wolves:
		raise GameError(f"<@{user_id}> is already a wolf.")
	if not wolf and user_id not in galaxy.lobby_wolves:
		raise GameError(f"<@{user_id}> isn't a wolf.")
	if wolf:
		galaxy.lobby_wolves.add(user_id)
	else:
		galaxy.lobby_wolves.discard(user_id)
	return (
		f"<@{user_id}> will {'be' if wolf else 'not be'} a wolf when the game starts."
	)


def wolves_list(galaxy: Galaxy) -> str:
	lines = [
		f"- {mention(p)}: {location_name(galaxy, p)}" + (" (dead)" if p.dead else "")
		for p in galaxy.players.values()
		if p.wolf
	]
	lines += [f"- <@{user}> (pregame lobby)" for user in sorted(galaxy.lobby_wolves)]
	return "**Wolves**\n" + ("\n".join(lines) if lines else "none")

"""The council: the president's chambers commands, and elections."""

from dataclasses import dataclass, field

from adama.models.galaxy import Galaxy
from adama.models.player import Player
from adama.models.ship import Ship
from adama.rules import CONTROL, COUNCIL, PDA, GameError, Notice
from adama.rules.players import mention

PRESIDENT = "president"
COUNCILLOR = "councillor"
OFFICES = (PRESIDENT, COUNCILLOR)


@dataclass
class CouncilChange:
	notices: list[Notice] = field(default_factory=list)
	# Players whose Discord access must be recalculated.
	resync: list[Player] = field(default_factory=list)
	# (player, office, given) for the President / Councillor Discord roles.
	roles: list[tuple[Player, str, bool]] = field(default_factory=list)


# The president's commands take `player` None when control runs them instead.


def _president(player: Player | None) -> None:
	if player is not None and not player.president:
		raise GameError("Only the president can do this.")


def _by(player: Player | None) -> str:
	return mention(player) if player else "Control"


def _chambers_ship(galaxy: Galaxy) -> Ship:
	ship = galaxy.ships.get(galaxy.council_chambers_ship or "")
	if ship is None:
		raise GameError("There are no council chambers.")
	return ship


def _in_chambers(galaxy: Galaxy, player: Player | None) -> Ship:
	_president(player)
	ship = _chambers_ship(galaxy)
	if player is not None and player.location != ship.id:
		raise GameError(f"You must be aboard the {ship.name}, where the chambers are.")
	return ship


def _aboard(galaxy: Galaxy, *ship_ids: str | None) -> list[Player]:
	return [p for p in galaxy.players.values() if p.location in ship_ids]


def _council(galaxy: Galaxy) -> list[Player]:
	return [
		p
		for p in galaxy.players.values()
		if (p.councillor or p.president) and not p.dead
	]


def summon(galaxy: Galaxy, player: Player | None) -> list[Notice]:
	_president(player)
	ship = _chambers_ship(galaxy)
	notices = [
		Notice(
			PDA,
			f"**The council is convening** in the chambers aboard the {ship.name}.",
			member.id,
		)
		for member in _council(galaxy)
	]
	if player is None:
		return notices  # control doesn't need telling it summoned the council
	notices.append(
		Notice(
			CONTROL,
			f"{mention(player)} has summoned the council.",
			claim=True,
			links=((COUNCIL, ""),),
		)
	)
	return notices


def relocate(galaxy: Galaxy, player: Player | None, ship: Ship) -> CouncilChange:
	"""`player` is None when control moves the chambers (e.g. off a destroyed ship)."""
	if player is not None:
		_president(player)
	if ship.destroyed:
		raise GameError(f"The {ship.name} has been destroyed.")
	if galaxy.council_chambers_ship == ship.id:
		raise GameError(f"The chambers are already on the {ship.name}.")
	old = galaxy.council_chambers_ship
	galaxy.council_chambers_ship = ship.id
	change = CouncilChange(resync=_aboard(galaxy, old, ship.id))
	change.notices = [
		Notice(PDA, f"The council chambers have moved to the {ship.name}.", member.id)
		for member in _council(galaxy)
	]
	change.notices.append(
		Notice(
			CONTROL,
			f"{_by(player)} moved the council chambers to the {ship.name}.",
		)
	)
	return change


def seal(galaxy: Galaxy, player: Player | None) -> CouncilChange:
	"""Everyone not on the council or permitted is removed and kept out until /unseal or
	the end of the turn."""
	ship = _in_chambers(galaxy, player)
	if galaxy.council_sealed:
		raise GameError("The chambers are already sealed.")
	galaxy.council_sealed = True
	return CouncilChange(
		[Notice(CONTROL, f"{_by(player)} sealed the council chambers.")],
		_aboard(galaxy, ship.id),
	)


def unseal(galaxy: Galaxy, player: Player | None) -> CouncilChange:
	ship = _in_chambers(galaxy, player)
	if not galaxy.council_sealed:
		raise GameError("The chambers aren't sealed.")
	galaxy.council_sealed = False
	return CouncilChange([], _aboard(galaxy, ship.id))


def permit(galaxy: Galaxy, player: Player | None, target: Player) -> CouncilChange:
	_in_chambers(galaxy, player)
	if target.councillor or target.president:
		raise GameError(f"{mention(target)} is on the council and may always speak.")
	if target.permitted:
		raise GameError(f"{mention(target)} is already permitted.")
	target.permitted = True
	return CouncilChange(
		[Notice(PDA, "You may now speak in the council chambers.", target.id)],
		[target],
	)


def gag(galaxy: Galaxy, player: Player | None, target: Player) -> CouncilChange:
	_in_chambers(galaxy, player)
	if target.councillor or target.president:
		raise GameError("Members of the council can't be gagged.")
	if not target.permitted:
		raise GameError(f"{mention(target)} isn't permitted to speak.")
	target.permitted = False
	return CouncilChange(
		[Notice(PDA, "You may no longer speak in the council chambers.", target.id)],
		[target],
	)


def elect(player: Player, office: str) -> CouncilChange:
	"""/electpresident and /electcouncillor. Nobody else is deposed, and it isn't announced."""
	if player.dead:
		raise GameError(f"{mention(player)} is dead.")
	if getattr(player, office):
		raise GameError(f"{mention(player)} is already {office}.")
	setattr(player, office, True)
	player.permitted = False
	return CouncilChange(
		[Notice(PDA, f"You are now **{office}**.", player.id)],
		[player],
		[(player, office, True)],
	)


def depose(player: Player, office: str | None) -> CouncilChange:
	"""Removes one office, or both if none is given."""
	offices = [office] if office else [o for o in OFFICES if getattr(player, o)]
	held = [o for o in offices if getattr(player, o)]
	if not held:
		raise GameError(f"{mention(player)} doesn't hold that office.")
	change = CouncilChange(resync=[player])
	for o in held:
		setattr(player, o, False)
		change.roles.append((player, o, False))
		change.notices.append(Notice(PDA, f"You are no longer {o}.", player.id))
	return change

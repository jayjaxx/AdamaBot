"""Lockdowns and their combat channels, battlemap observers, first responders, and
loading units onto shuttles."""

from dataclasses import dataclass, field

from adama.channels import crew_role_name
from adama.models.galaxy import Coordinates, Galaxy
from adama.models.player import LOCATION_BRIG, LOCATION_COMBAT, LOCATION_NONE, Player
from adama.models.ship import Ship
from adama.models.shuttle import Shuttle
from adama.models.unit import Unit
from adama.rules import (
	BRIDGE,
	COMBAT,
	CONTROL,
	COUNCIL,
	HANGAR,
	PDA,
	GameError,
	Notice,
)
from adama.rules.jumps import check_coordinates
from adama.rules.players import combat_coordinates, mention
from adama.rules.resources import docked_shuttle
from adama.rules.shuttles import cancel_launches_for_ship
from adama.rules.stations import AEGIS

FIRST_RESPONDER = "first responder"


@dataclass
class CombatChange:
	notices: list[Notice] = field(default_factory=list)
	# Players whose Discord access must be updated.
	resync: list[Player] = field(default_factory=list)
	# Combat channels to delete (lockdowns that ended).
	deleted_channels: list[int] = field(default_factory=list)


def first_responders_at(galaxy: Galaxy, coordinates: str) -> list[Player]:
	"""Living first responders aboard a ship at the coordinates."""
	here = {ship.id for ship in galaxy.ships_at(coordinates)}
	return [
		p
		for p in galaxy.players.values()
		if p.first_responder and not p.dead and p.location in here
	]


def commanding_crew(galaxy: Galaxy, coords: Coordinates) -> list[Player]:
	"""First responders who see the combat channels without travelling to combat: crew
	of the commanding ship who are aboard it."""
	ship_id = coords.commanding_ship
	return [
		p
		for p in galaxy.players.values()
		if ship_id is not None
		and p.first_responder
		and not p.dead
		and ship_id in p.crew_affiliations
		and p.location == ship_id
	]


def _crew_roles(galaxy: Galaxy, player: Player) -> str:
	names = [
		crew_role_name(galaxy.ships[ship_id].name)
		for ship_id in player.crew_affiliations
		if ship_id in galaxy.ships
	]
	return ", ".join(names) or "none"


def observers(galaxy: Galaxy) -> list[Player]:
	return [p for p in galaxy.players.values() if p.battlemap_observer and not p.dead]


def _merge(*groups: list[Player]) -> list[Player]:
	seen: dict[str, Player] = {}
	for group in groups:
		for player in group:
			seen.setdefault(player.id, player)
	return list(seen.values())


def _combat_problem(galaxy: Galaxy, player: Player, coordinates: str) -> str | None:
	"""Why the first responder couldn't /travel combat to this lockdown, if they couldn't."""
	if player.dead:
		return "dead"
	if player.brigged or player.location == LOCATION_BRIG:
		return "in the brig"
	if player.location == LOCATION_COMBAT:
		where = combat_coordinates(galaxy, player)
		return None if where == coordinates else f"already in combat at {where}"
	ship = galaxy.ships.get(player.location)
	if ship is None:
		return "not aboard any ship"
	if ship.coordinates != coordinates:
		return f"aboard the {ship.name} at {ship.coordinates}"
	if player.wounded:
		return "wounded"
	if ship.isolated:
		return f"aboard the {ship.name}, which is isolated"
	return None


def readiness_report(
	galaxy: Galaxy, coordinates: str, commanders: list[Player]
) -> Notice | None:
	"""For control at /lockdown: first responders in the fleet who couldn't /travel
	combat, and combat ships at the coordinates with no first responder in their crew."""
	unable = []
	for player in galaxy.players.values():
		if not player.first_responder:
			continue
		problem = _combat_problem(galaxy, player, coordinates)
		if problem is None:
			continue
		if player in commanders:
			problem += ", but sees the combat as commanding crew"
		unable.append(f"{mention(player)} ({problem})")
	uncovered = [
		ship.name
		for ship in galaxy.ships_at(coordinates)
		if ship.combat_ship
		and not any(
			p.first_responder and not p.dead and ship.id in p.crew_affiliations
			for p in galaxy.players.values()
		)
	]
	if not unable and not uncovered:
		return None
	parts = [f"Lockdown at {coordinates}."]
	if unable:
		parts.append(
			"First responders who can't /travel combat: " + "; ".join(unable) + "."
		)
	if uncovered:
		parts.append(
			"Combat ships here with no first responder in their crew: "
			+ ", ".join(uncovered)
			+ "."
		)
	return Notice(CONTROL, " ".join(parts), claim=True, links=((COMBAT, coordinates),))


def lockdown(
	galaxy: Galaxy, coordinates: str, commanding: Ship | None
) -> tuple[Coordinates, CombatChange]:
	"""/lockdown: stops travel between the ships there and summons the first responders.
	The cog creates the combat channels and stores their IDs on the coordinates."""
	coordinates = check_coordinates(coordinates)
	coords = galaxy.coordinates.get(coordinates)
	ships = galaxy.ships_at(coordinates)
	if coords is None or not ships:
		raise GameError(f"No ships are at {coordinates}.")
	if coords.locked_down:
		raise GameError(f"{coordinates} is already in lockdown.")
	if commanding is None:
		raise GameError(f"There is no {AEGIS}: name the ship commanding the lockdown.")
	if commanding.destroyed:
		raise GameError(f"The {commanding.name} has been destroyed.")
	coords.locked_down = True
	coords.commanding_ship = commanding.id
	change = CombatChange()
	for ship in ships:
		change.notices.extend(
			cancel_launches_for_ship(galaxy, ship, f"{coordinates} is in lockdown.")
		)
	alert = f"**{coordinates} is in lockdown.** Nobody can travel between ships until it ends."
	for ship in ships:
		change.notices += [
			Notice(HANGAR, alert, ship.id),
			Notice(BRIDGE, alert, ship.id),
		]
	if galaxy.council_chambers_ship in {ship.id for ship in ships}:
		change.notices.append(Notice(COUNCIL, alert))
	commanders = commanding_crew(galaxy, coords)
	for player in _merge(first_responders_at(galaxy, coordinates), commanders):
		text = (
			f"**Action stations!** {coordinates} is in lockdown: come to the battlemap."
		)
		if player in commanders:
			text += (
				f" As crew of the {commanding.name}, which commands it, you can "
				"already see the combat channels while aboard."
			)
		else:
			text += " Use `/travel combat` in your hangar to join the combat."
		change.notices.append(Notice(PDA, text, player.id, ping=True))
	change.notices.append(
		Notice(
			CONTROL,
			f"{coordinates} is in lockdown, commanded by the {commanding.name}.",
		)
	)
	report = readiness_report(galaxy, coordinates, commanders)
	if report is not None:
		change.notices.append(report)
	change.resync = _merge(commanders, observers(galaxy))
	return coords, change


def end_lockdown(galaxy: Galaxy, coordinates: str | None) -> CombatChange:
	"""/endlockdown: ends the lockdown at the coordinates, or every lockdown if None.
	Players in combat go back to the ship they entered it from."""
	if coordinates is None:
		ended = [c for c in galaxy.coordinates.values() if c.locked_down]
		if not ended:
			raise GameError("No lockdown is in effect.")
	else:
		coordinates = check_coordinates(coordinates)
		coords = galaxy.coordinates.get(coordinates)
		if coords is None or not coords.locked_down:
			raise GameError(f"{coordinates} isn't in lockdown.")
		ended = [coords]

	change = CombatChange()
	for coords in ended:
		coords.locked_down = False
		for channel_id in (
			coords.combat_text_channel_id,
			coords.combat_voice_channel_id,
		):
			if channel_id is not None:
				change.deleted_channels.append(channel_id)
		coords.combat_text_channel_id = coords.combat_voice_channel_id = None
		commanders = commanding_crew(galaxy, coords)
		commanding = galaxy.ships.get(coords.commanding_ship or "")
		if commanding is not None and commanding.destroyed:
			commanding = None
		coords.commanding_ship = None
		returned: list[Player] = []
		if commanding is not None and commanding.coordinates != coords.coordinates:
			commanding = None  # it jumped away too
		moved_over: list[str] = []
		unplaced: list[str] = []
		called: list[tuple[str, str]] = []  # their PDAs, linked in the alert
		for player in galaxy.players.values():
			origin = galaxy.ships.get(player.combat_origin_ship or "")
			if origin is None:
				continue
			if combat_coordinates(galaxy, player) != coords.coordinates:
				continue
			# Back to their ship; if it left, to the commanding ship; if that left
			# too, nowhere until control places them.
			# Wounded players stay for control to take to a Sickbay.
			dest: Ship | None = origin
			roles = f"crew roles: {_crew_roles(galaxy, player)}"
			if player.wounded:
				dest = None
				unplaced.append(f"{mention(player)} ({roles}; wounded)")
				called.append((PDA, player.id))
			elif origin.coordinates != coords.coordinates:
				dest = commanding
				reason = f"the {origin.name} had left"
				if dest is None:
					reason += " and so had the commanding ship"
				who = f"{mention(player)} ({roles}; {reason})"
				(moved_over if dest else unplaced).append(who)
				called.append((PDA, player.id))
			player.location = dest.id if dest else LOCATION_NONE
			player.combat_origin_ship = None
			player.combat_coordinates = None
			returned.append(player)
			if dest is not None:
				text = f"Combat is over: you're aboard the {dest.name}."
			elif player.wounded:
				text = (
					"Combat is over. You're wounded: control will take you to a ship "
					"with a Sickbay shortly. If they don't within a couple of minutes, "
					"use /control."
				)
			else:
				text = (
					"Combat is over. Your ship has left: control will place you "
					"shortly. If they don't within a couple of minutes, use /control."
				)
			change.notices.append(Notice(PDA, text, player.id))
		alert = [f"The lockdown at {coords.coordinates} ended."]
		if moved_over and commanding is not None:
			alert.append(
				"These players' ships had left, so they are now aboard the "
				f"{commanding.name}: " + "; ".join(moved_over) + "."
			)
		if unplaced:
			alert.append(
				"These players have no location: place them with /teleportplayer. "
				+ "; ".join(unplaced)
				+ "."
			)
		if len(alert) > 1:
			change.notices.append(
				Notice(CONTROL, " ".join(alert), claim=True, links=tuple(called))
			)
		ended_text = f"The lockdown at {coords.coordinates} has ended."
		here = galaxy.ships_at(coords.coordinates)
		for ship in here:
			change.notices += [
				Notice(HANGAR, ended_text, ship.id),
				Notice(BRIDGE, ended_text, ship.id),
			]
		if galaxy.council_chambers_ship in {ship.id for ship in here}:
			change.notices.append(Notice(COUNCIL, ended_text))
		change.resync = _merge(change.resync, returned, commanders)
	change.resync = _merge(change.resync, observers(galaxy))
	return change


def transfer_command(
	galaxy: Galaxy, coordinates: str, ship: Ship
) -> tuple[str, CombatChange]:
	"""/transfercommand: another ship commands the lockdown (e.g. after the commanding
	ship jumped away). Its first responders aboard get the combat channels."""
	coordinates = check_coordinates(coordinates)
	coords = galaxy.coordinates.get(coordinates)
	if coords is None or not coords.locked_down:
		raise GameError(f"{coordinates} isn't in lockdown.")
	if coords.commanding_ship == ship.id:
		raise GameError(f"The {ship.name} already commands the lockdown.")
	if ship.destroyed:
		raise GameError(f"The {ship.name} has been destroyed.")
	before = commanding_crew(galaxy, coords)
	coords.commanding_ship = ship.id
	after = commanding_crew(galaxy, coords)
	change = CombatChange(resync=_merge(before, after))
	for player in after:
		change.notices.append(
			Notice(
				PDA,
				f"The {ship.name} now commands the lockdown at {coordinates}: you can "
				"see the combat channels while aboard.",
				player.id,
				ping=True,
			)
		)
	return f"The {ship.name} now commands the lockdown at {coordinates}.", change


# Observers and first responders


def set_observer(
	galaxy: Galaxy, by: Player | None, target: Player, observing: bool
) -> list[Notice]:
	"""/observer and /removeobserver. `by` is None when control runs it; otherwise they
	must be a first responder who is crew of the Aegis."""
	if by is not None:
		aegis = galaxy.ship_by_name(AEGIS)
		if aegis is None:
			raise GameError(f"There is no {AEGIS}, so only control can do this.")
		if not by.first_responder or aegis.id not in by.crew_affiliations:
			raise GameError(
				f"Only first responders who are crew of the {AEGIS} can do this."
			)
	if target.dead:
		raise GameError(f"{mention(target)} is dead.")
	if observing and target.battlemap_observer:
		raise GameError(f"{mention(target)} is already an observer.")
	if not observing and not target.battlemap_observer:
		raise GameError(f"{mention(target)} isn't an observer.")
	target.battlemap_observer = observing
	text = (
		"You can now watch the battlemap: you can read every combat channel."
		if observing
		else "You can no longer watch the battlemap."
	)
	return [Notice(PDA, text, target.id)]


def set_first_responder(player: Player, responding: bool) -> list[Notice]:
	"""/setfirstresponder and /unsetfirstresponder (the cog gives or removes the role)."""
	if player.dead:
		raise GameError(f"{mention(player)} is dead.")
	if responding and player.first_responder:
		raise GameError(f"{mention(player)} is already a first responder.")
	if not responding and not player.first_responder:
		raise GameError(f"{mention(player)} isn't a first responder.")
	player.first_responder = responding
	text = (
		"You are now a first responder: when action stations is called you are "
		"summoned to the battlemap and may /travel combat."
		if responding
		else "You are no longer a first responder."
	)
	return [Notice(PDA, text, player.id)]


# Units on shuttles


def _check_unit_crew(galaxy: Galaxy, player: Player | None, unit: Unit) -> None:
	if player is None or unit.affiliation in player.crew_affiliations:
		return
	owner = galaxy.ships.get(unit.affiliation)
	ship = f"the {owner.name}" if owner else "its ship"
	raise GameError(f"Only crew of {ship} can move `{unit.id}` {unit.name}.")


def _listed(units: list[Unit]) -> str:
	return ", ".join(f"`{u.id}` {u.name}" for u in units)


def load_units(
	galaxy: Galaxy,
	player: Player | None,
	ship: Ship,
	unit_ids: list[str],
	shuttle_name: str | None,
) -> list[Notice]:
	"""/loadunit, for one or several units: all are loaded or none. `player` is None
	when control runs it."""
	units: list[Unit] = []
	for unit_id in unit_ids:
		unit = ship.units.get(unit_id.strip())
		if unit is None:
			raise GameError(f"There is no unit `{unit_id}` aboard the {ship.name}.")
		if unit.exhausted:
			raise GameError(f"`{unit.id}` {unit.name} is exhausted.")
		_check_unit_crew(galaxy, player, unit)
		units.append(unit)
	shuttle = docked_shuttle(ship, shuttle_name)
	for unit in units:
		del ship.units[unit.id]
		shuttle.units[unit.id] = unit
	by = mention(player) if player else "Control"
	return [
		Notice(HANGAR, f"{by} loaded {_listed(units)} onto {shuttle.name}.", ship.id)
	]


def load_unit(
	galaxy: Galaxy,
	player: Player | None,
	ship: Ship,
	unit_id: str,
	shuttle_name: str | None,
) -> list[Notice]:
	return load_units(galaxy, player, ship, [unit_id], shuttle_name)


def unload_units(
	galaxy: Galaxy, player: Player | None, ship: Ship, unit_ids: list[str]
) -> list[Notice]:
	"""/unloadunit: anyone aboard may unload any unit, whatever its affiliation. The unit
	keeps its affiliation (e.g. a boarding party). All are unloaded or none."""
	found: list[tuple[Shuttle, Unit]] = []
	for unit_id in unit_ids:
		unit_id = unit_id.strip()
		match = next(
			(
				(shuttle, shuttle.units[unit_id])
				for shuttle in ship.shuttles.values()
				if unit_id in shuttle.units
			),
			None,
		)
		if match is None:
			raise GameError(
				f"There is no unit `{unit_id}` on a shuttle docked at the {ship.name}."
			)
		found.append(match)
	by = mention(player) if player else "Control"
	notices: list[Notice] = []
	for shuttle in {id(s): s for s, _ in found}.values():
		units = [u for s, u in found if s is shuttle]
		for unit in units:
			del shuttle.units[unit.id]
			ship.units[unit.id] = unit
		notices.append(
			Notice(
				HANGAR,
				f"{by} unloaded {_listed(units)} from {shuttle.name}.",
				ship.id,
			)
		)
	return notices


def unload_unit(
	galaxy: Galaxy, player: Player | None, ship: Ship, unit_id: str
) -> list[Notice]:
	return unload_units(galaxy, player, ship, [unit_id])

"""Shuttles: launching, interception and clearance."""

from adama import ids
from adama.models.galaxy import Galaxy, Launch
from adama.models.player import Player
from adama.models.ship import Ship
from adama.models.shuttle import Shuttle
from adama.rules import BRIDGE, HANGAR, PDA, GameError, Notice, isolation, sentence
from adama.rules.players import mention
from adama.rules.resources import docked_shuttle

LAUNCH_SECONDS = 10


def launch_by(galaxy: Galaxy, player: Player) -> Launch | None:
	for launch in galaxy.launches.values():
		if launch.player_id == player.id:
			return launch
	return None


def cancel_launch(galaxy: Galaxy, shuttle_id: str, reason: str) -> list[Notice]:
	"""Stops a launch countdown, if one is running, and tells the origin hangar."""
	launch = galaxy.launches.pop(shuttle_id, None)
	if launch is None:
		return []
	origin = galaxy.ships.get(launch.origin_id)
	shuttle = origin.shuttles.get(shuttle_id) if origin else None
	if shuttle is not None:
		shuttle.launching = False
	name = shuttle.name if shuttle else "the shuttle"
	return [
		Notice(HANGAR, f"The launch of {name} was aborted: {reason}", launch.origin_id)
	]


def cancel_launches_for_ship(galaxy: Galaxy, ship: Ship, reason: str) -> list[Notice]:
	"""Stops every launch to or from the ship."""
	notices: list[Notice] = []
	for shuttle_id, launch in list(galaxy.launches.items()):
		if ship.id in (launch.origin_id, launch.destination_id):
			notices.extend(cancel_launch(galaxy, shuttle_id, reason))
	return notices


def cancel_player_launch(galaxy: Galaxy, player: Player, reason: str) -> list[Notice]:
	launch = launch_by(galaxy, player)
	return cancel_launch(galaxy, launch.shuttle_id, reason) if launch else []


def _launch_problem(
	galaxy: Galaxy, player: Player, origin: Ship, shuttle: Shuttle, dest: Ship
) -> str | None:
	"""Why the shuttle can't fly from origin to dest right now, if it can't."""
	if player.wounded:
		return "you're wounded and can't launch."
	if dest.id == origin.id:
		return f"{shuttle.name} is already docked at the {dest.name}."
	if dest.destroyed:
		return f"the {dest.name} has been destroyed."
	if dest.coordinates != origin.coordinates:
		return f"the {dest.name} isn't at these coordinates."
	for ship in (origin, dest):
		if ship.isolated:
			return isolation(ship.name)
	coords = galaxy.coordinates.get(origin.coordinates)
	if coords is not None and coords.locked_down:
		return f"{origin.coordinates} is in lockdown: nobody can travel between ships."
	if origin.jumping:
		return f"the {origin.name} is preparing to jump."
	if shuttle.intercepted and not (
		shuttle.clearance or shuttle.id in player.clearances
	):
		return (
			f"{shuttle.name} has been intercepted and can't launch without clearance."
		)
	return None


def launch(
	galaxy: Galaxy,
	player: Player,
	origin: Ship,
	shuttle_name: str | None,
	destination: str,
) -> tuple[Launch, list[Notice]]:
	"""/launch: starts the countdown. The cog finishes it with complete_launch."""
	if player.location != origin.id:
		raise GameError(f"You aren't aboard the {origin.name}.")
	shuttle = docked_shuttle(origin, shuttle_name)
	if shuttle.launching:
		raise GameError(f"{shuttle.name} is already launching.")
	if launch_by(galaxy, player) is not None:
		raise GameError("You're already launching a shuttle.")
	dest = galaxy.ship_by_name(destination)
	if dest is None:
		raise GameError(f'There is no ship named "{destination}".')
	problem = _launch_problem(galaxy, player, origin, shuttle, dest)
	if problem:
		raise GameError(sentence(problem))
	shuttle.launching = True
	started = Launch(shuttle.id, player.id, origin.id, dest.id)
	galaxy.launches[shuttle.id] = started
	return started, [
		Notice(
			HANGAR,
			f"{mention(player)} is launching {shuttle.name} to the {dest.name} in "
			f"{LAUNCH_SECONDS} seconds. It can be stopped with /intercept.",
			origin.id,
		)
	]


def complete_launch(galaxy: Galaxy, started: Launch) -> list[Notice]:
	"""Ends the countdown: the shuttle, its cargo and the launching player arrive.

	Does nothing if the launch was cancelled (or replaced) in the meantime."""
	if galaxy.launches.get(started.shuttle_id) is not started:
		return []
	origin = galaxy.ships.get(started.origin_id)
	dest = galaxy.ships.get(started.destination_id)
	player = galaxy.players.get(started.player_id)
	shuttle = origin.shuttles.get(started.shuttle_id) if origin else None
	if origin is None or dest is None or player is None or shuttle is None:
		galaxy.launches.pop(started.shuttle_id, None)
		if shuttle is not None:
			shuttle.launching = False
		return []
	problem = (
		"the pilot is no longer aboard."
		if player.location != origin.id
		else _launch_problem(galaxy, player, origin, shuttle, dest)
	)
	if problem:
		return cancel_launch(galaxy, shuttle.id, problem)

	del galaxy.launches[shuttle.id]
	shuttle.launching = False
	shuttle.clearance = False
	del origin.shuttles[shuttle.id]
	dest.shuttles[shuttle.id] = shuttle
	player.location = dest.id
	player.clearances.clear()
	return [
		Notice(
			HANGAR,
			f"{mention(player)} has launched {shuttle.name} to the {dest.name}.",
			origin.id,
		),
		Notice(
			HANGAR, f"{mention(player)} has arrived aboard {shuttle.name}.", dest.id
		),
	]


def intercept(
	galaxy: Galaxy,
	player: Player | None,
	ship: Ship,
	shuttle_name: str | None,
	by: str,
) -> list[Notice]:
	"""/intercept: stops a launch, and stops the shuttle launching until cleared.
	`player` is None when control intercepts."""
	if player is not None and player.wounded:
		raise GameError("You're wounded and can't intercept.")
	shuttle = docked_shuttle(ship, shuttle_name)
	if shuttle.clearance:
		raise GameError(f"{shuttle.name} has clearance and can't be intercepted.")
	running = galaxy.launches.get(shuttle.id)
	pilot = galaxy.players.get(running.player_id) if running else None
	if pilot is not None and shuttle.id in pilot.clearances:
		raise GameError(
			f"{mention(pilot)} has clearance for {shuttle.name}, so it can't be intercepted."
		)
	shuttle.intercepted = True
	if running is not None:
		galaxy.launches.pop(shuttle.id)
		shuttle.launching = False
		who = f"{mention(pilot)}'s" if pilot else "its"
		text = f"{by} intercepted {shuttle.name}: {who} launch is aborted."
	else:
		text = f"{by} intercepted {shuttle.name}: it can't launch without clearance."
	return [Notice(HANGAR, text, ship.id)]


def clearance(
	ship: Ship, shuttle_name: str | None, target: Player | None
) -> list[Notice]:
	"""/clearance: clears a shuttle for launch, or gives a player clearance for it."""
	shuttle = docked_shuttle(ship, shuttle_name)
	if target is None:
		shuttle.clearance = True
		shuttle.intercepted = False
		return [Notice(HANGAR, f"{shuttle.name} has been cleared for launch.", ship.id)]
	if shuttle.id in target.clearances:
		raise GameError(f"{mention(target)} already has clearance for {shuttle.name}.")
	target.clearances.append(shuttle.id)
	return [
		Notice(
			PDA,
			f"You have been given clearance for {shuttle.name} at the {ship.name}.",
			target.id,
		)
	]


def revoke_shuttle(ship: Ship, shuttle_name: str | None) -> list[Notice]:
	shuttle = docked_shuttle(ship, shuttle_name)
	if not shuttle.clearance:
		raise GameError(f"{shuttle.name} doesn't have clearance.")
	shuttle.clearance = False
	return [Notice(HANGAR, f"Clearance for {shuttle.name} has been revoked.", ship.id)]


def revoke_player(target: Player) -> list[Notice]:
	if not target.clearances:
		raise GameError(f"{mention(target)} has no clearances.")
	target.clearances.clear()
	return [Notice(PDA, "Your shuttle clearances have been revoked.", target.id)]


def shuttle_named(galaxy: Galaxy, name: str) -> tuple[Ship, Shuttle]:
	found = galaxy.find_shuttle(name)
	if found is None:
		raise GameError(f'There is no shuttle named "{name}".')
	return found


def teleport_shuttle(galaxy: Galaxy, name: str, destination: Ship) -> list[Notice]:
	"""/shuttle teleport: moves a shuttle and its cargo, ignoring every restriction."""
	origin, shuttle = shuttle_named(galaxy, name)
	if origin.id == destination.id:
		raise GameError(f"{shuttle.name} is already docked at the {destination.name}.")
	notices = cancel_launch(galaxy, shuttle.id, "control moved the shuttle.")
	del origin.shuttles[shuttle.id]
	destination.shuttles[shuttle.id] = shuttle
	notices.append(Notice(HANGAR, f"{shuttle.name} has departed.", origin.id))
	notices.append(Notice(HANGAR, f"{shuttle.name} has arrived.", destination.id))
	return notices


def conjure_shuttle(galaxy: Galaxy, name: str, ship: Ship) -> Shuttle:
	name = name.strip()
	if not name:
		raise GameError("The shuttle needs a name.")
	if galaxy.find_shuttle(name) is not None:
		raise GameError(f'A shuttle named "{name}" already exists.')
	if galaxy.ship_by_name(name) is not None:
		raise GameError(f'"{name}" is already the name of a ship.')
	try:
		shuttle_id = ids.next_object_id(
			galaxy, ids.SHUTTLE, ids.conjure_prefix(galaxy, ship.prefix)
		)
	except ids.NoFreeIDError as error:
		raise GameError(str(error)) from error
	shuttle = Shuttle(id=shuttle_id, name=name)
	ship.shuttles[shuttle.id] = shuttle
	return shuttle


def destroy_shuttle(galaxy: Galaxy, name: str) -> tuple[Shuttle, list[Notice]]:
	"""/shuttle destroy: removes the shuttle with all its resources and units."""
	ship, shuttle = shuttle_named(galaxy, name)
	notices = cancel_launch(galaxy, shuttle.id, "the shuttle was destroyed.")
	for player in galaxy.players.values():
		if shuttle.id in player.clearances:
			player.clearances.remove(shuttle.id)
	del ship.shuttles[shuttle.id]
	notices.append(Notice(HANGAR, f"{shuttle.name} has been destroyed.", ship.id))
	return shuttle, notices


def drop_stale_countdowns(galaxy: Galaxy) -> list[Notice]:
	"""Launches and jumps whose countdown was lost in a restart are cancelled."""
	notices: list[Notice] = []
	for ship in galaxy.ships.values():
		for shuttle in ship.shuttles.values():
			if shuttle.launching and shuttle.id not in galaxy.launches:
				shuttle.launching = False
				notices.append(
					Notice(
						HANGAR,
						f"The launch of {shuttle.name} was cancelled because ADAMA restarted. Launch again.",
						ship.id,
					)
				)
		if ship.jumping and ship.id not in galaxy.jumps:
			ship.jumping = False
			notices.append(
				Notice(
					BRIDGE,
					"The jump countdown was cancelled because ADAMA restarted. Run /jump again.",
					ship.id,
				)
			)
	return notices

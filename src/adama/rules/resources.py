"""Moving resources between ships and shuttles."""

from adama.models.galaxy import Galaxy
from adama.models.resources import RESOURCE_NAMES, ResourcesContainer
from adama.models.ship import Ship
from adama.models.shuttle import Shuttle
from adama.rules import BRIDGE, HANGAR, GameError, Notice


def check_resource(name: str) -> str:
	name = name.casefold()
	if name not in RESOURCE_NAMES:
		raise GameError(
			f'"{name}" isn\'t a resource. Use one of: {", ".join(RESOURCE_NAMES)}.'
		)
	return name


def docked_shuttle(ship: Ship, name: str | None) -> Shuttle:
	"""A shuttle docked at the ship. The name may be left out if only one is docked."""
	if name is None:
		if len(ship.shuttles) == 1:
			return next(iter(ship.shuttles.values()))
		if not ship.shuttles:
			raise GameError(f"No shuttles are docked at the {ship.name}.")
		raise GameError(
			"More than one shuttle is docked here: you must specify the shuttle name."
		)
	for shuttle in ship.shuttles.values():
		if shuttle.name.casefold() == name.casefold():
			return shuttle
	raise GameError(f'No shuttle named "{name}" is docked at the {ship.name}.')


def _move(
	source: ResourcesContainer,
	dest: ResourcesContainer,
	amount: int,
	resource: str,
	source_name: str,
) -> None:
	if amount <= 0:
		raise GameError("The amount must be more than 0.")
	if source.get(resource) < amount:
		raise GameError(
			f"Not enough {resource}: {source_name} has {source.get(resource)}."
		)
	source.set(resource, source.get(resource) - amount)
	dest.set(resource, dest.get(resource) + amount)


def load(
	ship: Ship, shuttle_name: str | None, amount: int, resource: str, by: str
) -> list[Notice]:
	resource = check_resource(resource)
	shuttle = docked_shuttle(ship, shuttle_name)
	_move(ship.resources, shuttle.resources, amount, resource, f"the {ship.name}")
	return [
		Notice(
			HANGAR, f"{by} loaded {amount} {resource} onto {shuttle.name}.", ship.id
		),
		Notice(
			BRIDGE,
			f"{by} loaded {amount} {resource} onto {shuttle.name}: the {ship.name} now has "
			f"**{ship.resources.get(resource)}** {resource}, {shuttle.name} has {shuttle.resources.get(resource)}.",
			ship.id,
		),
	]


def unload(
	ship: Ship, shuttle_name: str | None, amount: int, resource: str, by: str
) -> list[Notice]:
	resource = check_resource(resource)
	shuttle = docked_shuttle(ship, shuttle_name)
	_move(shuttle.resources, ship.resources, amount, resource, shuttle.name)
	return [
		Notice(
			HANGAR, f"{by} unloaded {amount} {resource} from {shuttle.name}.", ship.id
		),
		Notice(
			BRIDGE,
			f"{by} unloaded {amount} {resource} from {shuttle.name}: the {ship.name} now has "
			f"**{ship.resources.get(resource)}** {resource}, {shuttle.name} has {shuttle.resources.get(resource)}.",
			ship.id,
		),
	]


def find_holder(galaxy: Galaxy, name: str) -> tuple[str, ResourcesContainer, Ship]:
	"""A ship or shuttle by name, its resources, and the ship it is (or is docked at)."""
	ship = galaxy.ship_by_name(name)
	if ship is not None:
		return f"the {ship.name}", ship.resources, ship
	for docked_at, shuttle in galaxy.iter_shuttles():
		if shuttle.name.casefold() == name.casefold():
			return shuttle.name, shuttle.resources, docked_at
	raise GameError(f'There is no ship or shuttle named "{name}".')


def _bridge_change(
	ship: Ship, label: str, resources: ResourcesContainer, resource: str, change: str
) -> Notice:
	"""Tells a ship's bridge that it (or a shuttle docked there) gained or lost resources."""
	return Notice(
		BRIDGE,
		f"{change}: {label} now has **{resources.get(resource)}** {resource}.",
		ship.id,
	)


def add_resource(
	galaxy: Galaxy, amount: int, resource: str, holder: str
) -> tuple[str, list[Notice]]:
	"""/addresource: adds (or with a negative amount removes) resources, never below 0."""
	resource = check_resource(resource)
	label, resources, ship = find_holder(galaxy, holder)
	before = resources.get(resource)
	resources.set(resource, max(0, before + amount))
	change = resources.get(resource) - before
	text = f"{label} now has {resources.get(resource)} {resource}."
	if not change:
		return text, []
	verb = "added to" if change > 0 else "removed from"
	notice = _bridge_change(
		ship,
		label,
		resources,
		resource,
		f"Control {verb} {label}: {change:+d} {resource}",
	)
	return text, [notice]


def teleport_resource(
	galaxy: Galaxy, amount: int, resource: str, origin: str, destination: str
) -> tuple[str, list[Notice]]:
	resource = check_resource(resource)
	origin_label, source, origin_ship = find_holder(galaxy, origin)
	dest_label, dest, dest_ship = find_holder(galaxy, destination)
	_move(source, dest, amount, resource, origin_label)
	moved = f"Control moved {amount} {resource} from {origin_label} to {dest_label}"
	if origin_ship is dest_ship:
		notices = [
			Notice(
				BRIDGE,
				f"{moved}: {origin_label} now has **{source.get(resource)}** {resource}, "
				f"{dest_label} has **{dest.get(resource)}**.",
				origin_ship.id,
			)
		]
	else:
		notices = [
			_bridge_change(origin_ship, origin_label, source, resource, moved),
			_bridge_change(dest_ship, dest_label, dest, resource, moved),
		]
	return f"Moved {amount} {resource} from {origin_label} to {dest_label}.", notices

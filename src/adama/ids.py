"""The ID scheme: 4 digits, globally unique, a 2-digit prefix plus a 2-digit postfix.

Ships use prefixes 01-98 and their own ID is XX00. Players use prefix 00. Prefix 99 is
for anything control conjures after /startgame; before that, conjured objects use their
ship's prefix. Postfix 01-09 holds shuttles plus overflow for any full category.
"""

from adama.models.galaxy import Galaxy

PLAYER_PREFIX = "00"
CONTROL_PREFIX = "99"
SHIP_PREFIXES = range(1, 99)

STATION = "station"
ACTION_CARD = "action card"
UNIT = "unit"
SHUTTLE = "shuttle"

POSTFIX_RANGES = {
	STATION: range(10, 40),
	ACTION_CARD: range(40, 70),
	UNIT: range(70, 100),
	SHUTTLE: range(1, 10),
}
OVERFLOW = range(1, 10)


class NoFreeIDError(Exception):
	pass


def make_id(prefix: str, postfix: int) -> str:
	return f"{prefix}{postfix:02d}"


def ship_id(prefix: int) -> str:
	return make_id(f"{prefix:02d}", 0)


def postfix_allowed(kind: str, postfix: int) -> bool:
	"""Whether a postfix is in its category's range or the 01-09 overflow."""
	return postfix in POSTFIX_RANGES[kind] or postfix in OVERFLOW


def conjure_prefix(galaxy: Galaxy, ship_prefix: str) -> str:
	return CONTROL_PREFIX if galaxy.started else ship_prefix


def next_object_id(galaxy: Galaxy, kind: str, prefix: str) -> str:
	"""The lowest free ID for a new station, action card, unit or shuttle.

	Unit IDs are never reused, so any ID a unit has ever held stays taken.
	"""
	taken = galaxy.all_ids() | galaxy.used_unit_ids
	for postfix in [*POSTFIX_RANGES[kind], *OVERFLOW]:
		id = make_id(prefix, postfix)
		if id not in taken:
			return id
	raise NoFreeIDError(f"No free {kind} or overflow IDs with prefix {prefix}")


def next_ship_id(galaxy: Galaxy) -> str:
	for prefix in SHIP_PREFIXES:
		id = ship_id(prefix)
		if id not in galaxy.ships:
			return id
	raise NoFreeIDError("No free ship IDs")


def next_player_id(galaxy: Galaxy) -> str:
	for postfix in range(1, 100):
		id = make_id(PLAYER_PREFIX, postfix)
		if id not in galaxy.players:
			return id
	raise NoFreeIDError("No free player IDs")

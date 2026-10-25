from pathlib import Path

import pytest

from adama import ids
from adama.models import Galaxy, Player, Ship, Station, Unit
from adama.models.galaxy import STATE_IN_PROGRESS
from adama.shipfiles import parse_ship

ROOT = Path(__file__).parent.parent


def galaxy_with_aegis() -> tuple[Galaxy, Ship]:
	galaxy = Galaxy(1)
	ship, _ = parse_ship((ROOT / "shipdefs" / "aegis.json").read_bytes())
	assert ship is not None
	galaxy.add_ship(ship)
	return galaxy, ship


def test_next_station_fills_the_lowest_gap():
	galaxy, ship = galaxy_with_aegis()
	# The Aegis uses 0110-0133.
	assert ids.next_object_id(galaxy, ids.STATION, "01") == "0134"
	del ship.stations["0112"]
	assert ids.next_object_id(galaxy, ids.STATION, "01") == "0112"


def test_full_category_overflows_into_01_09_after_the_shuttles():
	galaxy, ship = galaxy_with_aegis()
	for postfix in range(34, 40):
		id = ids.make_id("01", postfix)
		ship.stations[id] = Station(id=id, name="Filler")
	# 0101 and 0102 are the Aegis shuttles.
	assert ids.next_object_id(galaxy, ids.STATION, "01") == "0103"


def test_unit_ids_are_never_reused():
	galaxy, ship = galaxy_with_aegis()
	# The Aegis has 23 units: 0170-0192.
	assert ids.next_object_id(galaxy, ids.UNIT, "01") == "0193"
	del ship.units["0192"]
	assert ids.next_object_id(galaxy, ids.UNIT, "01") == "0193"


def test_overflow_slots_once_held_by_a_unit_stay_taken():
	galaxy = Galaxy(1)
	galaxy.used_unit_ids.add("0103")
	assert ids.next_object_id(galaxy, ids.SHUTTLE, "01") == "0101"
	galaxy.used_unit_ids.update({"0101", "0102"})
	assert ids.next_object_id(galaxy, ids.SHUTTLE, "01") == "0104"


def test_ids_held_elsewhere_still_count():
	"""A unit that left its ship on a shuttle, or a card in a hand, still holds its ID."""
	galaxy, ship = galaxy_with_aegis()
	card = ship.action_cards.pop("0140")
	galaxy.players["0001"] = Player(
		id="0001", discord_user_id=5, action_cards={"0140": card}
	)
	assert ids.next_object_id(galaxy, ids.ACTION_CARD, "01") == "0153"


def test_no_free_ids():
	galaxy = Galaxy(1)
	ship = Ship(id="0100", name="Full", coordinates="12903")
	galaxy.add_ship(ship)
	for postfix in [*range(1, 10), *range(70, 100)]:
		id = ids.make_id("01", postfix)
		ship.units[id] = Unit(id=id, name="Work Team", affiliation="0100")
	with pytest.raises(ids.NoFreeIDError):
		ids.next_object_id(galaxy, ids.UNIT, "01")


def test_conjure_prefix_switches_to_99_after_startgame():
	galaxy = Galaxy(1)
	assert ids.conjure_prefix(galaxy, "05") == "05"
	galaxy.state = STATE_IN_PROGRESS
	assert ids.conjure_prefix(galaxy, "05") == "99"


def test_next_ship_and_player_ids():
	galaxy, _ = galaxy_with_aegis()
	assert ids.next_ship_id(galaxy) == "0200"
	assert ids.next_player_id(galaxy) == "0001"
	galaxy.players["0001"] = Player(id="0001", discord_user_id=1)
	assert ids.next_player_id(galaxy) == "0002"

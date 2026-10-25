import copy
import json
from pathlib import Path

import pytest

from adama.models import Galaxy
from adama.shipfiles import ShipFile, parse_ship, validate_ship_files

ROOT = Path(__file__).parent.parent
SHIPDEFS = sorted((ROOT / "shipdefs").glob("*.json"))


def ship_file(path: Path) -> ShipFile:
	return ShipFile(path.name, path.read_bytes())


def aegis() -> dict:
	return json.loads((ROOT / "shipdefs" / "aegis.json").read_text(encoding="utf-8"))


def problems_for(data: dict) -> list[str]:
	ship, problems = parse_ship(json.dumps(data))
	assert ship is None
	return problems


def test_there_are_eleven_ships():
	assert len(SHIPDEFS) == 11


@pytest.mark.parametrize("path", SHIPDEFS, ids=lambda p: p.name)
def test_shipdef_is_valid(path: Path):
	ship, problems = parse_ship(path.read_bytes())
	assert problems == []
	assert ship is not None


@pytest.mark.parametrize("path", SHIPDEFS, ids=lambda p: p.name)
def test_shipdef_round_trips_exactly(path: Path):
	"""Parsing then serialising a ship gives back the same file, keys in the same order."""
	raw = path.read_text(encoding="utf-8")
	ship, _ = parse_ship(raw)
	assert ship is not None
	assert json.dumps(ship.to_file_dict(), indent=4, ensure_ascii=False) + "\n" == raw


def test_template_is_valid_apart_from_placeholder_ids():
	raw = (ROOT / "jsontemplates.json").read_text(encoding="utf-8")
	real = raw.replace("YY", "42")
	ship, problems = parse_ship(real)
	assert problems == []
	assert ship is not None


def test_all_shipdefs_load_together():
	ships, problems = validate_ship_files([ship_file(p) for p in SHIPDEFS], Galaxy(1))
	assert problems == []
	assert {ship.name for ship in ships} == {
		"Aegis",
		"Dione",
		"Icebreaker",
		"Quellon",
		"Refinery 124",
		"Lucas",
		"Shepherd",
		"Endeavour",
		"Vulcan",
		"Salvador",
		"Star Alpha",
	}


def test_luxury_zone_uses_its_single_mode():
	ship, _ = parse_ship((ROOT / "shipdefs" / "dione.json").read_bytes())
	assert ship is not None
	zones = [s for s in ship.stations.values() if s.name == "Luxury Zone"]
	assert len(zones) == 2
	for zone in zones:
		assert zone.input.nonzero() == {}
		assert not zone.alert_gm_on_use
		[mode] = zone.modes
		assert mode.input.nonzero() == {"food": 1, "water": 1}
		assert mode.morale_modifier == 3


def test_reports_invalid_json():
	ship, problems = parse_ship(b"{ not json")
	assert ship is None
	assert problems[0].startswith("not valid JSON")


def test_reports_duplicate_keys():
	"""json.loads would otherwise silently drop the first of two stations with one ID."""
	raw = '{"0100": {"name": "A", "name": "B"}}'
	ship, problems = parse_ship(raw)
	assert ship is None
	assert "duplicate key" in problems[0]


def test_needs_exactly_one_ship():
	data = aegis()
	data["0200"] = data["0100"]
	assert problems_for(data) == ["must hold exactly one ship, keyed by its ship ID"]


@pytest.mark.parametrize("ship_id", ["0101", "100", "9900", "0000", "ab00"])
def test_rejects_bad_ship_ids(ship_id: str):
	data = {ship_id: aegis()["0100"]}
	assert any("ship ID" in p for p in problems_for(data))


def test_reports_missing_and_unknown_fields():
	data = aegis()
	del data["0100"]["stations"]["0124"]["riot"]
	data["0100"]["lockdown"] = False
	problems = problems_for(data)
	assert "0100.stations.0124.riot is missing" in problems
	assert "0100.lockdown is not a known field" in problems


def test_reports_wrong_types():
	data = aegis()
	data["0100"]["isolated"] = 0
	data["0100"]["resources"]["food"] = -1
	data["0100"]["coordinates"] = "1290"
	problems = problems_for(data)
	assert any(p.startswith("0100.isolated must be true or false") for p in problems)
	assert any(
		p.startswith("0100.resources.food must be a non-negative integer")
		for p in problems
	)
	assert any(
		p.startswith("0100.coordinates must be exactly 5 digits") for p in problems
	)


def test_reports_every_problem_not_just_the_first():
	data = aegis()
	data["0100"]["isolated"] = "no"
	data["0100"]["jumping"] = "no"
	assert len(problems_for(data)) == 2


def test_rejects_ids_with_the_wrong_prefix_or_range():
	data = aegis()
	stations = data["0100"]["stations"]
	stations["0210"] = stations.pop("0110")
	stations["0145"] = stations.pop("0111")
	problems = problems_for(data)
	assert any("0210 must start with the ship's prefix 01" in p for p in problems)
	assert any("0145 is outside the station range" in p for p in problems)


def test_allows_overflow_ids():
	data = aegis()
	stations = data["0100"]["stations"]
	stations["0109"] = stations.pop("0110")
	ship, problems = parse_ship(json.dumps(data))
	assert problems == []


def test_rejects_an_id_used_in_two_categories():
	data = aegis()
	data["0100"]["units"]["0101"] = data["0100"]["units"]["0170"]
	assert any("0101 is used more than once" in p for p in problems_for(data))


def test_rejects_a_set_ration_level_that_is_unavailable():
	data = json.loads((ROOT / "shipdefs" / "vulcan.json").read_text(encoding="utf-8"))
	data["0900"]["food_ration_level"] = "low"
	assert any("unavailable" in p for p in problems_for(data))


def test_rejects_on_foot_units_named_team():
	data = aegis()
	data["0100"]["units"]["0170"]["name"] = "Marine Team"
	assert any('must not contain "team"' in p for p in problems_for(data))


def test_rejects_players_in_a_ship_file():
	data = aegis()
	data["0100"]["players"] = {"0001": {}}
	assert any("players are created at /startgame" in p for p in problems_for(data))


def test_rejects_non_json_filenames():
	_, problems = validate_ship_files([ShipFile("aegis.txt", b"{}")], Galaxy(1))
	assert problems == ["aegis.txt: not a .json file"]


def test_rejects_clashes_within_a_batch():
	path = ROOT / "shipdefs" / "aegis.json"
	ships, problems = validate_ship_files([ship_file(path), ship_file(path)], Galaxy(1))
	assert ships == []
	assert "aegis.json: ship ID 0100 is already loaded" in problems


def test_rejects_clashes_with_loaded_ships_and_loads_nothing():
	galaxy = Galaxy(1)
	[aegis_ship], _ = validate_ship_files(
		[ship_file(ROOT / "shipdefs" / "aegis.json")], galaxy
	)
	galaxy.add_ship(aegis_ship)

	renamed = aegis()
	renamed["0100"]["name"] = "Not The Aegis"
	files = [
		ship_file(ROOT / "shipdefs" / "dione.json"),
		ShipFile("copy.json", json.dumps(renamed).encode()),
	]
	ships, problems = validate_ship_files(files, galaxy)
	assert ships == []
	assert "copy.json: ship ID 0100 is already loaded" in problems
	assert 'copy.json: a shuttle named "cyclops" already exists' in problems


def test_unit_ids_held_by_a_destroyed_unit_still_clash():
	galaxy = Galaxy(1)
	galaxy.used_unit_ids.add("0170")
	ships, problems = validate_ship_files(
		[ship_file(ROOT / "shipdefs" / "aegis.json")], galaxy
	)
	assert ships == []
	assert "aegis.json: ID 0170 clashes with an ID already loaded" in problems


def test_add_ship_sets_council_and_brig_ships():
	galaxy = Galaxy(1)
	ships, _ = validate_ship_files([ship_file(p) for p in SHIPDEFS], galaxy)
	for ship in ships:
		galaxy.add_ship(ship)
	assert galaxy.council_chambers_ship == "0200"
	assert galaxy.brig_ship == "0100"
	assert set(galaxy.coordinates) == {"12903"}
	assert galaxy.coordinates["12903"].distance == "short"


def test_remove_ship_frees_its_ids_for_a_reload():
	galaxy = Galaxy(1)
	path = ROOT / "shipdefs" / "aegis.json"
	[ship], _ = validate_ship_files([ship_file(path)], galaxy)
	galaxy.add_ship(ship)
	galaxy.remove_ship("0100")
	assert galaxy.brig_ship is None
	assert galaxy.coordinates == {}
	ships, problems = validate_ship_files([ship_file(path)], galaxy)
	assert problems == []


def test_parsed_ship_is_independent_of_its_source():
	data = aegis()
	ship, _ = parse_ship(json.dumps(data))
	original = copy.deepcopy(data)
	assert ship is not None
	ship.stations["0110"].damaged = True
	assert data == original

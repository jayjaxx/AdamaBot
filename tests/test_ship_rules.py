"""Resources, stations and the turn loop, with scripted dice."""

from pathlib import Path

import pytest

from adama.models import Galaxy, Player
from adama.models.galaxy import STATE_IN_PROGRESS
from adama.rules import BRIDGE, CONTROL, PDA, GameError
from adama.rules import resources as res
from adama.rules import stations as st
from adama.rules import turns
from adama.shipfiles import ShipFile, validate_ship_files

ROOT = Path(__file__).parent.parent


class Dice:
	"""Rolls the scripted values in order; `choice` picks the first candidate."""

	def __init__(self, *rolls: int):
		self.rolls = list(rolls)

	def randint(self, low: int, high: int) -> int:
		roll = self.rolls.pop(0)
		assert low <= roll <= high
		return roll

	def choice(self, seq):
		return seq[0]


def galaxy() -> Galaxy:
	g = Galaxy(1, state=STATE_IN_PROGRESS, turn_number=1)
	files = [
		ShipFile(p.name, p.read_bytes())
		for p in sorted((ROOT / "shipdefs").glob("*.json"))
	]
	ships, _ = validate_ship_files(files, g)
	for ship in ships:
		g.add_ship(ship)
	return g


# Crew


def test_auto_crew_prefers_untrained_and_own_ship():
	g = galaxy()
	aegis = g.ships["0100"]
	crew = st.pick_crew(aegis, "team", 4, None)
	assert all(u.name == "Work Team" for u in crew)
	crew = st.pick_crew(aegis, "team", 5, None)
	assert crew[-1].name != "Work Team"  # trained crew only once the Work Teams run out
	aegis.units["0170"].affiliation = "0200"
	assert "0170" not in [u.id for u in st.pick_crew(aegis, "team", 3, None)]


def test_designated_crew_is_checked():
	aegis = galaxy().ships["0100"]
	with pytest.raises(GameError, match="needs exactly 1"):
		st.pick_crew(aegis, "medic", 1, ["0170", "0171"])
	with pytest.raises(GameError, match='needs "medic"'):
		st.pick_crew(aegis, "medic", 1, ["0170"])
	aegis.units["0170"].exhausted = True
	with pytest.raises(GameError, match="exhausted"):
		st.pick_crew(aegis, "team", 1, ["0170"])
	with pytest.raises(GameError, match="isn't aboard"):
		st.pick_crew(aegis, "team", 1, ["0270"])


def test_trained_crew_can_do_untrained_work():
	aegis = galaxy().ships["0100"]
	[engineer] = [u for u in aegis.units.values() if u.name == "Engineering Team"]
	assert st.pick_crew(aegis, "team", 1, [engineer.id]) == [engineer]


# Activate


def test_activate_spends_and_gains():
	g = galaxy()
	aegis = g.ships["0100"]
	water = aegis.resources.water
	notices = st.activate(
		aegis, "0132", None, None, "@me"
	)  # Food Synthesiser 3.0: 3 water -> 6 food
	assert aegis.resources.water == water - 3
	assert aegis.resources.food == 8 + 6
	station = aegis.stations["0132"]
	assert station.exhausted and len(station.units_used_this_turn) == 1
	assert aegis.units[station.units_used_this_turn[0]].exhausted
	assert notices[0].target == BRIDGE
	with pytest.raises(GameError, match="already been used"):
		st.activate(aegis, "0132", None, None, "@me")


def test_activate_restrictions():
	aegis = galaxy().ships["0100"]
	with pytest.raises(GameError, match="can't be activated"):
		st.activate(aegis, "0110", None, None, "@me")  # Hull Plating
	aegis.stations["0129"].damaged = True
	with pytest.raises(GameError, match="damaged"):
		st.activate(aegis, "0129", None, None, "@me")
	aegis.stations["0130"].riot = True
	with pytest.raises(GameError, match="riot"):
		st.activate(aegis, "0130", None, None, "@me")
	aegis.resources.water = 2
	with pytest.raises(GameError, match="Not enough water"):
		st.activate(aegis, "0132", None, None, "@me")
	with pytest.raises(GameError, match="no station `0299`"):
		st.activate(aegis, "0299", None, None, "@me")


def test_reusable_station_can_be_used_again():
	aegis = galaxy().ships["0100"]
	st.activate(aegis, "0128", None, None, "@me")  # Hangar Bay
	st.activate(aegis, "0128", None, None, "@me")
	assert not aegis.stations["0128"].exhausted


def test_modes_by_name_number_and_morale():
	g = galaxy()
	refinery = g.ships["0500"]
	with pytest.raises(GameError, match="Choose a mode"):
		st.activate(refinery, "0518", None, None, "@me")
	st.activate(refinery, "0518", "12 fuel", None, "@me")
	assert refinery.resources.ore == 6 - 3 and refinery.resources.fuel == 12 + 12
	assert refinery.morale_roll_modifiers.temp == -1
	refinery.resources.ore = 4
	st.activate(refinery, "0519", "3", None, "@me")
	assert refinery.morale_roll_modifiers.temp == -3  # modes stack
	with pytest.raises(GameError, match='no mode "9 fuel"'):
		st.activate(refinery, "0520", "9 fuel", None, "@me")


def test_single_mode_is_automatic_and_alerts():
	dione = galaxy().ships["0200"]
	notices = st.activate(dione, "0215", None, None, "@me")  # Luxury Zone
	assert dione.morale_roll_modifiers.temp == 3
	assert dione.resources.food == 1 and dione.resources.water == 1
	assert not any(n.target == CONTROL for n in notices)
	endeavour = galaxy().ships["0800"]
	notices = st.activate(
		endeavour, "0812", None, None, "@me"
	)  # Laboratory alerts control
	assert any(n.target == CONTROL for n in notices)


# Repair, damage, riots


def test_repair():
	aegis = galaxy().ships["0100"]
	with pytest.raises(GameError, match="isn't damaged"):
		st.repair(aegis, "0129", None, "@me")
	aegis.stations["0129"].damaged = True
	st.repair(aegis, "0129", None, "@me")
	assert not aegis.stations["0129"].damaged and aegis.stations["0129"].exhausted
	assert aegis.resources.materials == 6 - 3


def test_damage_hurts_crew_who_worked_it_and_counts_for_the_aegis():
	g = galaxy()
	aegis = g.ships["0100"]
	st.activate(aegis, "0129", None, None, "@me")
	[worker] = aegis.stations["0129"].units_used_this_turn
	st.damage(g, "0129", Dice())
	assert aegis.stations["0129"].damaged
	assert aegis.units[worker].damaged
	assert aegis.damage_taken_this_turn == 1
	with pytest.raises(GameError, match="already damaged"):
		st.damage(g, "0129", Dice())


def test_damage_a_random_station_including_unusable_ones():
	g = galaxy()
	done, _ = st.damage(g, "Aegis", Dice())
	assert g.ships["0100"].stations["0110"].damaged
	assert (
		done == "Damaged `0110` Hull Plating on the Aegis."
	)  # Hull Plating, first candidate
	for station in g.ships["0800"].stations.values():
		station.damaged = True
	_, notices = st.damage(g, "Endeavour", Dice())
	assert notices[0].target == CONTROL


def test_disperse():
	g = galaxy()
	aegis = g.ships["0100"]
	aegis.stations["0129"].riot = True
	with pytest.raises(GameError, match="on-foot"):
		st.disperse(aegis, "0129", "0170", Dice(5), "@me")
	marine = next(u for u in aegis.units.values() if u.name == "Marine Forces")
	st.disperse(aegis, "0129", marine.id, Dice(2), "@me")
	assert aegis.stations["0129"].riot and marine.exhausted
	marine.exhausted = False
	st.disperse(aegis, "0129", marine.id, Dice(1), "@me")
	assert marine.damaged
	marine.exhausted = False
	st.disperse(aegis, "0129", marine.id, Dice(1), "@me")
	assert marine.id not in aegis.units  # destroyed
	other = next(u for u in aegis.units.values() if u.name == "Marine Forces")
	st.disperse(aegis, "0129", other.id, Dice(4), "@me")
	assert not aegis.stations["0129"].riot


def test_riots_in_cells_alert_control():
	g = galaxy()
	notices = st.start_riot(g, "0910", Dice())
	assert g.ships["0900"].stations["0910"].riot
	assert any(n.target == CONTROL for n in notices)


# Resources


def test_load_and_unload():
	aegis = galaxy().ships["0100"]
	with pytest.raises(GameError, match="must specify"):
		res.load(aegis, None, 1, "food", "@me")
	notices = res.load(aegis, "cyclops", 3, "Food", "@me")
	assert aegis.resources.food == 5 and aegis.shuttles["0101"].resources.food == 3
	bridge = [n for n in notices if n.target == BRIDGE]
	assert len(bridge) == 1 and "the Aegis now has **5** food" in bridge[0].text
	with pytest.raises(GameError, match="Not enough food"):
		res.unload(aegis, "Cyclops", 4, "food", "@me")
	res.unload(aegis, "Cyclops", 3, "food", "@me")
	assert aegis.resources.food == 8
	with pytest.raises(GameError, match="isn't a resource"):
		res.load(aegis, "Cyclops", 1, "gold", "@me")


def test_add_and_teleport_resources():
	g = galaxy()
	_, notices = res.add_resource(g, -100, "food", "Aegis")
	assert g.ships["0100"].resources.food == 0
	assert notices[0].target == BRIDGE and notices[0].id == "0100"
	assert "now has **0** food" in notices[0].text
	_, notices = res.add_resource(g, -1, "food", "Aegis")
	assert notices == []
	_, notices = res.add_resource(g, 5, "ore", "Phoenix")
	assert g.ships["0600"].shuttles["0601"].resources.ore == 5
	assert notices[0].id == "0600" and "Phoenix now has **5** ore" in notices[0].text
	_, notices = res.teleport_resource(g, 2, "ore", "Phoenix", "Dione")
	assert g.ships["0200"].resources.ore == 2
	assert {n.id for n in notices} == {"0600", "0200"}
	assert all(n.target == BRIDGE for n in notices)
	assert "the Dione now has **2** ore" in notices[1].text


# Rations and the morale roll


def test_ration_levels():
	g = galaxy()
	turns.set_ration_level(g.ships["0100"], "food", "luxurious")
	assert g.ships["0100"].food_ration_level == "luxurious"
	with pytest.raises(GameError, match="aren't available"):
		turns.set_ration_level(g.ships["0900"], "water", "low")


def test_rations_fall_to_the_highest_affordable_level():
	ship = galaxy().ships[
		"0800"
	]  # Endeavour: normal 2, low/minimum unavailable, critical 1
	ship.resources.food = 1
	assert turns.eat(ship, "food") == ("critical", 1, -4)
	assert turns.eat(ship, "food") == ("none", 0, -6)


@pytest.mark.parametrize(
	"total, change",
	[(13, 2), (7, 1), (12, 1), (6, 0), (1, 0), (0, -1), (-3, -1), (-4, -2)],
)
def test_morale_bands(total, change):
	assert turns.morale_change(total) == change


def test_morale_roll_breakdown():
	g = galaxy()
	aegis = g.ships["0100"]  # 8 food / 6 water; normal rations cost 10 food / 8 water
	aegis.damage_taken_this_turn = 7  # -2
	aegis.morale_roll_modifiers.temp = 1
	g.fleet_perm_morale_roll_modifier = -1
	result, notices = turns.morale_roll(g, aegis, Dice(6))
	assert result.rations == {"food": ("low", 8), "water": ("low", 5)}
	# 6 roll - 1 food - 1 water - 2 damage + 1 temp - 1 fleet = 2 -> no change
	assert result.total == 2 and result.change == 0 and aegis.morale_level == 10
	assert aegis.resources.food == 0 and aegis.resources.water == 1
	assert aegis.morale_rolled_this_turn
	assert "turn 1" in g.morale_results["0100"]
	assert notices[0].target == BRIDGE and notices[-1].target == CONTROL
	with pytest.raises(GameError, match="already made"):
		turns.morale_roll(g, aegis, Dice(6))
	_, notices = turns.morale_roll(g, aegis, Dice(6), force=True)
	assert any(n.target == CONTROL and "forced again" in n.text for n in notices)
	assert any("(forced by control)" in n.text for n in notices if n.target == BRIDGE)


def test_low_morale_rolls_for_riots_and_zero_alerts():
	g = galaxy()
	ship = g.ships["0800"]
	ship.morale_level = 3
	turns.morale_roll(
		g, ship, Dice(6, 2)
	)  # total 6-6-6 = -6 -> -2 => 1, riot roll 2 <= 3
	assert ship.morale_level == 1
	assert any(s.riot for s in ship.stations.values())

	g = galaxy()
	ship = g.ships["0800"]
	ship.morale_level = 1
	_, notices = turns.morale_roll(g, ship, Dice(1))  # one roll only: no riot roll at 0
	assert ship.morale_level == 0
	assert any(n.target == CONTROL and "0" in n.text for n in notices)


def test_uncontested_riots_spread():
	g = galaxy()
	ship = g.ships["0100"]
	ship.stations["0129"].riot = True
	ship.stations["0130"].riot = True
	ship.stations["0130"].exhausted = True  # a dispersal was tried: doesn't spread
	turns.morale_roll(g, ship, Dice(3, 6))  # 0129 spreads on 3, then the morale roll
	assert ship.stations["0110"].riot


# End of turn


def test_end_turn_refuses_once_then_forces():
	g = galaxy()
	first = turns.end_turn(g)
	assert not first.ended and g.turn_number == 1
	assert first.notices[0].target == CONTROL
	second = turns.end_turn(g)
	assert second.ended and g.turn_number == 2
	assert any("forced" in n.text for n in second.notices)
	assert not g.end_turn_warned


def test_end_turn_resets_the_turn():
	g = galaxy()
	for ship in g.ships.values():
		ship.morale_rolled_this_turn = True
	aegis = g.ships["0100"]
	st.activate(aegis, "0129", None, None, "@me")
	aegis.morale_roll_modifiers.temp, aegis.morale_roll_modifiers.perm = 3, 2
	aegis.damage_taken_this_turn = 4
	g.fleet_temp_morale_roll_modifier = 5
	g.council_sealed = True
	aegis.action_cards["0144"].exhausted = True  # once per turn
	aegis.action_cards["0141"].exhausted = True  # not once per turn
	wounded = Player(id="0001", discord_user_id=1, location="0100", wounded=True)
	elsewhere = Player(id="0002", discord_user_id=2, location="0300", wounded=True)
	g.players = {"0001": wounded, "0002": elsewhere}

	result = turns.end_turn(g)
	assert result.ended and result.unsealed and not g.council_sealed
	assert (
		not aegis.stations["0129"].exhausted
		and not aegis.stations["0129"].units_used_this_turn
	)
	assert not any(u.exhausted for u in g.iter_units())
	assert (
		aegis.morale_roll_modifiers.temp == 0 and aegis.morale_roll_modifiers.perm == 2
	)
	assert aegis.damage_taken_this_turn == 0 and g.fleet_temp_morale_roll_modifier == 0
	assert (
		not aegis.action_cards["0144"].exhausted
		and aegis.action_cards["0141"].exhausted
	)
	assert not any(s.morale_rolled_this_turn for s in g.ships.values())
	# Healed only aboard a ship with an undamaged Sickbay (the Icebreaker has none).
	assert result.healed == [wounded] and elsewhere.wounded
	assert any(n.target == PDA and n.id == "0001" for n in result.notices)


def test_add_morale_and_modifiers():
	g = galaxy()
	turns.add_morale(g, "Aegis", 5)
	assert g.ships["0100"].morale_level == 10
	notices = turns.add_morale(g, "fleet", -20)
	assert all(s.morale_level == 0 for s in g.ships.values())
	assert sum(1 for n in notices if n.target == CONTROL) == 11
	turns.add_modifier(g, "Dione", 2, "perm")
	turns.add_modifier(g, None, -1, "temp")
	assert g.ships["0200"].morale_roll_modifiers.perm == 2
	assert g.fleet_temp_morale_roll_modifier == -1

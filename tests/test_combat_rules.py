"""Lockdowns, observers, first responders, units on shuttles and control's editing."""

import pytest
from test_ship_rules import galaxy
from test_travel_rules import aboard

from adama.access import READ_ONLY, VOICE, player_access
from adama.models.player import LOCATION_COMBAT
from adama.rules import BRIDGE, CONTROL, COUNCIL, HANGAR, PDA, GameError
from adama.rules import combat
from adama.rules import editing
from adama.rules import jumps
from adama.rules import players as pl
from adama.rules import shuttles as sh

AEGIS = "0100"


def test_lockdown_stops_travel_and_summons_responders():
	g = galaxy()
	aegis = g.ships[AEGIS]
	commander = aboard(g, AEGIS)
	commander.first_responder = True
	other = aboard(g, "0200", "0003")  # a first responder aboard the Dione
	other.first_responder = True
	pilot = aboard(g, AEGIS, "0002")
	sh.launch(g, pilot, aegis, "Cyclops", "Dione")
	coords, change = combat.lockdown(g, "12903", aegis)
	assert coords.locked_down and coords.commanding_ship == AEGIS and not g.launches
	pings = {n.id: n for n in change.notices if n.target == PDA}
	assert set(pings) == {"0001", "0003"} and all(n.ping for n in pings.values())
	assert all("/travel combat" in n.text for n in pings.values())
	assert "already see" in pings["0001"].text and "already" not in pings["0003"].text
	at_aegis = {n.id for n in change.notices if n.target in (HANGAR, BRIDGE)}
	assert {n.target for n in change.notices if n.id == AEGIS} >= {HANGAR, BRIDGE}
	assert at_aegis == {s.id for s in g.ships_at("12903")}
	assert any(n.target == COUNCIL for n in change.notices)  # chambers on the Dione
	assert commander in change.resync and other not in change.resync
	with pytest.raises(GameError, match="lockdown"):
		pl.travel(g, pilot, "Dione")
	with pytest.raises(GameError, match="already in lockdown"):
		combat.lockdown(g, "12903", aegis)
	with pytest.raises(GameError, match="No ships"):
		combat.lockdown(g, "00000", None)
	elsewhere = editing.conjure_ship(g, "Pegasus", "44444")
	with pytest.raises(GameError, match="name the ship commanding"):
		combat.lockdown(g, "44444", None)
	_, change = combat.lockdown(g, "44444", aegis)  # may command from elsewhere
	assert not any(n.target == COUNCIL for n in change.notices)
	assert {n.id for n in change.notices if n.target == HANGAR} == {elsewhere.id}

	coords.combat_text_channel_id, coords.combat_voice_channel_id = 501, 502
	# Commanding ship's crew see the combat from aboard; others must travel.
	assert player_access(g, commander)[502] == VOICE
	assert 502 not in player_access(g, other)
	pl.travel(g, other, "combat")
	assert other.location == LOCATION_COMBAT and player_access(g, other)[502] == VOICE


def test_ending_lockdowns_returns_combat_players():
	g = galaxy()
	responder = aboard(g, "0200")
	responder.first_responder = True
	watcher = aboard(g, "0200", "0002")
	coords, _ = combat.lockdown(g, "12903", g.ships[AEGIS])
	coords.combat_text_channel_id, coords.combat_voice_channel_id = 501, 502
	combat.set_observer(g, None, watcher, True)
	assert player_access(g, watcher)[501] == READ_ONLY
	pl.travel(g, responder, "combat")

	change = combat.end_lockdown(g, None)
	assert any(n.target == COUNCIL for n in change.notices)
	assert not coords.locked_down and change.deleted_channels == [501, 502]
	assert coords.commanding_ship is None
	assert responder.location == "0200" and responder in change.resync
	assert responder.combat_coordinates is None
	assert watcher in change.resync and 501 not in player_access(g, watcher)
	with pytest.raises(GameError, match="No lockdown"):
		combat.end_lockdown(g, None)
	with pytest.raises(GameError, match="isn't in lockdown"):
		combat.end_lockdown(g, "12903")


def test_jumping_away_leaves_first_responders_and_alerts_control():
	g = galaxy()
	aegis, dione = g.ships[AEGIS], g.ships["0200"]
	commander = aboard(g, AEGIS)
	commander.first_responder = True
	fighter = aboard(g, "0200", "0002")
	fighter.first_responder = True
	coords, _ = combat.lockdown(g, "12903", aegis)
	coords.combat_text_channel_id, coords.combat_voice_channel_id = 501, 502
	pl.travel(g, fighter, "combat")

	moved = jumps.teleport_ship(g, dione, "55555")
	alert = next(n for n in moved.notices if n.target == CONTROL)
	assert alert.claim and "<@2>" in alert.text and "commanding" not in alert.text
	assert fighter.location == LOCATION_COMBAT  # still fighting at 12903
	assert player_access(g, fighter)[502] == VOICE
	with pytest.raises(GameError, match="can't leave until it ends"):
		pl.travel(g, fighter, "Aegis")

	crew = aboard(g, AEGIS, "0004")  # ordinary crew jump away with the ship
	moved = jumps.teleport_ship(g, aegis, "66666")
	alert = next(n for n in moved.notices if n.target == CONTROL)
	assert "left behind in combat (<@1>)" in alert.text
	assert "/transfercommand" in alert.text
	assert commander in moved.resync and crew not in moved.resync
	assert commander.location == LOCATION_COMBAT and crew.location == AEGIS
	assert commander.combat_coordinates == "12903"
	assert player_access(g, commander)[502] == VOICE

	lucas = g.ships_at("12903")[0]
	text, change = combat.transfer_command(g, "12903", lucas)
	assert coords.commanding_ship == lucas.id

	# Both the Dione and the Aegis left: the Aegis lost command, but the Dione's
	# fighter now goes to the commanding Lucas.
	change = combat.end_lockdown(g, "12903")
	assert fighter.location == lucas.id
	# The commander's own ship (the Aegis) left; the Lucas commands and is still
	# here, so they go there too.
	assert commander.location == lucas.id
	alert = next(n for n in change.notices if n.target == CONTROL and n.claim)
	assert "now aboard the" in alert.text and "Aegis Crew" in alert.text
	with pytest.raises(GameError, match="isn't in lockdown"):
		combat.transfer_command(g, "12903", lucas)


def test_nobody_to_take_them_leaves_players_for_control():
	g = galaxy()
	aegis = g.ships[AEGIS]
	commander = aboard(g, AEGIS)
	commander.first_responder = True
	coords, _ = combat.lockdown(g, "12903", aegis)
	jumps.teleport_ship(g, aegis, "66666")  # leaves the commander behind
	change = combat.end_lockdown(g, "12903")
	assert commander.location == ""  # for control to place
	alert = next(n for n in change.notices if n.target == CONTROL and n.claim)
	assert "/teleportplayer" in alert.text and "<@1>" in alert.text


def test_lockdown_readiness_report():
	g = galaxy()
	ready = aboard(g, "0200")
	ready.first_responder = True
	hurt = aboard(g, "0200", "0002")
	hurt.first_responder = hurt.wounded = True
	away = aboard(g, "0200", "0003")
	away.first_responder = True
	g.ships["0200"].coordinates = "12903"
	g.ships["0300"].coordinates = "55555"
	away.location = "0300"
	_, change = combat.lockdown(g, "12903", g.ships[AEGIS])
	report = next(n for n in change.notices if "can't /travel combat" in n.text)
	assert report.claim and "<@1>" not in report.text
	assert "<@2> (wounded)" in report.text and "<@3> (aboard the" in report.text
	assert "Ships with no first responder in their crew:" in report.text
	assert "Aegis" in report.text and "Dione," not in report.text


def test_destroying_the_commanding_ship_alerts_control():
	g = galaxy()
	combat.lockdown(g, "12903", g.ships[AEGIS])
	notices = editing.destroy_ship(g, g.ships[AEGIS])
	assert any(n.target == CONTROL and "/transfercommand" in n.text for n in notices)
	editing.conjure_ship(g, "Pegasus", "44444")
	with pytest.raises(GameError, match="destroyed"):
		combat.lockdown(g, "44444", g.ships[AEGIS])


def test_combat_players_whose_ship_left_land_on_the_commanding_ship():
	g = galaxy()
	aegis, dione = g.ships[AEGIS], g.ships["0200"]
	fighter = aboard(g, "0200", "0002")
	fighter.first_responder = True
	stayer = aboard(g, AEGIS, "0003")
	stayer.first_responder = True
	combat.lockdown(g, "12903", aegis)
	pl.travel(g, fighter, "combat")
	pl.travel(g, stayer, "combat")
	jumps.teleport_ship(g, dione, "55555")

	change = combat.end_lockdown(g, "12903")
	assert fighter.location == AEGIS and stayer.location == AEGIS
	alert = next(n for n in change.notices if n.target == CONTROL and n.claim)
	assert "<@2>" in alert.text and "Dione Crew" in alert.text
	assert "<@3>" not in alert.text  # their ship never left


def test_observers_need_an_aegis_first_responder():
	g = galaxy()
	target = aboard(g, "0200", "0002")
	outsider = aboard(g, "0200", "0003")
	outsider.first_responder = True
	with pytest.raises(GameError, match="crew of the Aegis"):
		combat.set_observer(g, outsider, target, True)
	responder = aboard(g, AEGIS)
	responder.first_responder = True
	combat.set_observer(g, responder, target, True)
	assert target.battlemap_observer
	with pytest.raises(GameError, match="already an observer"):
		combat.set_observer(g, responder, target, True)
	combat.set_observer(g, responder, target, False)
	assert not target.battlemap_observer


def test_first_responder_flag():
	g = galaxy()
	p = aboard(g, AEGIS)
	combat.set_first_responder(p, True)
	assert p.first_responder
	with pytest.raises(GameError, match="already"):
		combat.set_first_responder(p, True)
	combat.set_first_responder(p, False)
	with pytest.raises(GameError, match="isn't"):
		combat.set_first_responder(p, False)


def test_loading_units_onto_shuttles():
	g = galaxy()
	aegis = g.ships[AEGIS]
	crew = aboard(g, AEGIS)
	visitor = aboard(g, AEGIS, "0002", crew=False)
	unit_id = next(iter(aegis.units))
	with pytest.raises(GameError, match="Only crew of the Aegis"):
		combat.load_unit(g, visitor, aegis, unit_id, "Cyclops")
	combat.load_unit(g, crew, aegis, unit_id, "Cyclops")
	assert unit_id in aegis.shuttles["0101"].units
	combat.unload_unit(g, None, aegis, unit_id)  # control
	assert unit_id in aegis.units
	aegis.units[unit_id].exhausted = True
	with pytest.raises(GameError, match="exhausted"):
		combat.load_unit(g, crew, aegis, unit_id, "Cyclops")


def test_unit_editing():
	g = galaxy()
	aegis = g.ships[AEGIS]
	unit = editing.conjure_unit(g, "Marine Squad", aegis)
	assert unit.id == "9970" and unit.id in g.used_unit_ids
	editing.damage_unit(g, unit.id)
	assert unit.damaged
	editing.heal_unit(g, unit.id)
	with pytest.raises(GameError, match="isn't damaged"):
		editing.heal_unit(g, unit.id)
	editing.defect_unit(g, unit.id, g.ships["0200"])
	editing.rename_unit(g, unit.id, "Pilot Squad")
	editing.set_unit_exhausted(g, unit.id, True)
	assert unit.affiliation == "0200" and unit.name == "Pilot Squad" and unit.exhausted
	editing.damage_unit(g, unit.id)
	text, _ = editing.damage_unit(g, unit.id)
	assert "destroyed" in text
	with pytest.raises(GameError, match="no unit"):
		editing.find_unit(g, unit.id)


def test_station_editing():
	g = galaxy()
	station = editing.conjure_station(g, g.ships[AEGIS], "Laser Grid")
	sid = station.id
	editing.set_io(g, sid, "in", 2, "ore")
	editing.set_io(g, sid, "in", 1, "Pilot")
	editing.set_io(g, sid, "out", 3, "fuel")
	assert station.input.ore == 2 and station.output.fuel == 3
	assert (station.input.crew_keyword, station.input.crew_amount) == ("pilot", 1)
	with pytest.raises(GameError, match="Outputs can't be crew"):
		editing.set_io(g, sid, "out", 1, "team")
	with pytest.raises(GameError, match="negative"):
		editing.set_io(g, sid, "in", -1, "ore")
	editing.set_repair_cost(g, sid, 2, "materials")
	editing.set_call_gm(g, sid, "any")
	editing.set_flag(g, sid, "reusable", True)
	assert station.repair_cost.materials == 2 and station.reusable
	assert station.alert_gm_on_use and station.alert_gm_on_damage
	editing.set_mode(g, sid, "Burst", "out", 5, "fuel")
	editing.set_mode(g, sid, "burst", "morale", -1, None)
	assert station.modes[0].output.fuel == 5 and station.modes[0].morale_modifier == -1
	editing.set_mode(g, sid, "Burst", "remove", None, None)
	assert not station.modes
	editing.destroy_station(g, sid)
	assert sid not in g.ships[AEGIS].stations


def test_ship_editing():
	g = galaxy()
	ship = editing.conjure_ship(g, "Pegasus", "44444")
	assert ship.id in g.ships and "44444" in g.coordinates
	with pytest.raises(GameError, match="already exists"):
		editing.conjure_ship(g, "pegasus", "44444")
	editing.set_ration_consumption(ship, "low", None, "food")
	editing.set_fuel_consumption(ship, "long", 7)
	assert ship.ration_levels["low"].food is None and ship.fuel_consumption.long == 7

	aegis = g.ships[AEGIS]
	jumps.set_coords(aegis, "55555")
	aegis.resources.fuel = 99
	aegis.stations["0126"].exhausted = True  # the Jump Drive has been activated
	jumps.start_jump(g, aegis)
	notices = editing.destroy_ship(g, aegis)
	assert aegis.destroyed and not aegis.jumping and not g.jumps
	assert any(n.id == AEGIS for n in notices)
	with pytest.raises(GameError, match="already destroyed"):
		editing.destroy_ship(g, aegis)


def test_start_of_turn_station_during_a_lockdown_is_late():
	from adama.rules import CONTROL
	from adama.rules import stations as st
	from adama.rules import turns
	from adama.status import fleet_status

	g = galaxy()
	aegis = g.ships[AEGIS]
	drive = aegis.stations["0126"]  # Jump Drive, a start-of-turn station
	assert drive.startofturn
	notices = st.activate(aegis, drive.id, None, None, "@me", late=g.late_activations)
	assert g.late_activations == {"0126"}
	assert any(n.target == CONTROL and n.claim for n in notices)
	assert "`0126` Jump Drive ⏳" in fleet_status(g)
	turns.end_turn(g)
	turns.end_turn(g)  # the first is refused: no morale rolls
	assert not g.late_activations
	assert "`0126` Jump Drive ❌" in fleet_status(g)
	notices = st.activate(aegis, drive.id, None, None, "@me")
	assert not any(n.target == CONTROL for n in notices)
	assert "`0126` Jump Drive ✅" in fleet_status(g)

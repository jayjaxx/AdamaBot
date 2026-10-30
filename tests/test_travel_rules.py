"""Shuttles, jumps and isolation."""

import pytest
from test_ship_rules import galaxy

from adama.models import Player
from adama.models.player import LOCATION_COMBAT
from adama.rules import BRIDGE, CONTROL, HANGAR, PDA, GameError
from adama.rules import jumps
from adama.rules import players as pl
from adama.rules import shuttles as sh


def aboard(g, ship_id: str, player_id: str = "0001", crew: bool = True) -> Player:
	player = Player(
		id=player_id,
		discord_user_id=int(player_id),
		location=ship_id,
		crew_affiliations=[ship_id] if crew else [],
	)
	g.players[player.id] = player
	return player


# Launching


def test_launch_moves_shuttle_cargo_and_pilot():
	g = galaxy()
	aegis, dione = g.ships["0100"], g.ships["0200"]
	pilot = aboard(g, "0100")
	pilot.clearances.append("0201")
	cyclops = aegis.shuttles["0101"]
	cyclops.resources.food = 3
	started, notices = sh.launch(g, pilot, aegis, "cyclops", "Dione")
	assert cyclops.launching and notices[0].target == HANGAR
	with pytest.raises(GameError, match="already launching a shuttle"):
		sh.launch(g, pilot, aegis, "Blackbird", "Dione")
	with pytest.raises(GameError, match="launching a shuttle"):
		pl.travel(g, pilot, "Dione")
	notices = sh.complete_launch(g, started)
	assert "0101" in dione.shuttles and "0101" not in aegis.shuttles
	assert dione.shuttles["0101"].resources.food == 3
	assert pilot.location == "0200" and not pilot.clearances
	assert not cyclops.launching and not g.launches
	assert {n.id for n in notices} == {"0100", "0200"}
	assert sh.complete_launch(g, started) == []  # finishing twice does nothing


def test_launch_needs_the_shuttle_named_when_several_are_docked():
	g = galaxy()
	with pytest.raises(GameError, match="must specify"):
		sh.launch(g, aboard(g, "0100"), g.ships["0100"], None, "Dione")


def test_launch_restrictions():
	g = galaxy()
	aegis = g.ships["0100"]
	pilot = aboard(g, "0100")
	pilot.wounded = True
	with pytest.raises(GameError, match="wounded"):
		sh.launch(g, pilot, aegis, "Cyclops", "Dione")
	pilot.wounded = False
	g.ships["0200"].isolated = True
	with pytest.raises(GameError, match="isolated by control.*unisolated shortly"):
		sh.launch(g, pilot, aegis, "Cyclops", "Dione")
	g.ships["0200"].isolated = False
	aegis.jumping = True
	with pytest.raises(GameError, match="preparing to jump"):
		sh.launch(g, pilot, aegis, "Cyclops", "Dione")
	aegis.jumping = False
	g.ships["0200"].coordinates = "55555"
	with pytest.raises(GameError, match="isn't at these coordinates"):
		sh.launch(g, pilot, aegis, "Cyclops", "Dione")


def test_launch_cancelled_if_things_change_during_countdown():
	g = galaxy()
	pilot = aboard(g, "0100")
	started, _ = sh.launch(g, pilot, g.ships["0100"], "Cyclops", "Dione")
	g.ships["0200"].destroyed = True
	notices = sh.complete_launch(g, started)
	assert "aborted" in notices[0].text
	assert "0101" in g.ships["0100"].shuttles and pilot.location == "0100"


def test_intercept_and_clearance():
	g = galaxy()
	aegis = g.ships["0100"]
	pilot, guard = aboard(g, "0100"), aboard(g, "0100", "0002")
	started, _ = sh.launch(g, pilot, aegis, "Cyclops", "Dione")
	sh.intercept(g, guard, aegis, "Cyclops", "@guard")
	assert not g.launches and aegis.shuttles["0101"].intercepted
	assert sh.complete_launch(g, started) == []
	with pytest.raises(GameError, match="intercepted"):
		sh.launch(g, pilot, aegis, "Cyclops", "Dione")

	# A player with clearance may launch an intercepted shuttle, and can't be stopped.
	notices = sh.clearance(aegis, "Cyclops", pilot)
	assert notices[0].target == PDA
	sh.launch(g, pilot, aegis, "Cyclops", "Dione")
	with pytest.raises(GameError, match="has clearance"):
		sh.intercept(g, guard, aegis, "Cyclops", "@guard")

	# Clearing the shuttle itself un-intercepts it; a launch clears the flag again.
	blackbird = aegis.shuttles["0102"]
	blackbird.intercepted = True
	sh.clearance(aegis, "Blackbird", None)
	assert blackbird.clearance and not blackbird.intercepted
	with pytest.raises(GameError, match="has clearance"):
		sh.intercept(g, guard, aegis, "Blackbird", "@guard")
	sh.revoke_shuttle(g, aegis, "Blackbird")
	assert not blackbird.clearance
	sh.revoke_player(g, pilot)
	assert not pilot.clearances


def test_teleport_player_cancels_their_launch():
	g = galaxy()
	pilot = aboard(g, "0100")
	sh.launch(g, pilot, g.ships["0100"], "Cyclops", "Dione")
	notices = pl.teleport(g, pilot, "Lucas")
	assert not g.launches and not g.ships["0100"].shuttles["0101"].launching
	assert any("aborted" in n.text for n in notices)


def test_conjure_teleport_and_destroy_shuttles():
	g = galaxy()
	aegis, dione = g.ships["0100"], g.ships["0200"]
	created = sh.conjure_shuttle(g, "Raptor 1", aegis)
	assert created.id == "9901"  # conjured after /startgame: control's 99 prefix
	with pytest.raises(GameError, match="already exists"):
		sh.conjure_shuttle(g, "raptor 1", dione)
	with pytest.raises(GameError, match="name of a ship"):
		sh.conjure_shuttle(g, "Dione", aegis)
	sh.teleport_shuttle(g, "Raptor 1", dione)
	assert created.id in dione.shuttles
	pilot = aboard(g, "0200")
	pilot.clearances.append(created.id)
	sh.destroy_shuttle(g, "Raptor 1")
	assert created.id not in dione.shuttles and not pilot.clearances


def test_restart_drops_half_done_countdowns():
	g = galaxy()
	g.ships["0100"].shuttles["0101"].launching = True
	g.ships["0200"].jumping = True
	notices = sh.drop_stale_countdowns(g)
	assert not g.ships["0100"].shuttles["0101"].launching
	assert not g.ships["0200"].jumping
	assert {(n.target, n.id) for n in notices} == {(HANGAR, "0100"), (BRIDGE, "0200")}


# Jumps


def ready_to_jump(g, ship_id: str = "0100"):
	ship = g.ships[ship_id]
	jumps.set_coords(ship, "55555")
	for station in ship.stations.values():
		if station.name == "Jump Drive":
			station.exhausted = True
	return ship


def test_jump_moves_the_ship_and_everything_aboard():
	g = galaxy()
	aegis = ready_to_jump(g)
	crew = aboard(g, "0100")
	fighter = aboard(g, "0200", "0002")
	fighter.location, fighter.combat_origin_ship = LOCATION_COMBAT, "0100"
	jump, notices = jumps.start_jump(g, aegis)
	assert aegis.jumping and any(n.target == CONTROL for n in notices)
	moved = jumps.complete_jump(g, jump)
	assert moved is not None
	assert aegis.coordinates == "55555" and not aegis.jumping
	assert aegis.resources.fuel == 1  # 8 - 7 for a short jump
	assert aegis.next_coordinates == "" and "55555" in g.coordinates
	assert fighter.location == LOCATION_COMBAT  # nobody is pulled out of combat
	assert crew.location == "0100" and "0101" in aegis.shuttles
	left = {n.id for n in moved.notices if "jumped away" in n.text}
	assert "0200" in left and "0100" not in left


def test_jump_restrictions_and_subbed_coordinates():
	g = galaxy()
	aegis = g.ships["0100"]
	with pytest.raises(GameError, match="No jump coordinates"):
		jumps.start_jump(g, aegis)
	jumps.set_coords(aegis, "55555")
	with pytest.raises(GameError, match="hasn't been activated"):
		jumps.start_jump(g, aegis)
	with pytest.raises(GameError, match="5 digits"):
		jumps.set_coords(aegis, "123")
	ready_to_jump(g)
	jumps.sub_coords(aegis, "66666")
	assert "66666" not in jumps.check_coords(aegis)
	jumps.set_jump_distance(g, "66666", "long")
	with pytest.raises(jumps.JumpRefused) as refused:
		jumps.start_jump(g, aegis)  # long costs 14 fuel, the Aegis has 8
	assert refused.value.notices[0].target == CONTROL
	assert "emergency jump" in str(refused.value)
	jumps.clear_subbed_coords(aegis)
	jump, notices = jumps.start_jump(g, aegis)
	with pytest.raises(GameError, match="ship is jumping"):
		jumps.set_coords(aegis, "77777")
	jumps.abort_jump(g, aegis)
	assert jumps.complete_jump(g, jump) is None and aegis.coordinates == "12903"


def test_jump_cancels_launches_to_the_ship():
	g = galaxy()
	dione = ready_to_jump(g, "0200")
	dione.resources.fuel = 10
	pilot = aboard(g, "0100")
	sh.launch(g, pilot, g.ships["0100"], "Cyclops", "Dione")
	jump, _ = jumps.start_jump(g, dione)
	jumps.complete_jump(g, jump)
	assert not g.launches and "0101" in g.ships["0100"].shuttles


def test_teleport_ship_and_isolation():
	g = galaxy()
	aegis = ready_to_jump(g)
	jumps.start_jump(g, aegis)
	jumps.isolate(g, aegis)
	assert aegis.isolated and not aegis.jumping and not g.jumps
	with pytest.raises(GameError, match="isolated"):
		jumps.start_jump(g, aegis)
	with pytest.raises(GameError, match="already isolated"):
		jumps.isolate(g, aegis)
	jumps.unisolate(aegis)
	moved = jumps.teleport_ship(g, aegis, "44444")
	assert aegis.coordinates == "44444" and aegis.resources.fuel == 8
	assert any(n.id == "0200" for n in moved.notices)


def test_clearance_launches_say_so_and_stop_when_revoked():
	g = galaxy()
	aegis = g.ships["0100"]
	pilot, guard = aboard(g, "0100"), aboard(g, "0100", "0002")
	cyclops = aegis.shuttles["0101"]
	sh.intercept(g, guard, aegis, "Cyclops", "@guard")
	sh.clearance(aegis, "Cyclops", pilot)
	started, notices = sh.launch(g, pilot, aegis, "Cyclops", "Dione")
	assert "has clearance" in notices[0].text and notices[0].id == "0100"
	notices = sh.revoke_player(g, pilot)
	assert not g.launches and not cyclops.launching
	assert any("clearance was revoked" in n.text for n in notices)

	sh.clearance(aegis, "Cyclops", None)
	sh.launch(g, pilot, aegis, "Cyclops", "Dione")
	sh.revoke_shuttle(g, aegis, "Cyclops")
	assert not g.launches


def test_interception_stays_behind_at_the_ship_it_left():
	g = galaxy()
	aegis, dione = g.ships["0100"], g.ships["0200"]
	pilot, guard = aboard(g, "0100"), aboard(g, "0100", "0002")
	sh.intercept(g, guard, aegis, "Cyclops", "@guard")
	sh.clearance(aegis, "Cyclops", pilot)
	started, _ = sh.launch(g, pilot, aegis, "Cyclops", "Dione")
	sh.complete_launch(g, started)
	assert not dione.shuttles["0101"].intercepted


def test_coordinates_must_differ_from_the_ship_s():
	g = galaxy()
	aegis = g.ships["0100"]
	with pytest.raises(GameError, match="already at"):
		jumps.set_coords(aegis, aegis.coordinates)
	with pytest.raises(GameError, match="already at"):
		jumps.sub_coords(aegis, aegis.coordinates)


def test_bridges_hear_a_ship_jump_away():
	g = galaxy()
	aegis, dione = g.ships["0100"], g.ships["0200"]
	moved = jumps.teleport_ship(g, dione, "99999")
	assert {n.target for n in moved.notices if n.id == aegis.id} >= {HANGAR, BRIDGE}


def test_control_can_locate_anyone():
	g = galaxy()
	p = aboard(g, "0100")
	assert pl.locate_for_control(g, p) == (
		f"<@1> is aboard the Aegis ({g.ships['0100'].coordinates})."
	)
	p.dead = True
	assert pl.locate_for_control(g, p) == "<@1> is dead."

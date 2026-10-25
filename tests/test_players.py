from pathlib import Path

import pytest

from adama import channels as ch
from adama.access import (
	IMPERMANENT,
	LISTEN,
	POST,
	READ_ONLY,
	VOICE,
	managed_channels,
	player_access,
)
from adama.config import RolesConfig
from adama.models import Galaxy, Player
from adama.models.galaxy import STATE_INITIALIZING
from adama.rules import BROADCAST, CONTROL, HANGAR, GameError
from adama.rules import players as rules
from adama.rules.players import LobbyMember
from adama.shipfiles import ShipFile, validate_ship_files

ROOT = Path(__file__).parent.parent
ROLES = RolesConfig()


def galaxy_with_ships() -> Galaxy:
	galaxy = Galaxy(1, state=STATE_INITIALIZING)
	files = [
		ShipFile(p.name, p.read_bytes())
		for p in sorted((ROOT / "shipdefs").glob("*.json"))
	]
	ships, _ = validate_ship_files(files, galaxy)
	for ship in ships:
		galaxy.add_ship(ship)
	# Fake Discord IDs: static channels 1xx, ship channels <prefix>0<n>.
	for n, spec in enumerate(ch.CHANNELS):
		galaxy.discord.channels[spec.key] = 100 + n
	for ship in galaxy.ships.values():
		kinds = [*ch.ship_channel_names(ship.name), ch.SHIP_ROLE]
		galaxy.discord.ship_channels[ship.id] = {
			kind: int(ship.prefix) * 1000 + n for n, kind in enumerate(kinds)
		}
	return galaxy


def started(*members: LobbyMember) -> tuple[Galaxy, list[Player]]:
	galaxy = galaxy_with_ships()
	players, _, _ = rules.start_game(galaxy, list(members), ROLES)
	return galaxy, players


def member(user_id: int, *roles: str) -> LobbyMember:
	return LobbyMember(user_id, frozenset(roles))


def channel(galaxy: Galaxy, ship_id: str, kind: str) -> int:
	return galaxy.discord.ship_channels[ship_id][kind]


def static(galaxy: Galaxy, key: str) -> int:
	return galaxy.discord.channels[key]


# /startgame


def test_start_game_creates_players_from_roles():
	galaxy = galaxy_with_ships()
	members = [
		member(1, "Dione Crew", "Aegis Crew", "Councillor"),
		member(2, "President"),
		member(3, "Some Unrelated Role"),
		member(4, "First Responder", "Quellon Crew"),
	]
	players, left_out, notices = rules.start_game(galaxy, members, ROLES)
	assert left_out == [3]
	assert [p.id for p in players] == ["0001", "0002", "0003"]
	one, two, four = players
	# First crew ship = lowest ship ID.
	assert one.crew_affiliations == ["0100", "0200"] and one.location == "0100"
	assert one.councillor and not one.president
	assert two.president and two.location == ""
	assert four.first_responder
	assert galaxy.started and galaxy.turn_number == 1
	assert any(n.target == CONTROL and "<@2>" in n.text for n in notices)
	assert any(n.target == BROADCAST for n in notices)
	# Every ship has a Jump Drive, so every bridge gets a start-of-turn reminder.
	assert sum(1 for n in notices if n.target == "bridge") == 11


def test_start_game_needs_initialization_and_ships():
	with pytest.raises(GameError):
		rules.start_game(Galaxy(1), [], ROLES)
	galaxy, _ = started()
	with pytest.raises(GameError, match="already started"):
		rules.start_game(galaxy, [], ROLES)
	with pytest.raises(GameError, match="No ships"):
		rules.start_game(Galaxy(1, state=STATE_INITIALIZING), [], ROLES)


# /travel


def test_travel_moves_and_clears_clearances():
	galaxy, [player] = started(member(1, "Aegis Crew"))
	player.clearances.append("0101")
	notices = rules.travel(galaxy, player, "dione")
	assert player.location == "0200"
	assert player.clearances == []
	assert [(n.target, n.id) for n in notices] == [(HANGAR, "0100"), (HANGAR, "0200")]


@pytest.mark.parametrize(
	"setup, message",
	[
		(lambda g, p: setattr(p, "wounded", True), "wounded"),
		(lambda g, p: setattr(g.ships["0200"], "isolated", True), "isolated"),
		(lambda g, p: setattr(g.ships["0100"], "isolated", True), "isolated"),
		(lambda g, p: setattr(g.ships["0200"], "destroyed", True), "destroyed"),
		(lambda g, p: setattr(g.ships["0200"], "coordinates", "55555"), "coordinates"),
		(lambda g, p: setattr(g.coordinates["12903"], "locked_down", True), "lockdown"),
	],
)
def test_travel_restrictions(setup, message):
	galaxy, [player] = started(member(1, "Aegis Crew"))
	setup(galaxy, player)
	with pytest.raises(GameError, match=message):
		rules.travel(galaxy, player, "Dione")
	assert player.location == "0100"


def test_travel_errors():
	galaxy, [player] = started(member(1, "Aegis Crew"))
	with pytest.raises(GameError, match="already aboard"):
		rules.travel(galaxy, player, "Aegis")
	with pytest.raises(GameError, match="no ship named"):
		rules.travel(galaxy, player, "Galactica")


def test_only_first_responders_enter_combat_during_lockdown():
	galaxy, [crew, responder] = started(
		member(1, "Aegis Crew"), member(2, "Aegis Crew", "First Responder")
	)
	with pytest.raises(GameError, match="first responders during a lockdown"):
		rules.travel(galaxy, responder, "combat")
	galaxy.coordinates["12903"].locked_down = True
	with pytest.raises(GameError, match="first responders"):
		rules.travel(galaxy, crew, "combat")
	rules.travel(galaxy, responder, "combat")
	assert responder.location == "combat" and responder.combat_origin_ship == "0100"
	# From combat they may go to any ship at those coordinates, despite the lockdown.
	rules.travel(galaxy, responder, "Salvador")
	assert responder.location == "1000" and responder.combat_origin_ship is None


def test_teleport_ignores_restrictions_but_not_brig_or_death():
	galaxy, [player] = started(member(1, "Aegis Crew"))
	player.wounded = True
	galaxy.ships["0200"].coordinates = "55555"
	rules.teleport(galaxy, player, "Dione")
	assert player.location == "0200"
	player.brigged = True
	with pytest.raises(GameError, match="unbrig"):
		rules.teleport(galaxy, player, "Aegis")
	player.brigged, player.dead = False, True
	with pytest.raises(GameError, match="dead"):
		rules.teleport(galaxy, player, "Aegis")


def test_locate_needs_the_same_coordinates():
	galaxy, [a, b] = started(member(1, "Aegis Crew"), member(2, "Dione Crew"))
	assert rules.locate(galaxy, a, b) == "<@2> is aboard the Dione."
	galaxy.ships["0200"].coordinates = "55555"
	with pytest.raises(GameError, match="Unable to locate"):
		rules.locate(galaxy, a, b)


def test_add_and_remove_team():
	galaxy, _ = started(member(1, "Aegis Crew"))
	player = rules.add_player(galaxy, 9)
	assert player.location == ""
	rules.add_team(galaxy, player, galaxy.ships["0300"])
	assert player.crew_affiliations == ["0300"] and player.location == "0300"
	rules.add_team(galaxy, player, galaxy.ships["0400"])
	assert player.location == "0300"
	with pytest.raises(GameError, match="already crew"):
		rules.add_team(galaxy, player, galaxy.ships["0400"])
	rules.remove_team(galaxy, player, galaxy.ships["0300"])
	assert player.crew_affiliations == ["0400"]
	with pytest.raises(GameError, match="already a player"):
		rules.add_player(galaxy, 9)


# Permissions


def test_crew_aboard_get_hangar_bridge_and_voice():
	galaxy, [player] = started(member(1, "Aegis Crew"))
	access = player_access(galaxy, player)
	assert access[channel(galaxy, "0100", ch.SHIP_HANGAR)] == IMPERMANENT
	assert access[channel(galaxy, "0100", ch.SHIP_BRIDGE)] == IMPERMANENT
	assert access[channel(galaxy, "0100", ch.SHIP_VC_BRIDGE)] == VOICE
	assert access[channel(galaxy, "0100", "vc_shadowy_corner")] == VOICE
	assert access[static(galaxy, ch.LAWS)] == READ_ONLY
	assert access[static(galaxy, ch.VC_FLEET_ANNOUNCEMENTS)] == LISTEN
	assert static(galaxy, ch.SECRET_LAWS) not in access
	# Nothing on other ships.
	assert channel(galaxy, "0200", ch.SHIP_HANGAR) not in access


def test_bridge_only_while_aboard_and_visitors_get_no_bridge():
	galaxy, [player] = started(member(1, "Aegis Crew"))
	rules.travel(galaxy, player, "Quellon")
	access = player_access(galaxy, player)
	assert channel(galaxy, "0100", ch.SHIP_BRIDGE) not in access
	assert channel(galaxy, "0400", ch.SHIP_HANGAR) in access
	assert channel(galaxy, "0400", ch.SHIP_BRIDGE) not in access


def test_offices_and_council_chambers():
	galaxy, [president, councillor, visitor] = started(
		member(1, "Dione Crew", "President"),
		member(2, "Dione Crew", "Councillor"),
		member(3, "Dione Crew"),
	)
	chambers = static(galaxy, ch.VC_COUNCIL_CHAMBERS)
	notes = static(galaxy, ch.COUNCIL_NOTES)
	assert player_access(galaxy, president)[static(galaxy, ch.LAWS)] == POST
	assert player_access(galaxy, president)[static(galaxy, ch.SECRET_LAWS)] == POST
	assert (
		player_access(galaxy, councillor)[static(galaxy, ch.SECRET_LAWS)] == READ_ONLY
	)
	assert player_access(galaxy, councillor)[chambers] == VOICE
	assert player_access(galaxy, councillor)[notes] == POST
	assert player_access(galaxy, visitor)[chambers] == LISTEN
	assert notes not in player_access(galaxy, visitor)
	galaxy.council_sealed = True
	assert chambers not in player_access(galaxy, visitor)
	visitor.permitted = True
	assert player_access(galaxy, visitor)[chambers] == VOICE
	# Away from the chambers ship, councillors can't use the chambers or notes.
	rules.travel(galaxy, councillor, "Aegis")
	assert chambers not in player_access(galaxy, councillor)
	assert notes not in player_access(galaxy, councillor)


def test_brigged_and_dead_players():
	galaxy, [player] = started(member(1, "Aegis Crew"))
	player.brigged = True
	access = player_access(galaxy, player)
	assert access[static(galaxy, ch.BRIG)] == POST
	assert channel(galaxy, "0100", ch.SHIP_HANGAR) not in access
	player.dead = True
	assert player_access(galaxy, player) == {}


def test_combat_channels_per_coordinates():
	galaxy, [crew, responder, observer] = started(
		member(1, "Aegis Crew"),
		member(2, "Aegis Crew", "First Responder"),
		member(3, "Lucas Crew"),
	)
	observer.battlemap_observer = True
	coords = galaxy.coordinates["12903"]
	coords.locked_down = True
	coords.combat_text_channel_id, coords.combat_voice_channel_id = 9001, 9002
	assert 9001 in managed_channels(galaxy)
	assert 9001 not in player_access(galaxy, crew)
	assert player_access(galaxy, responder)[9002] == VOICE
	assert player_access(galaxy, observer)[9001] == READ_ONLY
	assert player_access(galaxy, observer)[9002] == LISTEN
	rules.travel(galaxy, responder, "combat")
	access = player_access(galaxy, responder)
	assert access[9001] == POST
	assert channel(galaxy, "0100", ch.SHIP_HANGAR) not in access

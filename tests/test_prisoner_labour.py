"""The Vulcan's Prisoner Labour cards."""

import pytest
from test_ship_rules import Dice, galaxy
from test_travel_rules import aboard

from adama.rules import CONTROL, PDA, GameError
from adama.rules import cards
from adama.rules import prisoner_labour as labour
from adama.rules import stations as st
from adama.rules import turns

VULCAN, AEGIS = "0900", "0100"
CARD = "0944"


def holding_labour(g, ship_id=AEGIS):
	"""An Aegis crew member holding a Prisoner Labour card, with no Aegis crew free."""
	player = aboard(g, ship_id)
	player.action_cards[CARD] = g.ships[VULCAN].action_cards.pop(CARD)
	for unit in g.ships[ship_id].units.values():
		unit.exhausted = True
	return player


def work(g, player, station_id):
	card = labour.held_card(g, player, CARD)
	ship = g.ships[AEGIS]
	notices = st.activate(ship, station_id, None, None, "@p", labour=labour.label(card))
	labour.put_to_work(g, card, st.station_on(ship, station_id))
	return notices


def test_prisoner_labour_crews_an_untrained_station():
	g = galaxy()
	player = holding_labour(g)
	notices = work(g, player, "0129")  # needs 1 team: the card is all the crew
	assert "Prisoner Labour `0944`" in notices[0].text
	assert g.prisoner_labour == {CARD: "0129"}
	with pytest.raises(GameError, match="already worked"):
		labour.held_card(g, player, CARD)


def test_prisoner_labour_limits():
	g = galaxy()
	player = holding_labour(g)
	card = labour.held_card(g, player, CARD)
	with pytest.raises(GameError, match="untrained"):  # the Sickbay needs a medic
		st.activate(g.ships[AEGIS], "0127", None, None, "@p", labour=labour.label(card))
	vulcan = g.ships[VULCAN]
	with pytest.raises(GameError, match="Guard Quarters"):
		st.activate(vulcan, "0917", None, None, "@p", labour=labour.label(card))
	with pytest.raises(GameError, match="isn't played"):
		cards.play(g, player, CARD, None)
	with pytest.raises(GameError, match="Only a player"):
		labour.held_card(g, None, CARD)


def test_prisoner_labour_repairs():
	g = galaxy()
	player = holding_labour(g)
	station = g.ships[AEGIS].stations["0129"]
	station.damaged = True
	station.repair_cost.crew_keyword, station.repair_cost.crew_amount = "team", 1
	card = labour.held_card(g, player, CARD)
	notices = st.repair(g.ships[AEGIS], "0129", None, "@p", labour=labour.label(card))
	assert not station.damaged and "Prisoner Labour" in notices[0].text


def test_used_cards_return_to_the_vulcan_and_may_riot():
	g = galaxy()
	player = holding_labour(g)
	work(g, player, "0129")
	notices = labour.end_of_turn(g, Dice(1))
	assert CARD in g.ships[VULCAN].action_cards and CARD not in player.action_cards
	assert not g.ships[VULCAN].action_cards[CARD].exhausted
	assert g.ships[AEGIS].stations["0129"].riot
	assert any(n.target == PDA and "returned" in n.text for n in notices)
	assert any(n.target == CONTROL and n.claim for n in notices)
	assert g.prisoner_labour == {}


def test_unused_cards_stay_and_quiet_prisoners_dont_riot():
	g = galaxy()
	player = holding_labour(g)
	assert labour.end_of_turn(g, Dice()) == []  # not used: nothing happens
	assert CARD in player.action_cards
	work(g, player, "0129")
	g.ships[AEGIS].morale_rolled_this_turn = True
	for ship in g.ships.values():
		ship.morale_rolled_this_turn = True
	turns.end_turn(g, Dice(4))
	assert not g.ships[AEGIS].stations["0129"].riot
	assert CARD in g.ships[VULCAN].action_cards

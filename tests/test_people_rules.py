"""Wounds, death, the brig, action cards and the council."""

import pytest
from test_ship_rules import galaxy
from test_travel_rules import aboard

from adama import channels as ch
from adama.access import IMPERMANENT, IMPERMANENT_READ_ONLY, player_access
from adama.models.player import LOCATION_BRIG
from adama.rules import BRIG, CONTROL, HANGAR, PDA, GameError
from adama.rules import cards
from adama.rules import characters as chars
from adama.rules import council
from adama.rules import players as pl
from adama.rules import shuttles as sh

# Health


def test_wound_blocks_travel_and_calls_control_without_a_sickbay():
	g = galaxy()
	p = aboard(g, "0100")  # the Aegis has a Sickbay
	notices = chars.wound(g, p)
	assert [n.target for n in notices] == [PDA]
	assert "healed at the end of the turn" in notices[0].text
	with pytest.raises(GameError, match="wounded"):
		pl.travel(g, p, "Dione")
	with pytest.raises(GameError, match="already wounded"):
		chars.wound(g, p)
	chars.heal(p)
	assert not p.wounded

	far = aboard(g, "0600", "0002")  # the Lucas has none
	g.ships["0100"].stations[
		next(s.id for s in g.ships["0100"].stations.values() if s.name == "Sickbay")
	].riot = True  # a riot doesn't stop a Sickbay
	notices = chars.wound(g, far)
	pda = next(n for n in notices if n.target == PDA)
	call = next(n for n in notices if n.target == CONTROL)
	assert "Control has been alerted" in pda.text and "/control" in pda.text
	assert call.claim and "<@2>" in call.text and "Aegis, Dione" in call.text


def test_endturn_heals_in_the_brig_ship_sickbay():
	from adama.rules import turns

	g = galaxy()
	prisoner = aboard(g, "0200")
	chars.brig(g, prisoner)  # the brig is on the Aegis, which has a Sickbay
	notices = chars.wound(g, prisoner)
	assert not any(n.target == CONTROL for n in notices)
	turns.end_turn(g)
	turns.end_turn(g)  # the first is refused: no morale rolls
	assert not prisoner.wounded


def test_kill_and_resurrect_restore_everything():
	g = galaxy()
	p = aboard(g, "0100")
	p.president = p.first_responder = True
	p.action_cards["0140"] = g.ships["0100"].action_cards.pop("0140")
	sh.launch(g, p, g.ships["0100"], "Cyclops", "Dione")
	notices = chars.kill(g, p)
	assert p.dead and not p.president and not p.crew_affiliations
	assert p.location == "" and not g.launches
	assert "0140" in p.action_cards  # the ghost keeps the cards
	assert player_access(g, p) == {}
	assert any(n.target == PDA and "died" in n.text for n in notices)
	p.stored_state["roles"] = ["Aegis Crew", "President"]  # filled in by the cog

	with pytest.raises(GameError, match="already dead|is dead"):
		chars.kill(g, p)
	chars.resurrect(g, p, None)
	assert not p.dead and p.president and p.first_responder
	assert p.crew_affiliations == ["0100"] and p.location == "0100"
	assert chars.stored_roles(p) == ["Aegis Crew", "President"]


def test_resurrect_somewhere_else_or_without_a_crew():
	g = galaxy()
	p = aboard(g, "0100", crew=False)
	chars.kill(g, p)
	with pytest.raises(GameError, match="choose where"):
		chars.resurrect(g, p, None)
	chars.resurrect(g, p, g.ships["0200"])
	assert p.location == "0200"


def test_brig_and_unbrig():
	g = galaxy()
	p = aboard(g, "0200")
	p.clearances.append("0101")
	notices = chars.brig(g, p)
	assert p.brigged and p.location == LOCATION_BRIG and not p.clearances
	assert {n.target for n in notices} == {HANGAR, PDA, BRIG}
	assert pl.current_ship(g, p).id == "0100"  # the brig is on the Aegis
	chars.move_brig(g, g.ships["0200"])
	assert pl.current_ship(g, p).id == "0200"  # brigged players move with it
	chars.unbrig(g, p)
	assert not p.brigged and p.location == "0200"
	g.brig_ship = None
	with pytest.raises(GameError, match="no brig"):
		chars.brig(g, p)


# Action cards


def test_take_leave_and_give_cards():
	g = galaxy()
	aegis = g.ships["0100"]
	crew, visitor = aboard(g, "0100"), aboard(g, "0100", "0002", crew=False)
	with pytest.raises(GameError, match="Only crew"):
		cards.take(g, visitor, aegis, "0140")
	cards.take(g, crew, aegis, "0140")
	assert "0140" in crew.action_cards and "0140" not in aegis.action_cards
	with pytest.raises(GameError, match="only be given to crew"):
		cards.give(g, crew, "0140", visitor)  # affiliation restricted
	crew.action_cards["0140"].affiliation_restricted = False
	notices = cards.give(g, crew, "0140", visitor)
	assert "0140" in visitor.action_cards and len(notices) == 2
	cards.leave(visitor, aegis, "0140")
	assert "0140" in aegis.action_cards
	elsewhere = aboard(g, "0200", "0003")
	cards.take(g, crew, aegis, "0141")
	with pytest.raises(GameError, match="same ship"):
		cards.give(g, crew, "0141", elsewhere)


def test_playing_cards():
	g = galaxy()
	p = aboard(g, "0100")
	single = g.ships["0100"].action_cards.pop("0141")  # not retained
	kept = g.ships["0100"].action_cards.pop("0140")  # retained
	once = g.ships["0800"].action_cards.pop("0840")  # once per turn
	for card in (single, kept, once):
		p.action_cards[card.id] = card
	notices = cards.play(g, p, "0141", "on the Dione")
	assert notices[0].target == CONTROL and notices[0].ping
	assert "on the Dione" in notices[0].text and "0141" not in p.action_cards
	cards.play(g, p, "0140", None)
	assert "0140" in p.action_cards and not kept.exhausted
	cards.play(g, p, "0840", None)
	assert once.exhausted
	with pytest.raises(GameError, match="already been used"):
		cards.play(g, p, "0840", None)


def test_status_shows_card_details():
	from adama.status import bridge_status, pda_status

	g = galaxy()
	p = aboard(g, "0100")
	aegis = g.ships["0100"]
	card = aegis.action_cards["0140"]
	card.description = "Reroll one die."
	card.retained, card.once_per_turn = True, True
	assert "`0140` **" in bridge_status(
		g, aegis
	) and "(once per turn; restricted to Aegis crew): Reroll one die." in bridge_status(
		g, aegis
	)
	cards.take(g, p, aegis, "0140")
	card.affiliation_restricted, card.once_per_turn = False, False
	assert "(reusable): Reroll one die." in pda_status(g, p)
	card.retained = False
	assert "(single use)" in pda_status(g, p)


def test_control_card_commands():
	g = galaxy()
	p = aboard(g, "0100")
	card, notices = cards.conjure(g, "Lucky Break", p, None)
	assert card.id == "9940" and card.affiliation == "0100" and notices
	assert card.id in p.action_cards
	cards.set_description(g, card.id, "Reroll one die.")
	cards.set_retain(g, card.id, "once")
	assert card.retained and card.once_per_turn
	cards.set_affiliation(g, card.id, g.ships["0200"], False)
	assert card.affiliation == "0200" and not card.affiliation_restricted
	cards.transfer(g, card.id, None, g.ships["0300"])
	assert card.id in g.ships["0300"].action_cards
	with pytest.raises(GameError, match="one of them"):
		cards.transfer(g, card.id, p, g.ships["0300"])
	cards.destroy(g, card.id)
	with pytest.raises(GameError, match="no action card"):
		cards.destroy(g, card.id)


# The council


def test_president_chambers_commands():
	g = galaxy()
	president = aboard(g, "0200", crew=False)
	president.president = True
	councillor = aboard(g, "0300", "0002")
	councillor.councillor = True
	guest = aboard(g, "0200", "0003")
	summoned = council.summon(g, president)
	assert {n.id for n in summoned if n.target == PDA} == {president.id, "0002"}
	assert any(n.target == CONTROL and n.claim for n in summoned)

	chambers_text = 77
	g.discord.channels[ch.COUNCIL_CHAMBERS] = chambers_text
	assert player_access(g, guest)[chambers_text] == IMPERMANENT_READ_ONLY
	assert player_access(g, president)[chambers_text] == IMPERMANENT
	change = council.seal(g, president)
	assert g.council_sealed and guest in change.resync
	assert chambers_text not in player_access(g, guest)
	council.permit(g, president, guest)
	assert guest.permitted
	with pytest.raises(GameError, match="always speak"):
		council.permit(g, president, councillor)
	council.gag(g, president, guest)
	assert not guest.permitted
	council.unseal(g, president)

	president.location = "0100"
	with pytest.raises(GameError, match="aboard the Dione"):
		council.seal(g, president)
	change = council.relocate(g, president, g.ships["0100"])
	assert g.council_chambers_ship == "0100" and guest in change.resync
	with pytest.raises(GameError, match="Only the president"):
		council.summon(g, guest)


def test_elections():
	g = galaxy()
	p = aboard(g, "0100")
	p.permitted = True
	change = council.elect(p, council.COUNCILLOR)
	assert (
		p.councillor and not p.permitted and change.roles == [(p, "councillor", True)]
	)
	council.elect(p, council.PRESIDENT)
	with pytest.raises(GameError, match="already president"):
		council.elect(p, council.PRESIDENT)
	change = council.depose(p, None)
	assert not p.president and not p.councillor and len(change.roles) == 2
	with pytest.raises(GameError, match="doesn't hold"):
		council.depose(p, council.PRESIDENT)


def test_wolves_are_secret():
	from adama.models.galaxy import STATE_INITIALIZING
	from adama.status import pda_status

	g = galaxy()
	p = aboard(g, "0100")
	assert "wolf" not in pda_status(g, p)
	chars.set_wolf(p, True)
	assert p.wolf and "You are a wolf" in pda_status(g, p)
	with pytest.raises(GameError, match="already a wolf"):
		chars.set_wolf(p, True)
	assert "<@1>: Aegis" in chars.wolves_list(g)
	chars.set_wolf(p, False)
	assert chars.wolves_list(g).endswith("none")

	g.state = STATE_INITIALIZING
	with pytest.raises(GameError, match="pregame lobby"):
		chars.set_lobby_wolf(g, 42, True)
	g.discord.admitted.append(42)
	chars.set_lobby_wolf(g, 42, True)
	assert g.lobby_wolves == {42} and "(pregame lobby)" in chars.wolves_list(g)
	from adama.config import RolesConfig

	g.players.clear()
	players, _, _ = pl.start_game(
		g, [pl.LobbyMember(42, frozenset({"Aegis Crew"}))], RolesConfig()
	)
	assert players[0].wolf and not g.lobby_wolves

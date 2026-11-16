"""The Vulcan's Prisoner Labour cards: untrained crew that any player can hold and use
for one station a turn. At /endturn a card that worked returns to the Vulcan (the
Warden's ship) and rolls 1d6: on a 1 the station it worked riots."""

import random

from adama.models.action_card import ActionCard
from adama.models.galaxy import Galaxy
from adama.models.player import Player
from adama.models.ship import Ship
from adama.models.station import Station
from adama.rules import BRIDGE, CONTROL, PDA, GameError, Notice
from adama.rules.players import mention
from adama.rules.stations import PRISONER_LABOUR, riot_at


def is_labour(card: ActionCard) -> bool:
	return card.name.casefold() == PRISONER_LABOUR.casefold()


def label(card: ActionCard) -> str:
	"""How the card is named as crew in station messages."""
	return f"{PRISONER_LABOUR} `{card.id}`"


def held_card(galaxy: Galaxy, player: Player | None, card_id: str) -> ActionCard:
	"""The Prisoner Labour card the player is putting to work, checked."""
	if player is None:
		raise GameError(f"Only a player holding a {PRISONER_LABOUR} card can use one.")
	card = player.action_cards.get(card_id.strip())
	if card is None:
		raise GameError(f"You don't hold a card `{card_id}`.")
	if not is_labour(card):
		raise GameError(f"**{card.name}** isn't a {PRISONER_LABOUR} card.")
	if card.exhausted or card.id in galaxy.prisoner_labour:
		raise GameError(
			f"{label(card)} has already worked a station this turn. It returns to the "
			"Vulcan at the end of the turn."
		)
	return card


def put_to_work(galaxy: Galaxy, card: ActionCard, station: Station) -> None:
	card.exhausted = True
	galaxy.prisoner_labour[card.id] = station.id


def _holder(galaxy: Galaxy, card_id: str) -> Player | Ship | None:
	for player in galaxy.players.values():
		if card_id in player.action_cards:
			return player
	for ship in galaxy.ships.values():
		if card_id in ship.action_cards:
			return ship
	return None


def _station(galaxy: Galaxy, station_id: str) -> tuple[Ship, Station] | None:
	for ship, station in galaxy.iter_stations():
		if station.id == station_id:
			return ship, station
	return None


def end_of_turn(galaxy: Galaxy, rng: random.Random) -> list[Notice]:
	"""Every card that worked this turn goes back to the Vulcan and rolls for a riot."""
	notices: list[Notice] = []
	for card_id, station_id in sorted(galaxy.prisoner_labour.items()):
		holder = _holder(galaxy, card_id)
		if holder is None:
			continue  # destroyed by control
		card = holder.action_cards[card_id]
		card.exhausted = False
		warden = galaxy.ships.get(card.affiliation)
		if warden is not None and not warden.destroyed and holder is not warden:
			del holder.action_cards[card_id]
			warden.action_cards[card_id] = card
			if isinstance(holder, Player):
				notices.append(
					Notice(
						PDA,
						f"{label(card)} has returned to the {warden.name}.",
						holder.id,
					)
				)
		elif warden is None or warden.destroyed:
			notices.append(
				Notice(
					CONTROL,
					f"{label(card)} couldn't return to its ship, which is gone: it "
					"stays where it is.",
				)
			)

		roll = rng.randint(1, 6)
		found = _station(galaxy, station_id)
		if found is None:
			continue
		ship, station = found
		if roll != 1:
			notices.append(
				Notice(
					BRIDGE,
					f"The prisoners who worked `{station.id}` {station.name} have gone "
					f"back to the Vulcan quietly (rolled {roll}).",
					ship.id,
				)
			)
			continue
		who = mention(holder) if isinstance(holder, Player) else "Its holder"
		if station.riot or ship.destroyed:
			notices.append(
				Notice(
					CONTROL,
					f"{label(card)} rolled a 1, but `{station.id}` {station.name} on the "
					f"{ship.name} already has a riot (or the ship is gone). {who} used it.",
				)
			)
			continue
		notices += riot_at(ship, station)
		notices.append(
			Notice(
				CONTROL,
				f"{label(card)} rolled a 1: the prisoners rioted in `{station.id}` "
				f"{station.name} on the {ship.name}. {who} used it.",
				claim=True,
				links=((BRIDGE, ship.id),),
			)
		)
	galaxy.prisoner_labour.clear()
	return notices

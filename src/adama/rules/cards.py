"""Action cards: held in a player's hand (a dead player's 'ghost') or unclaimed on a ship."""

from adama import ids
from adama.models.action_card import ActionCard
from adama.models.galaxy import Galaxy
from adama.models.player import Player
from adama.models.ship import Ship
from adama.rules import CONTROL, PDA, GameError, Notice
from adama.rules.players import current_ship, mention

RETAIN_VALUES = ("false", "true", "once")


def card_text(card: ActionCard) -> str:
	return f"`{card.id}` **{card.name}**: {card.description or '(no description)'}"


def find_card(
	galaxy: Galaxy, card_id: str
) -> tuple[ActionCard, dict[str, ActionCard], str]:
	"""The card, the dict holding it, and who holds it (a mention or "the <ship>")."""
	card_id = card_id.strip()
	for ship in galaxy.ships.values():
		if card_id in ship.action_cards:
			return ship.action_cards[card_id], ship.action_cards, f"the {ship.name}"
	for player in galaxy.players.values():
		if card_id in player.action_cards:
			return player.action_cards[card_id], player.action_cards, mention(player)
	raise GameError(f"There is no action card `{card_id}`.")


def _in_hand(player: Player, card_id: str) -> ActionCard:
	card = player.action_cards.get(card_id.strip())
	if card is None:
		raise GameError(f"You don't have action card `{card_id}`.")
	return card


def _ship_name(galaxy: Galaxy, ship_id: str) -> str:
	ship = galaxy.ships.get(ship_id)
	return ship.name if ship else ship_id


def take(galaxy: Galaxy, player: Player, ship: Ship, card_id: str) -> list[Notice]:
	"""/takecard: only crew of the card's affiliated ship may take it."""
	card = ship.action_cards.get(card_id.strip())
	if card is None:
		raise GameError(f"The {ship.name} has no unclaimed action card `{card_id}`.")
	if card.affiliation not in player.crew_affiliations:
		raise GameError(
			f"Only crew of the {_ship_name(galaxy, card.affiliation)} can take this card."
		)
	del ship.action_cards[card.id]
	player.action_cards[card.id] = card
	return [Notice(PDA, f"You took an action card:\n{card_text(card)}", player.id)]


def leave(player: Player, ship: Ship, card_id: str) -> list[Notice]:
	"""/leavecard: puts a card from the player's hand into the ship's unclaimed cards."""
	card = _in_hand(player, card_id)
	del player.action_cards[card.id]
	ship.action_cards[card.id] = card
	return [
		Notice(
			PDA, f"You left **{card.name}** `{card.id}` on the {ship.name}.", player.id
		)
	]


def give(galaxy: Galaxy, player: Player, card_id: str, target: Player) -> list[Notice]:
	card = _in_hand(player, card_id)
	if target.id == player.id:
		raise GameError("You already have that card.")
	if target.dead:
		raise GameError(f"{mention(target)} is dead.")
	here, there = current_ship(galaxy, player), current_ship(galaxy, target)
	if here is None or player.location != target.location or here is not there:
		raise GameError(f"You and {mention(target)} must be aboard the same ship.")
	if card.affiliation_restricted and card.affiliation not in target.crew_affiliations:
		raise GameError(
			f"This card can only be given to crew of the {_ship_name(galaxy, card.affiliation)}."
		)
	del player.action_cards[card.id]
	target.action_cards[card.id] = card
	return [
		Notice(
			PDA,
			f"You gave **{card.name}** `{card.id}` to {mention(target)}.",
			player.id,
		),
		Notice(
			PDA,
			f"{mention(player)} gave you an action card:\n{card_text(card)}",
			target.id,
		),
	]


def play(
	galaxy: Galaxy, player: Player, card_id: str, notes: str | None
) -> list[Notice]:
	"""/playcard: alerts (and pings) control. Single-use cards are destroyed; once-per-turn
	cards are exhausted until /endturn."""
	card = _in_hand(player, card_id)
	if card.exhausted:
		raise GameError(f"**{card.name}** has already been used this turn.")
	here = current_ship(galaxy, player)
	where = here.name if here else "nowhere"
	alert = f"{mention(player)} (aboard the {where}) played {card_text(card)}"
	if notes:
		alert += f"\nNotes: {notes}"
	if not card.retained:
		del player.action_cards[card.id]
		result = "It has been used up."
	elif card.once_per_turn:
		card.exhausted = True
		result = "You can use it again next turn."
	else:
		result = "You keep it."
	return [
		Notice(CONTROL, alert, ping=True, claim=True),
		Notice(
			PDA,
			f"You played **{card.name}**. Control has been alerted. {result}",
			player.id,
		),
	]


def _holder(
	galaxy: Galaxy, player: Player | None, ship: Ship | None
) -> tuple[dict[str, ActionCard], Player | None, Ship]:
	"""Where a card goes (a player's hand or a ship), and the ship a new card there is
	affiliated with: the ship, or the player's first crew ship."""
	if (player is None) == (ship is None):
		raise GameError("Choose a player or a ship (one of them).")
	if player is not None:
		crew_ship = (
			galaxy.ships.get(player.crew_affiliations[0])
			if player.crew_affiliations
			else current_ship(galaxy, player)
		)
		if crew_ship is None:
			raise GameError(f"{mention(player)} isn't crew of any ship.")
		return player.action_cards, player, crew_ship
	assert ship is not None
	return ship.action_cards, None, ship


def transfer(
	galaxy: Galaxy, card_id: str, player: Player | None, ship: Ship | None
) -> tuple[str, list[Notice]]:
	"""/card transfer: from anywhere to anyone, ignoring affiliation."""
	card, source, held_by = find_card(galaxy, card_id)
	dest, player, ship = _holder(galaxy, player, ship)
	if dest is source:
		raise GameError(f"{held_by} already holds that card.")
	del source[card.id]
	dest[card.id] = card
	if player is None:
		return f"Moved **{card.name}** from {held_by} to the {ship.name}.", []
	return f"Moved **{card.name}** from {held_by} to {mention(player)}.", [
		Notice(PDA, f"You were given an action card:\n{card_text(card)}", player.id)
	]


def conjure(
	galaxy: Galaxy, name: str, player: Player | None, ship: Ship | None
) -> tuple[ActionCard, list[Notice]]:
	"""/card conjure: affiliated with the ship, or the player's (first) crew ship."""
	name = name.strip()
	if not name:
		raise GameError("The card needs a name.")
	dest, player, ship = _holder(galaxy, player, ship)
	try:
		card_id = ids.next_object_id(
			galaxy, ids.ACTION_CARD, ids.conjure_prefix(galaxy, ship.prefix)
		)
	except ids.NoFreeIDError as error:
		raise GameError(str(error)) from error
	card = ActionCard(id=card_id, name=name, affiliation=ship.id)
	dest[card.id] = card
	notices = (
		[Notice(PDA, f"You were given an action card:\n{card_text(card)}", player.id)]
		if player
		else []
	)
	return card, notices


def set_description(galaxy: Galaxy, card_id: str, description: str) -> str:
	card, _, _ = find_card(galaxy, card_id)
	card.description = description.strip()
	return f"**{card.name}** now reads: {card.description}"


def set_retain(galaxy: Galaxy, card_id: str, value: str) -> str:
	value = value.casefold()
	if value not in RETAIN_VALUES:
		raise GameError(f"Choose one of: {', '.join(RETAIN_VALUES)}.")
	card, _, _ = find_card(galaxy, card_id)
	card.retained = value != "false"
	card.once_per_turn = value == "once"
	if not card.once_per_turn:
		card.exhausted = False
	kind = {"false": "single use", "true": "kept after use", "once": "once per turn"}
	return f"**{card.name}** is now {kind[value]}."


def set_affiliation(
	galaxy: Galaxy, card_id: str, ship: Ship, restricted: bool | None
) -> str:
	card, _, _ = find_card(galaxy, card_id)
	card.affiliation = ship.id
	if restricted is not None:
		card.affiliation_restricted = restricted
	limit = (
		f"only crew of the {ship.name} can be given it"
		if card.affiliation_restricted
		else "anyone can be given it"
	)
	return f"**{card.name}** is now affiliated with the {ship.name} ({limit})."


def destroy(galaxy: Galaxy, card_id: str) -> str:
	card, source, held_by = find_card(galaxy, card_id)
	del source[card.id]
	return f"Destroyed **{card.name}** (held by {held_by})."

"""Game rules as plain functions: they check restrictions, change the galaxy, and return
the notices to post. They never touch Discord, so they can be tested directly."""

from dataclasses import dataclass

HANGAR = "hangar"
BRIDGE = "bridge"
PDA = "pda"
CONTROL = "control"
BROADCAST = "broadcast"
BRIG = "brig"
COUNCIL = "council"
# Only used in Notice.links: the combat text channel (id = the coordinates).
COMBAT = "combat"


def sentence(text: str) -> str:
	"""Capitalises the first letter, leaving the rest alone."""
	return text[:1].upper() + text[1:]


def isolation(ship_name: str) -> str:
	"""What players are told whenever isolation stops them (lower-case start)."""
	return (
		f"the {ship_name} has been isolated by control while they deal with an event. "
		"It should be unisolated shortly."
	)


class GameError(Exception):
	"""A rule stopped the command. The message is shown to the user who ran it."""


@dataclass(frozen=True)
class Notice:
	"""A message to post. `target` is HANGAR or BRIDGE (with a ship ID), PDA (with a
	player ID), CONTROL (#control-chat), BROADCAST (#control-broadcasts), BRIG (#brig) or COUNCIL
	(#council-chambers).
	`ping` mentions the control role (CONTROL) or the PDA's owner (PDA). `claim` (CONTROL only) marks a
	call that needs a controller to act: the bot salutes it, and the first controller
	to salute takes it. `links` are (target, id) pairs of channels the alert is
	about, linked under it (e.g. the bridge with the problem, or a player's PDA)."""

	target: str
	text: str
	id: str = ""
	ping: bool = False
	claim: bool = False
	links: tuple[tuple[str, str], ...] = ()

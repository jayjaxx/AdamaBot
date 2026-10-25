"""Game rules as plain functions: they check restrictions, change the galaxy, and return
the notices to post. They never touch Discord, so they can be tested directly."""

from dataclasses import dataclass

HANGAR = "hangar"
BRIDGE = "bridge"
PDA = "pda"
CONTROL = "control"
BROADCAST = "broadcast"


class GameError(Exception):
	"""A rule stopped the command. The message is shown to the user who ran it."""


@dataclass(frozen=True)
class Notice:
	"""A message to post. `target` is HANGAR or BRIDGE (with a ship ID), PDA (with a
	player ID), CONTROL (#control-chat) or BROADCAST (#control-broadcasts)."""

	target: str
	text: str
	id: str = ""

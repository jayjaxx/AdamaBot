from dataclasses import dataclass, field
from typing import Any, Self

from adama.models.action_card import ActionCard

LOCATION_COMBAT = "combat"
LOCATION_BRIG = "brig"
LOCATION_NONE = ""


@dataclass
class Player:
	"""A person playing in the game. ID prefix 00.

	location is a ship ID, "combat", "brig" or "" (none). A player in combat is at
	the coordinates of their combat origin ship.
	"""

	id: str
	discord_user_id: int
	pda_channel_id: int | None = None

	crew_affiliations: list[str] = field(default_factory=list)
	location: str = LOCATION_NONE
	combat_origin_ship: str | None = None

	wounded: bool = False
	brigged: bool = False
	dead: bool = False
	president: bool = False
	councillor: bool = False
	battlemapper: bool = False
	battlemap_observer: bool = False
	first_responder: bool = False
	permitted: bool = False
	# GM-only, no gameplay effect.
	wolf: bool = False

	clearances: list[str] = field(default_factory=list)
	action_cards: dict[str, ActionCard] = field(default_factory=dict)
	# Crews, offices, flags and roles kept while dead, for /ressurrect.
	stored_state: dict[str, Any] | None = None

	def to_dict(self) -> dict[str, Any]:
		return {
			"discord_user_id": self.discord_user_id,
			"pda_channel_id": self.pda_channel_id,
			"crew_affiliations": list(self.crew_affiliations),
			"location": self.location,
			"combat_origin_ship": self.combat_origin_ship,
			"wounded": self.wounded,
			"brigged": self.brigged,
			"dead": self.dead,
			"president": self.president,
			"councillor": self.councillor,
			"battlemapper": self.battlemapper,
			"battlemap_observer": self.battlemap_observer,
			"first_responder": self.first_responder,
			"permitted": self.permitted,
			"wolf": self.wolf,
			"clearances": list(self.clearances),
			"action_cards": {
				id: card.to_dict() for id, card in self.action_cards.items()
			},
			"stored_state": self.stored_state,
		}

	@classmethod
	def from_dict(cls, id: str, data: dict[str, Any]) -> Self:
		return cls(
			id=id,
			discord_user_id=data["discord_user_id"],
			pda_channel_id=data["pda_channel_id"],
			crew_affiliations=list(data["crew_affiliations"]),
			location=data["location"],
			combat_origin_ship=data["combat_origin_ship"],
			wounded=data["wounded"],
			brigged=data["brigged"],
			dead=data["dead"],
			president=data["president"],
			councillor=data["councillor"],
			battlemapper=data["battlemapper"],
			battlemap_observer=data["battlemap_observer"],
			first_responder=data["first_responder"],
			permitted=data["permitted"],
			wolf=data["wolf"],
			clearances=list(data["clearances"]),
			action_cards={
				id: ActionCard.from_dict(id, c)
				for id, c in data["action_cards"].items()
			},
			stored_state=data["stored_state"],
		)

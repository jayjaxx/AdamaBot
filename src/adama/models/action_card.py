from dataclasses import dataclass
from typing import Any, Self


@dataclass
class ActionCard:
	"""Its location is whichever ship (unclaimed) or player (hand or ghost) holds it."""

	id: str
	name: str
	affiliation: str
	affiliation_restricted: bool = True
	retained: bool = False
	once_per_turn: bool = False
	exhausted: bool = False
	description: str = ""

	def to_dict(self) -> dict[str, Any]:
		return {
			"name": self.name,
			"affiliation": self.affiliation,
			"affiliation_restricted": self.affiliation_restricted,
			"retained": self.retained,
			"once_per_turn": self.once_per_turn,
			"exhausted": self.exhausted,
			"description": self.description,
		}

	@classmethod
	def from_dict(cls, id: str, data: dict[str, Any]) -> Self:
		return cls(
			id=id,
			name=data["name"],
			affiliation=data["affiliation"],
			affiliation_restricted=data["affiliation_restricted"],
			retained=data["retained"],
			once_per_turn=data["once_per_turn"],
			exhausted=data["exhausted"],
			description=data["description"],
		)

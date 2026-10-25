from dataclasses import dataclass
from typing import Any, Self


@dataclass
class Unit:
	"""A team, on-foot unit or squadron. Its location is whichever ship or shuttle holds it."""

	id: str
	name: str
	affiliation: str
	damaged: bool = False
	exhausted: bool = False

	def to_dict(self) -> dict[str, Any]:
		return {
			"name": self.name,
			"affiliation": self.affiliation,
			"damaged": self.damaged,
			"exhausted": self.exhausted,
		}

	@classmethod
	def from_dict(cls, id: str, data: dict[str, Any]) -> Self:
		return cls(
			id=id,
			name=data["name"],
			affiliation=data["affiliation"],
			damaged=data["damaged"],
			exhausted=data["exhausted"],
		)

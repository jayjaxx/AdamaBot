from dataclasses import dataclass, field
from typing import Any, Self

from adama.models.resources import ResourcesContainer
from adama.models.unit import Unit


@dataclass
class Shuttle:
	"""The ship a shuttle is docked at is the ship that contains it."""

	id: str
	name: str

	intercepted: bool = False
	clearance: bool = False
	launching: bool = False
	resources: ResourcesContainer = field(default_factory=ResourcesContainer)
	units: dict[str, Unit] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		return {
			"name": self.name,
			"intercepted": self.intercepted,
			"clearance": self.clearance,
			"launching": self.launching,
			"resources": self.resources.to_dict(),
			"units": {id: unit.to_dict() for id, unit in self.units.items()},
		}

	@classmethod
	def from_dict(cls, id: str, data: dict[str, Any]) -> Self:
		return cls(
			id=id,
			name=data["name"],
			intercepted=data["intercepted"],
			clearance=data["clearance"],
			launching=data["launching"],
			resources=ResourcesContainer.from_dict(data["resources"]),
			units={id: Unit.from_dict(id, unit) for id, unit in data["units"].items()},
		)

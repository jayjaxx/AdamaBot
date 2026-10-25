from dataclasses import dataclass
from typing import Any, Self

RESOURCE_NAMES = ("food", "fuel", "materials", "ore", "water")


@dataclass
class ResourcesContainer:
	food: int = 0
	fuel: int = 0
	materials: int = 0
	ore: int = 0
	water: int = 0

	def get(self, name: str) -> int:
		if name not in RESOURCE_NAMES:
			raise KeyError(name)
		return getattr(self, name)

	def set(self, name: str, amount: int) -> None:
		if name not in RESOURCE_NAMES:
			raise KeyError(name)
		setattr(self, name, amount)

	def nonzero(self) -> dict[str, int]:
		return {name: self.get(name) for name in RESOURCE_NAMES if self.get(name)}

	def to_dict(self) -> dict[str, Any]:
		return {name: self.get(name) for name in RESOURCE_NAMES}

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> Self:
		return cls(**{name: data[name] for name in RESOURCE_NAMES})


@dataclass
class StationCost(ResourcesContainer):
	"""A station input or repair cost: resources plus the crew needed."""

	crew_keyword: str = ""
	crew_amount: int = 0

	def to_dict(self) -> dict[str, Any]:
		data = super().to_dict()
		data["crew_keyword"] = self.crew_keyword
		data["crew_amount"] = self.crew_amount
		return data

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> Self:
		cost = super().from_dict(data)
		cost.crew_keyword = data["crew_keyword"]
		cost.crew_amount = data["crew_amount"]
		return cost

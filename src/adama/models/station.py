from dataclasses import dataclass, field
from typing import Any, Self

from adama.models.resources import ResourcesContainer, StationCost


@dataclass
class StationMode:
	"""One choice of output for a station (e.g. Mining Facility "ore")."""

	name: str
	input: ResourcesContainer = field(default_factory=ResourcesContainer)
	output: ResourcesContainer = field(default_factory=ResourcesContainer)
	morale_modifier: int = 0

	def to_dict(self) -> dict[str, Any]:
		return {
			"name": self.name,
			"input": self.input.to_dict(),
			"output": self.output.to_dict(),
			"morale_modifier": self.morale_modifier,
		}

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> Self:
		return cls(
			name=data["name"],
			input=ResourcesContainer.from_dict(data["input"]),
			output=ResourcesContainer.from_dict(data["output"]),
			morale_modifier=data["morale_modifier"],
		)


@dataclass
class Station:
	id: str
	name: str
	description: str = ""
	usable: bool = True
	reusable: bool = False
	startofturn: bool = False
	exhausted: bool = False
	damaged: bool = False
	damaged_description: str = ""
	alert_gm_on_use: bool = False
	alert_gm_on_damage: bool = False
	riot: bool = False
	input: StationCost = field(default_factory=lambda: StationCost(crew_keyword="team"))
	output: ResourcesContainer = field(default_factory=ResourcesContainer)
	repair_cost: StationCost = field(default_factory=StationCost)
	modes: list[StationMode] = field(default_factory=list)
	units_used_this_turn: list[str] = field(default_factory=list)

	def to_dict(self) -> dict[str, Any]:
		return {
			"name": self.name,
			"description": self.description,
			"usable": self.usable,
			"reusable": self.reusable,
			"startofturn": self.startofturn,
			"exhausted": self.exhausted,
			"damaged": self.damaged,
			"damaged_description": self.damaged_description,
			"alert_gm_on_use": self.alert_gm_on_use,
			"alert_gm_on_damage": self.alert_gm_on_damage,
			"riot": self.riot,
			"input": self.input.to_dict(),
			"output": self.output.to_dict(),
			"repair_cost": self.repair_cost.to_dict(),
			"modes": [mode.to_dict() for mode in self.modes],
			"units_used_this_turn": list(self.units_used_this_turn),
		}

	@classmethod
	def from_dict(cls, id: str, data: dict[str, Any]) -> Self:
		return cls(
			id=id,
			name=data["name"],
			description=data["description"],
			usable=data["usable"],
			reusable=data["reusable"],
			startofturn=data["startofturn"],
			exhausted=data["exhausted"],
			damaged=data["damaged"],
			damaged_description=data["damaged_description"],
			alert_gm_on_use=data["alert_gm_on_use"],
			alert_gm_on_damage=data["alert_gm_on_damage"],
			riot=data["riot"],
			input=StationCost.from_dict(data["input"]),
			output=ResourcesContainer.from_dict(data["output"]),
			repair_cost=StationCost.from_dict(data["repair_cost"]),
			modes=[StationMode.from_dict(mode) for mode in data["modes"]],
			units_used_this_turn=list(data["units_used_this_turn"]),
		)

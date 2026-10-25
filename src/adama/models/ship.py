from dataclasses import dataclass, field
from typing import Any, Self

from adama.models.action_card import ActionCard
from adama.models.resources import ResourcesContainer
from adama.models.shuttle import Shuttle
from adama.models.station import Station
from adama.models.unit import Unit

# Highest to lowest; the morale roll falls through this order.
RATION_LEVELS = (
	"luxurious",
	"increased",
	"normal",
	"low",
	"minimum",
	"critical",
	"none",
)
DISTANCES = ("short", "medium", "long")


@dataclass
class RationAmount:
	"""Consumption at one ration level. None means the level is unavailable on this ship."""

	food: int | None = 0
	water: int | None = 0

	def to_dict(self) -> dict[str, Any]:
		return {"food": self.food, "water": self.water}

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> Self:
		return cls(food=data["food"], water=data["water"])


@dataclass
class FuelConsumption:
	short: int = 0
	medium: int = 0
	long: int = 0

	def get(self, distance: str) -> int:
		if distance not in DISTANCES:
			raise KeyError(distance)
		return getattr(self, distance)

	def to_dict(self) -> dict[str, Any]:
		return {distance: self.get(distance) for distance in DISTANCES}

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> Self:
		return cls(**{distance: data[distance] for distance in DISTANCES})


@dataclass
class MoraleRollModifiers:
	temp: int = 0
	perm: int = 0

	def to_dict(self) -> dict[str, Any]:
		return {
			"temp_morale_roll_modifier": self.temp,
			"perm_morale_roll_modifier": self.perm,
		}

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> Self:
		return cls(
			temp=data["temp_morale_roll_modifier"],
			perm=data["perm_morale_roll_modifier"],
		)


def _default_rations() -> dict[str, RationAmount]:
	return {level: RationAmount() for level in RATION_LEVELS}


@dataclass
class Ship:
	"""A ship in the exact shape of the shipdefs JSON.

	Players aboard are not stored here: a player's location is the source of truth.
	"""

	id: str
	name: str
	coordinates: str

	next_coordinates: str = ""
	sabotage_coordinates: str = ""
	food_ration_level: str = "normal"
	water_ration_level: str = "normal"
	isolated: bool = False
	destroyed: bool = False
	jumping: bool = False
	morale_level: int = 10
	morale_rolled_this_turn: bool = False
	damage_taken_this_turn: int = 0
	morale_roll_modifiers: MoraleRollModifiers = field(
		default_factory=MoraleRollModifiers
	)
	resources: ResourcesContainer = field(default_factory=ResourcesContainer)
	ration_levels: dict[str, RationAmount] = field(default_factory=_default_rations)
	fuel_consumption: FuelConsumption = field(default_factory=FuelConsumption)

	stations: dict[str, Station] = field(default_factory=dict)
	shuttles: dict[str, Shuttle] = field(default_factory=dict)
	units: dict[str, Unit] = field(default_factory=dict)
	action_cards: dict[str, ActionCard] = field(default_factory=dict)

	@property
	def prefix(self) -> str:
		return self.id[:2]

	def has_station(self, name: str) -> bool:
		return any(station.name == name for station in self.stations.values())

	def to_dict(self) -> dict[str, Any]:
		"""The ship's body, without the ID key wrapping it."""
		return {
			"name": self.name,
			"coordinates": self.coordinates,
			"next_coordinates": self.next_coordinates,
			"sabotage_coordinates": self.sabotage_coordinates,
			"food_ration_level": self.food_ration_level,
			"water_ration_level": self.water_ration_level,
			"isolated": self.isolated,
			"destroyed": self.destroyed,
			"jumping": self.jumping,
			"morale_level": self.morale_level,
			"morale_rolled_this_turn": self.morale_rolled_this_turn,
			"damage_taken_this_turn": self.damage_taken_this_turn,
			"morale_roll_modifiers": self.morale_roll_modifiers.to_dict(),
			"resources": self.resources.to_dict(),
			"ration_levels": {
				level: amount.to_dict() for level, amount in self.ration_levels.items()
			},
			"fuel_consumption": self.fuel_consumption.to_dict(),
			"players": {},
			"stations": {
				id: station.to_dict() for id, station in self.stations.items()
			},
			"shuttles": {
				id: shuttle.to_dict() for id, shuttle in self.shuttles.items()
			},
			"units": {id: unit.to_dict() for id, unit in self.units.items()},
			"action_cards": {
				id: card.to_dict() for id, card in self.action_cards.items()
			},
		}

	def to_file_dict(self) -> dict[str, Any]:
		"""The ship as a shipdefs file: a single object keyed by the ship's ID."""
		return {self.id: self.to_dict()}

	@classmethod
	def from_dict(cls, id: str, data: dict[str, Any]) -> Self:
		return cls(
			id=id,
			name=data["name"],
			coordinates=data["coordinates"],
			next_coordinates=data["next_coordinates"],
			sabotage_coordinates=data["sabotage_coordinates"],
			food_ration_level=data["food_ration_level"],
			water_ration_level=data["water_ration_level"],
			isolated=data["isolated"],
			destroyed=data["destroyed"],
			jumping=data["jumping"],
			morale_level=data["morale_level"],
			morale_rolled_this_turn=data["morale_rolled_this_turn"],
			damage_taken_this_turn=data["damage_taken_this_turn"],
			morale_roll_modifiers=MoraleRollModifiers.from_dict(
				data["morale_roll_modifiers"]
			),
			resources=ResourcesContainer.from_dict(data["resources"]),
			ration_levels={
				level: RationAmount.from_dict(amount)
				for level, amount in data["ration_levels"].items()
			},
			fuel_consumption=FuelConsumption.from_dict(data["fuel_consumption"]),
			stations={
				id: Station.from_dict(id, s) for id, s in data["stations"].items()
			},
			shuttles={
				id: Shuttle.from_dict(id, s) for id, s in data["shuttles"].items()
			},
			units={id: Unit.from_dict(id, u) for id, u in data["units"].items()},
			action_cards={
				id: ActionCard.from_dict(id, c)
				for id, c in data["action_cards"].items()
			},
		)

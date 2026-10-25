from dataclasses import dataclass, field
from typing import Any, Iterator, Self

from adama.models.action_card import ActionCard
from adama.models.player import Player
from adama.models.ship import Ship
from adama.models.shuttle import Shuttle
from adama.models.station import Station
from adama.models.unit import Unit

STATE_IDLE = "idle"
STATE_INITIALIZING = "initializing"
STATE_IN_PROGRESS = "in progress"


@dataclass
class Coordinates:
	"""Created when first referenced, with distance short. Ships at these coordinates are
	the ships whose `coordinates` match."""

	coordinates: str
	locked_down: bool = False
	distance: str = "short"
	# Per-coordinates combat channels, open only during a lockdown.
	combat_text_channel_id: int | None = None
	combat_voice_channel_id: int | None = None

	def to_dict(self) -> dict[str, Any]:
		return {
			"locked_down": self.locked_down,
			"distance": self.distance,
			"combat_text_channel_id": self.combat_text_channel_id,
			"combat_voice_channel_id": self.combat_voice_channel_id,
		}

	@classmethod
	def from_dict(cls, coordinates: str, data: dict[str, Any]) -> Self:
		return cls(coordinates=coordinates, **data)


@dataclass(eq=False)
class Launch:
	"""A shuttle launch counting down. Not saved: a restart cancels it."""

	shuttle_id: str
	player_id: str
	origin_id: str
	destination_id: str


@dataclass(eq=False)
class Jump:
	"""A jump counting down. Not saved: a restart cancels it."""

	ship_id: str


@dataclass
class DiscordState:
	"""Discord IDs the bot has created or must remember. Not part of the game rules."""

	# Permanent channels and categories, keyed by their constant name.
	channels: dict[str, int] = field(default_factory=dict)
	# Per-ship category, channels and crew role, keyed by ship ID then by kind.
	ship_channels: dict[str, dict[str, int]] = field(default_factory=dict)
	# Pregame lobby.
	start_message_id: int | None = None
	queue: list[int] = field(default_factory=list)
	admitted: list[int] = field(default_factory=list)
	# PDA channel per Discord user, created on entering the lobby.
	pdas: dict[int, int] = field(default_factory=dict)
	pda_categories: list[int] = field(default_factory=list)
	fleet_status_message_ids: list[int] = field(default_factory=list)

	def to_dict(self) -> dict[str, Any]:
		return {
			"channels": dict(self.channels),
			"ship_channels": {id: dict(c) for id, c in self.ship_channels.items()},
			"start_message_id": self.start_message_id,
			"queue": list(self.queue),
			"admitted": list(self.admitted),
			# JSON keys must be strings.
			"pdas": {str(user): channel for user, channel in self.pdas.items()},
			"pda_categories": list(self.pda_categories),
			"fleet_status_message_ids": list(self.fleet_status_message_ids),
		}

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> Self:
		return cls(
			channels=dict(data["channels"]),
			ship_channels={id: dict(c) for id, c in data["ship_channels"].items()},
			start_message_id=data["start_message_id"],
			queue=list(data["queue"]),
			admitted=list(data["admitted"]),
			pdas={int(user): channel for user, channel in data["pdas"].items()},
			pda_categories=list(data["pda_categories"]),
			fleet_status_message_ids=list(data["fleet_status_message_ids"]),
		)


@dataclass
class Galaxy:
	"""The game state of one Discord server."""

	guild_id: int

	state: str = STATE_IDLE
	player_limit: int = 0
	turn_number: int = 0
	fleet_temp_morale_roll_modifier: int = 0
	fleet_perm_morale_roll_modifier: int = 0
	council_chambers_ship: str | None = None
	council_sealed: bool = False
	brig_ship: str | None = None
	# /endturn was refused once because ships hadn't rolled; the next one forces it.
	end_turn_warned: bool = False
	# Latest morale roll summary per ship ID, shown in #fleet-status.
	morale_results: dict[str, str] = field(default_factory=dict)

	coordinates: dict[str, Coordinates] = field(default_factory=dict)
	ships: dict[str, Ship] = field(default_factory=dict)
	players: dict[str, Player] = field(default_factory=dict)
	# Unit IDs are never reused, even after the unit is destroyed.
	used_unit_ids: set[str] = field(default_factory=set)

	discord: DiscordState = field(default_factory=DiscordState)

	# Countdowns in progress, keyed by shuttle and ship ID. A countdown finishes only if
	# its entry is still the same object when the timer ends.
	launches: dict[str, Launch] = field(default_factory=dict, repr=False)
	jumps: dict[str, Jump] = field(default_factory=dict, repr=False)

	@property
	def started(self) -> bool:
		return self.state == STATE_IN_PROGRESS

	def get_or_create_coordinates(self, coordinates: str) -> Coordinates:
		if coordinates not in self.coordinates:
			self.coordinates[coordinates] = Coordinates(coordinates)
		return self.coordinates[coordinates]

	def ships_at(
		self, coordinates: str, *, include_destroyed: bool = False
	) -> list[Ship]:
		return [
			ship
			for ship in self.ships.values()
			if ship.coordinates == coordinates
			and (include_destroyed or not ship.destroyed)
		]

	def ship_by_name(self, name: str) -> Ship | None:
		folded = name.casefold()
		for ship in self.ships.values():
			if ship.name.casefold() == folded:
				return ship
		return None

	def player_by_user(self, user_id: int) -> Player | None:
		for player in self.players.values():
			if player.discord_user_id == user_id:
				return player
		return None

	def iter_stations(self) -> Iterator[tuple[Ship, Station]]:
		for ship in self.ships.values():
			for station in ship.stations.values():
				yield ship, station

	def iter_shuttles(self) -> Iterator[tuple[Ship, Shuttle]]:
		for ship in self.ships.values():
			for shuttle in ship.shuttles.values():
				yield ship, shuttle

	def find_shuttle(self, name: str) -> tuple[Ship, Shuttle] | None:
		folded = name.casefold()
		for ship, shuttle in self.iter_shuttles():
			if shuttle.name.casefold() == folded:
				return ship, shuttle
		return None

	def iter_units(self) -> Iterator[Unit]:
		for ship in self.ships.values():
			yield from ship.units.values()
			for shuttle in ship.shuttles.values():
				yield from shuttle.units.values()

	def iter_action_cards(self) -> Iterator[ActionCard]:
		for ship in self.ships.values():
			yield from ship.action_cards.values()
		for player in self.players.values():
			yield from player.action_cards.values()

	def all_ids(self) -> set[str]:
		"""Every ID in use: ships, stations, shuttles, units, action cards and players."""
		ids: set[str] = set(self.ships) | set(self.players)
		ids.update(station.id for _, station in self.iter_stations())
		ids.update(shuttle.id for _, shuttle in self.iter_shuttles())
		ids.update(unit.id for unit in self.iter_units())
		ids.update(card.id for card in self.iter_action_cards())
		return ids

	def all_ship_names(self) -> set[str]:
		return {ship.name.casefold() for ship in self.ships.values()}

	def add_ship(self, ship: Ship) -> None:
		"""Adds a loaded ship, keeping its IDs. The first ship with a Council Chamber or
		Brig station becomes the council chambers or brig ship if none is set."""
		self.ships[ship.id] = ship
		self.get_or_create_coordinates(ship.coordinates)
		self.used_unit_ids.update(ship.units)
		for shuttle in ship.shuttles.values():
			self.used_unit_ids.update(shuttle.units)
		if self.council_chambers_ship is None and ship.has_station("Council Chamber"):
			self.council_chambers_ship = ship.id
		if self.brig_ship is None and ship.has_station("Brig"):
			self.brig_ship = ship.id

	def remove_ship(self, ship_id: str) -> Ship:
		"""Removes a ship loaded by mistake (initialization only), freeing its IDs so a
		corrected file can be loaded again."""
		ship = self.ships.pop(ship_id)
		prefix = ship.prefix
		self.used_unit_ids = {id for id in self.used_unit_ids if id[:2] != prefix}
		if self.council_chambers_ship == ship_id:
			self.council_chambers_ship = None
		if self.brig_ship == ship_id:
			self.brig_ship = None
		if not self.ships_at(ship.coordinates, include_destroyed=True):
			self.coordinates.pop(ship.coordinates, None)
		return ship

	def to_dict(self) -> dict[str, Any]:
		"""Everything except ships and players, which are stored as their own documents."""
		return {
			"state": self.state,
			"player_limit": self.player_limit,
			"turn_number": self.turn_number,
			"fleet_temp_morale_roll_modifier": self.fleet_temp_morale_roll_modifier,
			"fleet_perm_morale_roll_modifier": self.fleet_perm_morale_roll_modifier,
			"council_chambers_ship": self.council_chambers_ship,
			"council_sealed": self.council_sealed,
			"brig_ship": self.brig_ship,
			"end_turn_warned": self.end_turn_warned,
			"morale_results": dict(self.morale_results),
			"coordinates": {
				c: coords.to_dict() for c, coords in self.coordinates.items()
			},
			"used_unit_ids": sorted(self.used_unit_ids),
			"discord": self.discord.to_dict(),
		}

	@classmethod
	def from_dict(
		cls,
		guild_id: int,
		data: dict[str, Any],
		ships: dict[str, Ship],
		players: dict[str, Player],
	) -> Self:
		return cls(
			guild_id=guild_id,
			state=data["state"],
			player_limit=data["player_limit"],
			turn_number=data["turn_number"],
			fleet_temp_morale_roll_modifier=data["fleet_temp_morale_roll_modifier"],
			fleet_perm_morale_roll_modifier=data["fleet_perm_morale_roll_modifier"],
			council_chambers_ship=data["council_chambers_ship"],
			council_sealed=data["council_sealed"],
			brig_ship=data["brig_ship"],
			# .get: saved before these fields existed.
			end_turn_warned=data.get("end_turn_warned", False),
			morale_results=dict(data.get("morale_results", {})),
			coordinates={
				c: Coordinates.from_dict(c, coords)
				for c, coords in data["coordinates"].items()
			},
			ships=ships,
			players=players,
			used_unit_ids=set(data["used_unit_ids"]),
			discord=DiscordState.from_dict(data["discord"]),
		)

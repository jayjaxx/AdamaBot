import json

from adama.models.galaxy import Galaxy
from adama.models.player import Player
from adama.models.ship import Ship
from adama.repositories.base import BaseRepository


class GalaxyRepository(BaseRepository[Galaxy]):
	"""Stores each guild's galaxy, ships and players as JSON documents."""

	def save(self, entity: Galaxy) -> bool:
		"""Replaces everything stored for the galaxy's guild in one transaction."""
		guild_id = entity.guild_id
		with self.conn:
			self.conn.execute(
				"INSERT INTO galaxies (guild_id, data) VALUES (?, ?) "
				"ON CONFLICT (guild_id) DO UPDATE SET data = excluded.data",
				(guild_id, json.dumps(entity.to_dict())),
			)
			self.conn.execute("DELETE FROM ships WHERE guild_id = ?", (guild_id,))
			self.conn.executemany(
				"INSERT INTO ships (guild_id, ship_id, data) VALUES (?, ?, ?)",
				[
					(guild_id, id, json.dumps(ship.to_dict()))
					for id, ship in entity.ships.items()
				],
			)
			self.conn.execute("DELETE FROM players WHERE guild_id = ?", (guild_id,))
			self.conn.executemany(
				"INSERT INTO players (guild_id, player_id, data) VALUES (?, ?, ?)",
				[
					(guild_id, id, json.dumps(p.to_dict()))
					for id, p in entity.players.items()
				],
			)
		return True

	def load(self, id: str) -> Galaxy | None:
		"""Loads the galaxy of the guild with this ID, if one is stored."""
		guild_id = int(id)
		row = self.conn.execute(
			"SELECT data FROM galaxies WHERE guild_id = ?", (guild_id,)
		).fetchone()
		if row is None:
			return None

		ships = {
			ship_id: Ship.from_dict(ship_id, json.loads(data))
			for ship_id, data in self.conn.execute(
				"SELECT ship_id, data FROM ships WHERE guild_id = ? ORDER BY ship_id",
				(guild_id,),
			)
		}
		players = {
			player_id: Player.from_dict(player_id, json.loads(data))
			for player_id, data in self.conn.execute(
				"SELECT player_id, data FROM players WHERE guild_id = ? ORDER BY player_id",
				(guild_id,),
			)
		}
		return Galaxy.from_dict(guild_id, json.loads(row[0]), ships, players)

	def delete(self, entity: Galaxy) -> None:
		with self.conn:
			self.conn.execute(
				"DELETE FROM galaxies WHERE guild_id = ?", (entity.guild_id,)
			)

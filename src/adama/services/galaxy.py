import asyncio
import logging

from adama.models.galaxy import Galaxy
from adama.repositories.base import BaseRepository
from adama.services.base import BaseService

logger = logging.getLogger(__name__)


class GalaxyService(BaseService):
	"""Holds each guild's galaxy in memory and saves it after every change.

	Commands that change a galaxy should hold `lock(guild_id)` from reading it to saving
	it, so two commands in the same server can't interleave.
	"""

	def __init__(self, repo: BaseRepository[Galaxy]) -> None:
		self.repo = repo
		self._galaxies: dict[int, Galaxy] = {}
		self._locks: dict[int, asyncio.Lock] = {}

	def get(self, guild_id: int) -> Galaxy:
		"""The guild's galaxy, loaded from storage or created idle."""
		if guild_id not in self._galaxies:
			galaxy = self.repo.load(str(guild_id))
			self._galaxies[guild_id] = (
				galaxy if galaxy is not None else Galaxy(guild_id)
			)
		return self._galaxies[guild_id]

	def lock(self, guild_id: int) -> asyncio.Lock:
		if guild_id not in self._locks:
			self._locks[guild_id] = asyncio.Lock()
		return self._locks[guild_id]

	def save(self, galaxy: Galaxy) -> None:
		self._galaxies[galaxy.guild_id] = galaxy
		self.repo.save(galaxy)

	def close(self):
		logger.info("Closing GalaxyService!")
		self.repo.conn.close()

import sqlite3
from dataclasses import dataclass, field

from adama.db.database import get_connection
from adama.repositories import GalaxyRepository
from adama.services import GalaxyService


@dataclass
class Repositories:
	galaxy: GalaxyRepository


@dataclass
class Services:
	galaxy: GalaxyService


@dataclass
class Container:
	conn: sqlite3.Connection = field(default_factory=get_connection)
	repos: Repositories = field(init=False)
	services: Services = field(init=False)

	def __post_init__(self):
		self.repos = Repositories(galaxy=GalaxyRepository(self.conn))
		self.services = Services(galaxy=GalaxyService(self.repos.galaxy))

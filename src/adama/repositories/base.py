import sqlite3
from abc import ABC
from typing import Generic, TypeVar

from adama.db.database import get_connection

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
	def __init__(self, conn: sqlite3.Connection | None = None) -> None:
		self.conn = conn if conn is not None else get_connection()

	def save(self, entity: T) -> bool:
		raise NotImplementedError

	def load(self, id: str) -> T | None:
		raise NotImplementedError

	def delete(self, entity: T) -> None:
		raise NotImplementedError

	def purge(self, entity: T) -> None:
		raise NotImplementedError

	def all(self) -> list[T]:
		raise NotImplementedError

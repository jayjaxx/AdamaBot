import sqlite3

from adama.constants import _DATABASE_SCHEMA_LOCATION

_conn: sqlite3.Connection | None = None


def connect(path: str) -> sqlite3.Connection:
	"""Opens a new connection and makes sure the schema exists. Use ":memory:" for tests."""
	conn = sqlite3.connect(path)
	with open(_DATABASE_SCHEMA_LOCATION) as f:
		conn.executescript(f.read())
	return conn


def get_connection(path: str = ":memory:") -> sqlite3.Connection:
	"""The bot's shared connection, opened on first use."""
	global _conn
	if _conn is None:
		_conn = connect(path)
	return _conn

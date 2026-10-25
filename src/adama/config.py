import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from adama.constants import CONFIG_PATH


@dataclass
class DiscordConfig:
	TOKEN: str
	PREFIX: str = "."
	# Servers to sync slash commands to instantly while developing. Empty = sync globally.
	DEV_GUILD_IDS: list[int] = field(default_factory=list)


@dataclass
class RolesConfig:
	# Users with this role are control (GMs).
	CONTROL: str = "Control"
	# Users with this role (or Discord Administrator) may run /startinitialization.
	GAME_STARTER: str = "Game Starter"
	# Read at /startgame to set each player's offices. Ship crews use "<ship name> Crew".
	PRESIDENT: str = "President"
	COUNCILLOR: str = "Councillor"
	# First responders run the battlemap and may /travel to combat during a lockdown.
	FIRST_RESPONDER: str = "First Responder"
	# May post in #newscasts.
	PRESS: str = "Press"


@dataclass
class DatabaseConfig:
	DB_PATH: str = "src/adama/db/adama.sqlite"
	# Keep the game state in memory only: it is lost when the bot restarts.
	IN_MEMORY: bool = False


@dataclass
class GlobalConfig:
	discord: DiscordConfig
	database: DatabaseConfig
	roles: RolesConfig = field(default_factory=RolesConfig)


def load_config(file: Path = CONFIG_PATH):
	if not file.exists():
		raise RuntimeError(f"Could not find '{file}' file")

	with open(file, "rb") as f:
		data = tomllib.load(f)

	config = GlobalConfig(
		discord=DiscordConfig(**data.get("discord", {})),
		database=DatabaseConfig(**data.get("database", {})),
		roles=RolesConfig(**data.get("roles", {})),
	)

	return config

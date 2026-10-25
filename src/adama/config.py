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


@dataclass
class DatabaseConfig:
	DB_PATH: str = "src/adama/db/adama.sqlite"


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

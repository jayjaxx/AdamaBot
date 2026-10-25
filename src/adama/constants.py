from pathlib import Path

CONFIG_PATH = Path("config.toml")

SRC_DIR = Path("src")
PACKAGE_DIR = Path(__file__).parent

_BOT_EXTENSION_DIRECTORY = PACKAGE_DIR / "cogs"
_BOT_EXTENSION_MODULE = "adama.cogs"

_DATABASE_SCHEMA_LOCATION = PACKAGE_DIR / "db" / "schema.sql"

# The default fleet, loaded by /startinitialization when no ship files are attached.
DEFAULT_SHIPS_DIR = PACKAGE_DIR.parent.parent / "shipdefs"

from pathlib import Path

DEBUG_MODE = True

CONFIG_PATH = Path("config.toml")

SRC_DIR = Path("src")
PACKAGE_DIR = Path(__file__).parent

_BOT_EXTENSION_DIRECTORY = PACKAGE_DIR / "cogs"
_BOT_EXTENSION_MODULE = "adama.cogs"

_DATABASE_SCHEMA_LOCATION = PACKAGE_DIR / "db" / "schema.sql"

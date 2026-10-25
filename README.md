# Configuration Files
Please copy the `config_defaults.toml` file to a `config.toml` file for storing your private configurations.

This can be done using:
`Copy-Item config_defaults.toml config.toml`

or

`cp config_defaults.toml config.toml`

```toml
[discord]
TOKEN="<your discord token here>"
# Server IDs to sync slash commands to instantly while developing. Leave empty to sync globally.
DEV_GUILD_IDS=[]

[roles]
CONTROL="Control"            # the control (GM) role; created if missing
GAME_STARTER="Game Starter"  # may run /startinitialization, as may Discord admins
```

# Discord setup
ADAMA uses slash commands. Each Discord server it's in runs its own independent game.

The bot needs the **Administrator** permission and the **Server Members** and **Message Content** privileged intents (enable them in the Discord developer portal). It creates any permanent channels it needs that are missing (`#starting-games`, `#control-chat`, `#laws` and so on; see `src/adama/channels.py`).

To start a game, run `/startinitialization` in `#starting-games` with a player limit and the ship files from `shipdefs/` attached. See `shipdefs/README.md`.

# Operation
First you must install the ADAMA package to support module namespacing

`pip install -e .`

Then you can run the main.py entrypoint located in `src/adama/main.py`

While `DEBUG_MODE` in `src/adama/constants.py` is `True`, the game state is kept in memory only and is lost when the bot restarts. Set it to `False` to store it in the SQLite file at `DB_PATH`.

# Development
`make test` runs the tests (`pytest`) and `make lint` runs `ruff` and `mypy`.

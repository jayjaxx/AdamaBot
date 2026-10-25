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
# Read at /startgame. Crews use each ship's "<ship name> Crew" role.
PRESIDENT="President"
COUNCILLOR="Councillor"
FIRST_RESPONDER="First Responder"
PRESS="Press"              # may post in #newscasts
```

# Discord setup
ADAMA uses slash commands. Each Discord server it's in runs its own independent game.

The bot needs the **Administrator** permission and the **Server Members** and **Message Content** privileged intents (enable them in the Discord developer portal). It creates any permanent channels it needs that are missing (`#starting-games`, `#control-chat`, `#laws` and so on; see `src/adama/channels.py`).

To start a game, run `/startinitialization` in `#starting-games` with a player limit. With no ship files attached it loads the default fleet in `shipdefs/`; attach files to use your own ships instead. See `shipdefs/README.md`.

# Operation
First you must install the ADAMA package to support module namespacing

`pip install -e .`

Then you can run the main.py entrypoint located in `src/adama/main.py`

The game state is saved to the SQLite file at `DB_PATH` (set under `[database]` in `config.toml`), so a game survives restarts. Set `IN_MEMORY=true` there to keep it in memory only instead, which is handy for testing: every restart then starts from scratch.

# Development
`make test` runs the tests (`pytest`) and `make lint` runs `ruff` and `mypy`.

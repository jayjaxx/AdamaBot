PRAGMA foreign_keys = ON;

-- One game per Discord server. Each document is JSON in the same shape as the shipdefs.
CREATE TABLE IF NOT EXISTS galaxies (
	guild_id INTEGER PRIMARY KEY,
	data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ships (
	guild_id INTEGER NOT NULL REFERENCES galaxies(guild_id) ON DELETE CASCADE,
	ship_id TEXT NOT NULL,
	data TEXT NOT NULL,
	PRIMARY KEY (guild_id, ship_id)
);

CREATE TABLE IF NOT EXISTS players (
	guild_id INTEGER NOT NULL REFERENCES galaxies(guild_id) ON DELETE CASCADE,
	player_id TEXT NOT NULL,
	data TEXT NOT NULL,
	PRIMARY KEY (guild_id, player_id)
);

"""Offline checks of the Discord layer: no connection to Discord is made."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from adama.bot import AdamaBot, is_blind
from adama.config import DatabaseConfig, DiscordConfig, GlobalConfig
from adama.container import Container
from adama.db.database import connect
from adama.models import Galaxy, Player
from adama.shipfiles import ShipFile, validate_ship_files
from adama.status import (
	bridge_status,
	fleet_status,
	hangar_status,
	pda_status,
	split_message,
)

ROOT = Path(__file__).parent.parent

PLAYER_COMMANDS = {
	"ping",
	"roll",
	"status",
	"fleet",
	"control",
	"help",
	"gm",
	"travel",
	"locate",
}
GM_COMMANDS = {
	"startgame",
	"addplayer",
	"addteam",
	"removeteam",
	"teleportplayer",
	"watch",
	"unwatch",
	"startinitialization",
	"loadship",
	"unloadship",
	"playerlimit",
	"endgame",
	"gmhelp",
	"shutdown",
}


def make_bot() -> AdamaBot:
	config = GlobalConfig(discord=DiscordConfig(TOKEN=""), database=DatabaseConfig())
	return AdamaBot(config, container=Container(conn=connect(":memory:")))


async def load_cogs(bot: AdamaBot) -> None:
	for ext in bot.get_all_extensions():
		await bot.load_extension(ext)


def test_every_command_registers():
	bot = make_bot()
	asyncio.run(load_cogs(bot))
	commands = {c.name: c for c in bot.tree.get_commands()}
	assert set(commands) == PLAYER_COMMANDS | GM_COMMANDS
	for name in GM_COMMANDS:
		assert commands[name].extras.get("gm"), name
	for name in PLAYER_COMMANDS:
		assert not commands[name].extras.get("gm"), name


def test_loadship_takes_up_to_ten_files():
	bot = make_bot()
	asyncio.run(load_cogs(bot))
	loadship = bot.tree.get_command("loadship")
	assert isinstance(loadship, discord.app_commands.Command)
	files = [
		p
		for p in loadship.parameters
		if p.type == discord.AppCommandOptionType.attachment
	]
	assert len(files) == 10
	assert files[0].required and not any(p.required for p in files[1:])


def fake_interaction(channel, command: str | None = "status"):
	return SimpleNamespace(
		channel=channel, command=SimpleNamespace(name=command) if command else None
	)


def text_channel(name: str):
	channel = object.__new__(discord.TextChannel)
	channel.name = name
	return channel


def test_blind_channels():
	assert is_blind(fake_interaction(text_channel("general")))
	assert is_blind(fake_interaction(text_channel("pregame-chat")))
	assert is_blind(fake_interaction(text_channel("starting-games")))
	assert not is_blind(
		fake_interaction(text_channel("starting-games"), "startinitialization")
	)
	assert not is_blind(fake_interaction(text_channel("aegis-bridge")))
	assert is_blind(fake_interaction(object.__new__(discord.VoiceChannel)))


def full_galaxy() -> Galaxy:
	galaxy = Galaxy(1)
	files = [
		ShipFile(p.name, p.read_bytes())
		for p in sorted((ROOT / "shipdefs").glob("*.json"))
	]
	ships, _ = validate_ship_files(files, galaxy)
	for ship in ships:
		galaxy.add_ship(ship)
	return galaxy


def test_status_views_render_every_ship():
	galaxy = full_galaxy()
	galaxy.players["0001"] = Player(
		id="0001", discord_user_id=42, location="0100", crew_affiliations=["0100"]
	)
	galaxy.players["0002"] = Player(id="0002", discord_user_id=43, location="0100")
	for ship in galaxy.ships.values():
		bridge = bridge_status(galaxy, ship)
		assert ship.name in bridge
		assert all(len(chunk) <= 2000 for chunk in split_message(bridge))
	aegis = galaxy.ships["0100"]
	hangar = hangar_status(galaxy, aegis)
	assert "**Crew aboard:** <@42>" in hangar
	assert "**Visitors aboard:** <@43>" in hangar
	assert "Cyclops, Blackbird" in hangar
	assert "Aegis" in pda_status(galaxy, galaxy.players["0001"])


def test_bridge_status_never_shows_subbed_coordinates():
	galaxy = full_galaxy()
	ship = galaxy.ships["0100"]
	ship.next_coordinates = "11111"
	ship.sabotage_coordinates = "99999"
	text = bridge_status(galaxy, ship)
	assert "11111" in text
	assert "99999" not in text


def test_bridge_status_lists_station_modes():
	galaxy = full_galaxy()
	text = bridge_status(galaxy, galaxy.ships["0500"])
	assert "1. 10 fuel: 2 ore → 10 fuel" in text
	assert "3. 14 fuel: 4 ore → 14 fuel (-2 morale)" in text


def test_fleet_status_lists_every_ship():
	galaxy = full_galaxy()
	text = fleet_status(galaxy)
	for ship in galaxy.ships.values():
		assert ship.name in text
	assert "Council chambers: Dione" in text


def test_split_message():
	assert split_message("a\nb", limit=3) == ["a\nb"]
	assert split_message("aa\nbb", limit=3) == ["aa", "bb"]
	assert split_message("x" * 5, limit=2) == ["xx", "xx", "x"]


def test_stale_pda_owner_is_the_oldest_left_lobby():
	from adama.discord_ops import stale_pda_owner

	galaxy = Galaxy(1)
	galaxy.discord.pdas = {1: 10, 2: 20, 3: 30}
	galaxy.discord.admitted = [1, 3]
	assert stale_pda_owner(galaxy) == 2
	galaxy.discord.admitted = [1, 2, 3]
	assert stale_pda_owner(galaxy) is None


def test_brig_is_cleared_on_init():
	from adama import channels as ch

	assert ch.SPECS[ch.BRIG].cleared_on_init
	assert ch.SPECS[ch.VC_BRIG].cleared_on_init
	assert not ch.SPECS[ch.PREGAME_CHAT].cleared_on_init


def test_dead_and_brigged_command_restrictions():
	from adama.bot import restricted_reason

	assert restricted_reason(False, False, "travel") is None
	assert restricted_reason(False, True, "status") is None
	assert restricted_reason(False, True, "travel") is not None
	assert restricted_reason(True, False, "control") is None
	assert restricted_reason(True, False, "status") is not None


def test_recognises_leftover_ship_and_pda_categories():
	from adama.discord_ops import is_pda_category, is_ship_category

	assert is_ship_category(
		"Refinery 124", {"refinery-124-hangar", "refinery-124-bridge"}
	)
	assert not is_ship_category("Refinery 124", {"refinery-124-hangar"})
	assert not is_ship_category("General", {"general"})
	assert is_pda_category("PDAs") and is_pda_category("PDAs 3")
	assert not is_pda_category("PDAs and more")

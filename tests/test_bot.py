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
	"load",
	"unload",
	"activate",
	"repair",
	"disperse",
	"setrationlevel",
	"moraleroll",
	"launch",
	"intercept",
	"clearance",
	"revoke",
	"jump",
	"abort",
	"setcoords",
	"checkcoords",
	"takecard",
	"leavecard",
	"givecard",
	"playcard",
	"summoncouncil",
	"relocate",
	"seal",
	"unseal",
	"permit",
	"gag",
	"observer",
	"removeobserver",
	"loadunit",
	"unloadunit",
}
GM_COMMANDS = {
	"addresource",
	"teleportresource",
	"damage",
	"undamage",
	"startriot",
	"clearriot",
	"teamphase",
	"endturn",
	"addmorale",
	"shipmoralemodifier",
	"fleetmoralemodifier",
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
	"shutdown",
	"subcoords",
	"clearsubbedcoords",
	"isolate",
	"unisolate",
	"shuttle",
	"setjumpdistance",
	"wound",
	"heal",
	"kill",
	"resurrect",
	"brig",
	"unbrig",
	"movebrig",
	"electpresident",
	"electcouncillor",
	"depose",
	"card",
	"destroyship",
	"teleportship",
	"teleportshuttle",
	"exhauststation",
	"refreshstation",
	"teleportunit",
	"damageunit",
	"ungm",
	"lockdown",
	"endlockdown",
	"transfercommand",
	"setfirstresponder",
	"unsetfirstresponder",
	"setwolf",
	"unsetwolf",
	"wolves",
	"unit",
	"station",
	"ship",
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
	# Discord allows a bot at most 100 top-level slash commands.
	assert len(commands) <= 100


def test_control_commands_are_hidden_from_players():
	from adama.bot import hide_from_players
	from adama.perms import GM_COMMAND_PERMISSION

	bot = make_bot()
	asyncio.run(load_cogs(bot))
	for command in bot.tree.get_commands():
		hide_from_players(command)
		hidden = command.default_permissions == GM_COMMAND_PERMISSION
		assert hidden == (command.name in GM_COMMANDS), command.name


def test_help_lists():
	bot = make_bot()
	asyncio.run(load_cogs(bot))
	help_list = bot.get_cog("GeneralCommands").command_list
	text = help_list("players")
	assert text.startswith("**ADAMA commands**\n")
	assert "`/jump` *(bridge, crew only)*" in text
	assert "`/load` *(hangar or bridge, crew only)*" in text
	assert "`/travel` *(hangar or bridge)*" in text
	assert "`/clearance` *(hangar or bridge, crew only)*" in text
	assert "`/roll` Roll" in text
	assert "summoncouncil" not in text and "President" not in text
	assert "/endturn" not in text and "/help" in text

	cog = bot.get_cog("GeneralCommands")
	president = Player(id="0001", discord_user_id=1, president=True)
	nobody = SimpleNamespace(roles=[])
	assert cog.offices_held(president, nobody) == {"president"}
	# Before /startgame (or with the role given by hand) the Discord role counts.
	responder_role = SimpleNamespace(roles=[SimpleNamespace(name="First Responder")])
	assert cog.offices_held(None, responder_role) == {"first_responder"}
	assert cog.offices_held(None, nobody) == set()
	text = help_list("players", {"president"})
	assert "\n\n**President commands**\n`/gag` stop a permitted player" in text
	assert text.count("summoncouncil") == 1

	office = help_list("president")
	assert office.startswith("**President commands**") and "/roll" not in office
	control = help_list("control")
	assert control.startswith("**Control commands**") and "/endturn" in control
	assert "/roll" not in control and "/endturn" in control
	editing = control.split("**Control editing commands**")[1]
	assert "/card conjure" in editing and "/ship conjure" in editing
	assert "/teleportship" in control.split("**Control editing commands**")[0]
	assert "/endturn" not in editing
	everything = help_list("all")
	headers = [line for line in everything.splitlines() if line.startswith("**")]
	assert headers == [
		"**ADAMA commands**",
		"**President commands**",
		"**First Responder commands**",
		"**Control commands**",
		"**Control editing commands**",
	]


def test_groups_are_control_only():
	bot = make_bot()
	asyncio.run(load_cogs(bot))
	shuttle = bot.tree.get_command("shuttle")
	assert isinstance(shuttle, discord.app_commands.Group)
	assert {c.name for c in shuttle.commands} == {"conjure", "destroy"}
	unit = bot.tree.get_command("unit")
	assert isinstance(unit, discord.app_commands.Group)
	assert "teleport" not in {c.name for c in unit.commands}
	for group in ("shuttle", "card", "unit", "station", "ship"):
		command = bot.tree.get_command(group)
		assert isinstance(command, discord.app_commands.Group)
		for sub in command.commands:
			assert sub.checks, sub.qualified_name


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
	assert not is_blind(fake_interaction(text_channel("starting-games"), "gm"))
	assert not is_blind(fake_interaction(text_channel("starting-games"), "shutdown"))
	assert not is_blind(fake_interaction(text_channel("aegis-bridge")))
	assert is_blind(fake_interaction(object.__new__(discord.VoiceChannel)))


def test_refusing_autocomplete_sends_no_message():
	from adama.bot import refuse

	sent: list = []

	async def autocomplete(choices):
		sent.append(("choices", choices))

	async def send_message(*args, **kwargs):
		sent.append(("message", args))

	response = SimpleNamespace(
		autocomplete=autocomplete,
		send_message=send_message,
		is_done=lambda: False,
	)
	interaction = SimpleNamespace(
		type=discord.InteractionType.autocomplete, response=response
	)
	asyncio.run(refuse(interaction, "no"))
	assert sent == [("choices", [])]
	interaction.type = discord.InteractionType.application_command
	asyncio.run(refuse(interaction, "no"))
	assert sent[-1] == ("message", ("no",))


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
	assert "- Cyclops: " in hangar and "- Blackbird: " in hangar
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
	assert "resources: 8 food, 8 fuel" in text
	assert "`0126` Jump Drive ❌" in text
	galaxy.ships["0100"].stations["0126"].exhausted = True
	assert "`0126` Jump Drive ✅" in fleet_status(galaxy)


def test_only_open_calls_can_be_taken():
	from adama.discord_ops import CALL_EMOJI, TAKEN_BY, is_open_call

	def message(author: int, saluted: bool, content: str = "call", embeds=()):
		reaction = SimpleNamespace(me=saluted, emoji=CALL_EMOJI)
		return SimpleNamespace(
			author=SimpleNamespace(id=author),
			reactions=[reaction],
			content=content,
			embeds=list(embeds),
		)

	assert is_open_call(message(1, True), bot_id=1)
	assert not is_open_call(message(2, True), bot_id=1)  # not the bot's
	assert not is_open_call(message(1, False), bot_id=1)  # a log, not a call
	assert not is_open_call(message(1, True, f"call\n{TAKEN_BY}<@5>"), bot_id=1)
	assert not is_open_call(message(1, True, embeds=[object()]), bot_id=1)


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


def test_default_fleet_loads_when_no_files_are_attached():
	from adama.cogs.game import default_ship_files

	files = default_ship_files()
	assert len(files) == 11
	ships, problems = validate_ship_files(files, Galaxy(1))
	assert not problems and len(ships) == 11


def test_offices_can_be_set_in_the_lobby(monkeypatch):
	import sys

	import pytest

	from adama import discord_ops as ops
	from adama.models.galaxy import STATE_INITIALIZING
	from adama.perms import UserError

	bot = make_bot()
	asyncio.run(load_cogs(bot))
	cog = bot.get_cog("CharacterCommands")
	galaxy = Galaxy(1)
	galaxy.state = STATE_INITIALIZING
	member = SimpleNamespace(id=42, mention="<@42>", roles=[])
	changed: list = []

	async def set_member_role(m, name, given):
		changed.append((name, given))
		if given:
			m.roles.append(SimpleNamespace(name=name))
		else:
			m.roles = [r for r in m.roles if r.name != name]

	async def fake_reply(*args, **kwargs):
		pass

	monkeypatch.setattr(ops, "set_member_role", set_member_role)
	monkeypatch.setattr(sys.modules[type(cog).__module__], "reply", fake_reply)

	def run(offices, given):
		return asyncio.run(
			cog._lobby_offices(None, galaxy, member, offices, given, "done")
		)

	with pytest.raises(UserError, match="isn't in the pregame lobby"):
		run(["president"], True)
	galaxy.discord.admitted.append(42)
	assert run(["president"], True) and changed == [("President", True)]
	with pytest.raises(UserError, match="already has the President role"):
		run(["president"], True)
	assert run(["president", "councillor"], False)
	assert ("President", False) in changed and not member.roles
	with pytest.raises(UserError, match="doesn't hold"):
		run(["councillor"], False)
	galaxy.state = "in progress"
	assert not run(["president"], True)  # the game changes the player instead


def test_only_starting_games_gets_out_of_game_permissions():
	from adama import channels as ch
	from adama.discord_ops import _overwrites

	assert ch.SPECS[ch.CAT_OUT_OF_GAME].visibility == ch.SERVER_DEFAULT
	assert ch.SPECS[ch.STARTING_GAMES].visibility == ch.PUBLIC_READ
	guild = SimpleNamespace(default_role="everyone")
	assert _overwrites(guild, ch.SERVER_DEFAULT, "control") == {}
	assert set(_overwrites(guild, ch.PUBLIC_READ, "control")) == {"everyone", "control"}


def test_only_status_and_control_work_after_the_game_ends():
	from adama.bot import ENDED_COMMANDS

	assert {"status", "control", "startinitialization"} <= ENDED_COMMANDS
	assert not {"travel", "endturn", "help", "lockdown"} & ENDED_COMMANDS


def test_shutdown_pings_everyone_only_during_a_game():
	from adama.bot import shutdown_alert
	from adama.models.galaxy import (
		STATE_ENDED,
		STATE_IDLE,
		STATE_IN_PROGRESS,
		STATE_INITIALIZING,
	)

	for state in (STATE_INITIALIZING, STATE_IN_PROGRESS):
		assert shutdown_alert(Galaxy(1, state=state)).startswith("@everyone ")
	for state in (STATE_IDLE, STATE_ENDED):
		text = shutdown_alert(Galaxy(1, state=state))
		assert "@everyone" not in text and "shutting down" in text


def test_every_call_links_to_where_the_problem_is():
	"""Each claimable control alert in the rules names the channel(s) it's about."""
	import ast

	rules = ROOT / "src" / "adama" / "rules"
	missing = []
	for path in rules.glob("*.py"):
		for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
			if not (
				isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Notice"
			):
				continue
			names = {k.arg for k in node.keywords}
			if "claim" in names and "links" not in names:
				missing.append(f"{path.name}:{node.lineno}")
	assert not missing


def test_link_line_turns_links_into_channel_mentions():
	from adama import channels as ch
	from adama.discord_ops import channel_link, link_line
	from adama.rules import BRIDGE, COMBAT, CONTROL, COUNCIL, PDA, Notice

	galaxy = Galaxy(1)
	galaxy.players["0001"] = Player(id="0001", discord_user_id=9, pda_channel_id=31)
	galaxy.discord.ship_channels["0100"] = {ch.SHIP_BRIDGE: 32}
	galaxy.discord.channels[ch.COUNCIL_CHAMBERS] = 33
	galaxy.get_or_create_coordinates("12903").combat_text_channel_id = 34
	guild = SimpleNamespace(
		get_channel=lambda id: SimpleNamespace(mention=f"<#{id}>") if id else None
	)
	notice = Notice(
		CONTROL,
		"call",
		claim=True,
		links=(
			(PDA, "0001"),
			(BRIDGE, "0100"),
			(COUNCIL, ""),
			(COMBAT, "12903"),
			(PDA, "0001"),
		),
	)
	assert link_line(guild, galaxy, notice) == "\n📍 <#31> <#32> <#33> <#34>"
	assert link_line(guild, galaxy, Notice(CONTROL, "log")) == ""
	assert channel_link(SimpleNamespace(mention="<#5>")) == "\n📍 <#5>"
	assert channel_link(None) == ""


def test_back_online_alert_only_during_a_game():
	from adama.bot import back_online_alert
	from adama.models.galaxy import (
		STATE_ENDED,
		STATE_IDLE,
		STATE_IN_PROGRESS,
		STATE_INITIALIZING,
	)

	for state in (STATE_INITIALIZING, STATE_IN_PROGRESS):
		text = back_online_alert(Galaxy(1, state=state), False)
		assert text is not None and text.startswith("@everyone **ADAMA is back online")
		assert "cancelled" not in text
	text = back_online_alert(Galaxy(1, state=STATE_IN_PROGRESS), True)
	assert text is not None and "cancelled" in text
	for state in (STATE_IDLE, STATE_ENDED):
		assert back_online_alert(Galaxy(1, state=state), True) is None

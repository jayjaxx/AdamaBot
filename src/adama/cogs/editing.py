import logging

import discord
from discord import app_commands
from discord.ext import commands

from adama import discord_ops as ops
from adama.bot import AdamaBot, reply
from adama.cogs._shared import choices, ship_named
from adama.models.galaxy import Galaxy
from adama.models.resources import RESOURCE_NAMES
from adama.models.ship import DISTANCES, RATION_LEVELS
from adama.perms import UserError, control_only
from adama.rules import Notice
from adama.rules import editing
from adama.rules import jumps as jump_rules

logger = logging.getLogger()

GM = {"gm": True}


def _choices(values) -> list[app_commands.Choice[str]]:
	return [app_commands.Choice(name=v, value=v) for v in values]


IN_OUT = _choices(("in", "out"))
CALL_GM = [
	app_commands.Choice(name="damaged: alert when damaged", value="damaged"),
	app_commands.Choice(name="used: alert when used", value="used"),
	app_commands.Choice(name="any: alert when used or damaged", value="any"),
	app_commands.Choice(name="never", value="never"),
]
FLAGS = [
	app_commands.Choice(name="usable: can be activated at all", value="usable"),
	app_commands.Choice(
		name="reusable: can be activated more than once a turn", value="reusable"
	),
	app_commands.Choice(
		name="startofturn: bridge is reminded each turn", value="startofturn"
	),
]
MODE_ACTIONS = [
	app_commands.Choice(name="in: set an input", value="in"),
	app_commands.Choice(name="out: set an output", value="out"),
	app_commands.Choice(name="morale: set the morale roll modifier", value="morale"),
	app_commands.Choice(name="remove: delete the mode", value="remove"),
]
RESOURCES = _choices(RESOURCE_NAMES)
RATIONS = _choices(RATION_LEVELS)
FOOD_WATER = _choices(("food", "water"))
DISTANCE_CHOICES = _choices(DISTANCES)


class EditingCommands(commands.Cog):
	"""Control's /station and /ship editing commands."""

	station = app_commands.Group(
		name="station", description="Control: create and edit stations", extras=GM
	)
	ship = app_commands.Group(
		name="ship", description="Control: create, destroy and edit ships", extras=GM
	)

	def __init__(self, bot: AdamaBot):
		self.bot = bot
		self.galaxies = bot.container.services.galaxy
		self.roles = bot.config.roles

	def galaxy(self, interaction: discord.Interaction) -> Galaxy:
		assert interaction.guild is not None
		return self.galaxies.get(interaction.guild.id)

	async def finish(
		self,
		interaction: discord.Interaction,
		galaxy: Galaxy,
		notices: list[Notice],
		done: str,
	) -> None:
		guild = interaction.guild
		assert guild is not None
		self.galaxies.save(galaxy)
		if not interaction.response.is_done():
			await interaction.response.defer(ephemeral=True)
		await ops.post_notices(guild, galaxy, notices, self.roles)
		if galaxy.started:
			await ops.update_fleet_status(guild, galaxy)
		await reply(interaction, done, ephemeral=True)

	async def edit(self, interaction: discord.Interaction, change, *args) -> None:
		"""Runs a rules function returning either text or (text, notices)."""
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			result = change(galaxy, *args)
			text, notices = result if isinstance(result, tuple) else (result, [])
			await self.finish(interaction, galaxy, notices, text)

	# Autocomplete

	async def ship_autocomplete(self, interaction: discord.Interaction, current: str):
		galaxy = self.galaxy(interaction)
		return choices([(s.name, s.name) for s in galaxy.ships.values()], current)

	async def station_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		galaxy = self.galaxy(interaction)
		return choices(
			[
				(f"{s.id} {s.name} ({ship.name})", s.id)
				for ship, s in galaxy.iter_stations()
			],
			current,
		)

	async def mode_autocomplete(self, interaction: discord.Interaction, current: str):
		station_id = getattr(interaction.namespace, "station", None)
		galaxy = self.galaxy(interaction)
		modes = [
			m.name
			for _, s in galaxy.iter_stations()
			if s.id == station_id
			for m in s.modes
		]
		return choices([(m, m) for m in modes], current)

	# /station

	@station.command(name="conjure", description="Create a new station on a ship")
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def station_conjure(
		self, interaction: discord.Interaction, ship_name: str, name: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_named(galaxy, ship_name)
			created = editing.conjure_station(galaxy, ship, name)
			await self.finish(
				interaction,
				galaxy,
				[],
				f"Conjured `{created.id}` {created.name} on the {ship.name}. Set it up "
				"with /station io, description, flag and the other /station commands.",
			)

	@station.command(
		name="io",
		description="Set a station input or output (a non-resource input is its crew)",
	)
	@app_commands.describe(
		num="0 removes it",
		what="A resource name, or for an input a crew keyword (e.g. team, pilot)",
	)
	@app_commands.choices(direction=IN_OUT)
	@app_commands.autocomplete(station=station_autocomplete)
	@control_only()
	async def station_io(
		self,
		interaction: discord.Interaction,
		station: str,
		direction: str,
		num: int,
		what: str,
	):
		await self.edit(interaction, editing.set_io, station, direction, num, what)

	@station.command(name="description", description="Set a station's description")
	@app_commands.autocomplete(station=station_autocomplete)
	@control_only()
	async def station_description(
		self, interaction: discord.Interaction, station: str, description: str
	):
		await self.edit(
			interaction, editing.set_description, station, description, False
		)

	@station.command(
		name="damageddescription",
		description="Set what a station's damage does (shown when it is damaged)",
	)
	@app_commands.autocomplete(station=station_autocomplete)
	@control_only()
	async def station_damageddescription(
		self, interaction: discord.Interaction, station: str, description: str
	):
		await self.edit(
			interaction, editing.set_description, station, description, True
		)

	@station.command(
		name="repaircost",
		description="Set a station's repair cost (a non-resource is the repair crew)",
	)
	@app_commands.describe(num="0 removes it", what="A resource name or crew keyword")
	@app_commands.autocomplete(station=station_autocomplete)
	@control_only()
	async def station_repaircost(
		self, interaction: discord.Interaction, station: str, num: int, what: str
	):
		await self.edit(interaction, editing.set_repair_cost, station, num, what)

	@station.command(
		name="callgm", description="Set when control is alerted about a station"
	)
	@app_commands.choices(when=CALL_GM)
	@app_commands.autocomplete(station=station_autocomplete)
	@control_only()
	async def station_callgm(
		self, interaction: discord.Interaction, station: str, when: str
	):
		await self.edit(interaction, editing.set_call_gm, station, when)

	@station.command(name="flag", description="Set one of a station's behaviour flags")
	@app_commands.choices(flag=FLAGS)
	@app_commands.autocomplete(station=station_autocomplete)
	@control_only()
	async def station_flag(
		self, interaction: discord.Interaction, station: str, flag: str, value: bool
	):
		await self.edit(interaction, editing.set_flag, station, flag, value)

	@station.command(
		name="mode",
		description="Add or edit one of a station's modes (choices of output)",
	)
	@app_commands.describe(
		mode="The mode's name; a new name creates it",
		num="Not needed for remove",
		resource="Needed for in and out",
	)
	@app_commands.choices(action=MODE_ACTIONS, resource=RESOURCES)
	@app_commands.autocomplete(station=station_autocomplete, mode=mode_autocomplete)
	@control_only()
	async def station_mode(
		self,
		interaction: discord.Interaction,
		station: str,
		mode: str,
		action: str,
		num: int | None = None,
		resource: str | None = None,
	):
		await self.edit(
			interaction, editing.set_mode, station, mode, action, num, resource
		)

	@station.command(name="destroy", description="Destroy a station")
	@app_commands.autocomplete(station=station_autocomplete)
	@control_only()
	async def station_destroy(self, interaction: discord.Interaction, station: str):
		await self.edit(interaction, editing.destroy_station, station)

	# /ship

	@ship.command(
		name="conjure",
		description="Create a new empty ship with its channels and crew role",
	)
	@app_commands.describe(coordinates="5 digits, e.g. 12903")
	@control_only()
	async def ship_conjure(
		self, interaction: discord.Interaction, name: str, coordinates: str
	):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			control = ops.get_control_role(guild, self.roles)
			if control is None:
				raise UserError("There is no control role.")
			created = editing.conjure_ship(galaxy, name, coordinates)
			await interaction.response.defer(ephemeral=True)
			await ops.create_ship_channels(guild, galaxy, created, control)
			await self.finish(
				interaction,
				galaxy,
				[],
				f"Conjured the {created.name} (`{created.id}`) at {created.coordinates}.",
			)

	@ship.command(
		name="teleport",
		description="Move a ship and everything aboard to any coordinates, without fuel",
	)
	@app_commands.describe(coordinates="5 digits, e.g. 12903")
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def ship_teleport(
		self, interaction: discord.Interaction, ship_name: str, coordinates: str
	):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			target = ship_named(galaxy, ship_name)
			moved = jump_rules.teleport_ship(galaxy, target, coordinates)
			await self.finish(
				interaction,
				galaxy,
				moved.notices,
				f"The {target.name} is now at {target.coordinates}.",
			)
			# First responders left behind in combat by a commanding ship.
			await ops.sync_players(guild, galaxy, moved.resync)

	@ship.command(
		name="destroy",
		description="Mark a ship destroyed (nothing is deleted: move people yourself)",
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def ship_destroy(self, interaction: discord.Interaction, ship_name: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			target = ship_named(galaxy, ship_name)
			notices = editing.destroy_ship(galaxy, target)
			await self.finish(
				interaction, galaxy, notices, f"The {target.name} has been destroyed."
			)

	@ship.command(
		name="setcombat",
		description="Make a ship a combat ship (lockdowns check it for first responders)",
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def ship_setcombat(self, interaction: discord.Interaction, ship_name: str):
		await self._combat_ship(interaction, ship_name, True)

	@ship.command(name="unsetcombat", description="Stop a ship being a combat ship")
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def ship_unsetcombat(self, interaction: discord.Interaction, ship_name: str):
		await self._combat_ship(interaction, ship_name, False)

	async def _combat_ship(
		self, interaction: discord.Interaction, ship_name: str, combat: bool
	) -> None:
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = editing.set_combat_ship(ship_named(galaxy, ship_name), combat)
			await self.finish(interaction, galaxy, [], text)

	@ship.command(
		name="rationconsumption",
		description="Set how much food or water a ship uses at one ration level",
	)
	@app_commands.describe(num="Leave out to make the level unavailable on this ship")
	@app_commands.choices(ration_level=RATIONS, resource=FOOD_WATER)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def ship_rationconsumption(
		self,
		interaction: discord.Interaction,
		ship_name: str,
		ration_level: str,
		resource: str,
		num: int | None = None,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = editing.set_ration_consumption(
				ship_named(galaxy, ship_name), ration_level, num, resource
			)
			await self.finish(interaction, galaxy, [], text)

	@ship.command(
		name="fuelconsumption",
		description="Set how much fuel a ship uses for a jump of some distance",
	)
	@app_commands.choices(distance=DISTANCE_CHOICES)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def ship_fuelconsumption(
		self,
		interaction: discord.Interaction,
		ship_name: str,
		distance: str,
		num: int,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = editing.set_fuel_consumption(
				ship_named(galaxy, ship_name), distance, num
			)
			await self.finish(interaction, galaxy, [], text)


async def setup(bot: AdamaBot):
	await bot.add_cog(EditingCommands(bot))

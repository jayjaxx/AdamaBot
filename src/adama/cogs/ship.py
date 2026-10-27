import logging
import random

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama import discord_ops as ops
from adama.bot import AdamaBot, reply
from adama.cogs._shared import (
	HANGAR_OR_BRIDGE,
	WHERE_BRIDGE,
	WHERE_SHIP,
	WHERE_SHIP_CREW,
	CrewPicker,
	choices,
	ship_for_channel,
	ship_here,
)
from adama.models.galaxy import Galaxy
from adama.models.resources import RESOURCE_NAMES
from adama.models.ship import RATION_LEVELS, Ship
from adama.models.station import Station
from adama.perms import UserError, control_only, is_gm
from adama.rules import Notice
from adama.rules import resources as res_rules
from adama.rules import stations as st_rules
from adama.rules import turns as turn_rules

logger = logging.getLogger()

GM = {"gm": True}
RESOURCE_CHOICES = [app_commands.Choice(name=r, value=r) for r in RESOURCE_NAMES]
RATION_CHOICES = [
	app_commands.Choice(name=level, value=level) for level in RATION_LEVELS
]
FOOD_WATER = [app_commands.Choice(name=r, value=r) for r in ("food", "water")]
DURATION = [
	app_commands.Choice(name="temporary (cleared at end of turn)", value="temp"),
	app_commands.Choice(name="permanent", value="perm"),
]


def station_label(station: Station) -> str:
	flags = [
		f
		for f, on in (
			("damaged", station.damaged),
			("riot", station.riot),
			("used", station.exhausted),
		)
		if on
	]
	return f"{station.id} {station.name}" + (f" ({', '.join(flags)})" if flags else "")


class ShipCommands(commands.Cog):
	"""Resources, stations and the turn loop."""

	def __init__(self, bot: AdamaBot):
		self.bot = bot
		self.container = bot.container
		self.galaxies = bot.container.services.galaxy
		self.roles = bot.config.roles
		self.rng = random.Random()

	def galaxy(self, interaction: discord.Interaction) -> Galaxy:
		assert interaction.guild is not None
		return self.galaxies.get(interaction.guild.id)

	def gm(self, interaction: discord.Interaction) -> bool:
		return is_gm(interaction.user, self.roles)

	def channel_ship(self, interaction: discord.Interaction) -> Ship | None:
		found = ship_for_channel(self.galaxy(interaction), interaction.channel_id)
		return found[0] if found else None

	async def finish(
		self,
		interaction: discord.Interaction,
		galaxy: Galaxy,
		notices: list[Notice],
		done: str,
		*,
		fleet: bool = True,
	) -> None:
		"""Saves, posts the notices, refreshes #fleet-status and confirms to the user."""
		assert interaction.guild is not None
		self.galaxies.save(galaxy)
		if not interaction.response.is_done():
			await interaction.response.defer(ephemeral=True)
		await ops.post_notices(interaction.guild, galaxy, notices)
		if fleet and galaxy.started:
			await ops.update_fleet_status(interaction.guild, galaxy)
		await reply(interaction, done, ephemeral=True)

	# Autocomplete

	async def station_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		ship = self.channel_ship(interaction)
		if ship is None:
			return []
		command = interaction.command.name if interaction.command else ""
		wanted = {
			"activate": lambda s: s.usable and not s.damaged,
			"repair": lambda s: s.damaged,
			"disperse": lambda s: s.riot,
		}.get(command, lambda s: True)
		return choices(
			[(station_label(s), s.id) for s in ship.stations.values() if wanted(s)],
			current,
		)

	async def mode_autocomplete(self, interaction: discord.Interaction, current: str):
		ship = self.channel_ship(interaction)
		station = (
			ship.stations.get(str(interaction.namespace.station)) if ship else None
		)
		if station is None:
			return []
		return choices(
			[(f"{n}. {m.name}", m.name) for n, m in enumerate(station.modes, start=1)],
			current,
		)

	async def on_foot_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		ship = self.channel_ship(interaction)
		if ship is None:
			return []
		units = [
			u for u in ship.units.values() if st_rules.is_on_foot(u) and not u.exhausted
		]
		return choices(
			[
				(f"{u.id} {u.name}" + (" (damaged)" if u.damaged else ""), u.id)
				for u in units
			],
			current,
		)

	async def shuttle_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		ship = self.channel_ship(interaction)
		if ship is None:
			return []
		return choices([(s.name, s.name) for s in ship.shuttles.values()], current)

	async def target_autocomplete(self, interaction: discord.Interaction, current: str):
		"""Ship names, plus the stations of the ship whose channel this is."""
		galaxy = self.galaxy(interaction)
		values = [(ship.name, ship.name) for ship in galaxy.ships.values()]
		here = self.channel_ship(interaction)
		if here is not None:
			values += [(station_label(s), s.id) for s in here.stations.values()]
		elif current:
			values += [(station_label(s), s.id) for _, s in galaxy.iter_stations()]
		return choices(values, current)

	async def station_id_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		galaxy = self.galaxy(interaction)
		here = self.channel_ship(interaction)
		stations = (
			list(here.stations.values())
			if here
			else [s for _, s in galaxy.iter_stations()]
		)
		return choices([(station_label(s), s.id) for s in stations], current)

	async def ship_or_shuttle_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		galaxy = self.galaxy(interaction)
		values = [(s.name, s.name) for s in galaxy.ships.values()]
		values += [
			(f"{sh.name} (shuttle at {s.name})", sh.name)
			for s, sh in galaxy.iter_shuttles()
		]
		return choices(values, current)

	async def ship_autocomplete(self, interaction: discord.Interaction, current: str):
		galaxy = self.galaxy(interaction)
		return choices([(s.name, s.name) for s in galaxy.ships.values()], current)

	# Resources

	@app_commands.command(
		name="load",
		extras=WHERE_SHIP_CREW,
		description="Load resources from your ship onto a docked shuttle",
	)
	@app_commands.describe(shuttle="Leave out if only one shuttle is docked")
	@app_commands.choices(resource=RESOURCE_CHOICES)
	@app_commands.autocomplete(shuttle=shuttle_autocomplete)
	async def load(
		self,
		interaction: discord.Interaction,
		amount: app_commands.Range[int, 1],
		resource: str,
		shuttle: str | None = None,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_here(
				galaxy,
				interaction,
				HANGAR_OR_BRIDGE,
				gm=self.gm(interaction),
				crew=True,
			)
			notices = res_rules.load(
				ship, shuttle, amount, resource, interaction.user.mention
			)
			await self.finish(interaction, galaxy, notices, "Loaded.")

	@app_commands.command(
		name="unload",
		extras=WHERE_SHIP,
		description="Unload resources from a docked shuttle onto this ship",
	)
	@app_commands.describe(shuttle="Leave out if only one shuttle is docked")
	@app_commands.choices(resource=RESOURCE_CHOICES)
	@app_commands.autocomplete(shuttle=shuttle_autocomplete)
	async def unload(
		self,
		interaction: discord.Interaction,
		amount: app_commands.Range[int, 1],
		resource: str,
		shuttle: str | None = None,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_here(
				galaxy,
				interaction,
				HANGAR_OR_BRIDGE,
				gm=self.gm(interaction),
				crew=False,
			)
			notices = res_rules.unload(
				ship, shuttle, amount, resource, interaction.user.mention
			)
			await self.finish(interaction, galaxy, notices, "Unloaded.")

	@app_commands.command(
		name="addresource",
		description="Add (or with a negative amount remove) resources",
		extras=GM,
	)
	@app_commands.describe(
		ship_or_shuttle="Leave out to use the ship whose channel this is",
		silent="Don't tell the ship's bridge about the change",
	)
	@app_commands.choices(resource=RESOURCE_CHOICES)
	@app_commands.autocomplete(ship_or_shuttle=ship_or_shuttle_autocomplete)
	@control_only()
	async def addresource(
		self,
		interaction: discord.Interaction,
		amount: int,
		resource: str,
		ship_or_shuttle: str | None = None,
		silent: bool = False,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			if ship_or_shuttle is None:
				here = self.channel_ship(interaction)
				if here is None:
					raise UserError(
						"Specify a ship or shuttle, or run this in a ship's channel."
					)
				ship_or_shuttle = here.name
			text, notices = res_rules.add_resource(
				galaxy, amount, resource, ship_or_shuttle
			)
			if silent:
				notices, text = [], f"{text} (silently: the bridge wasn't told)"
			await self.finish(interaction, galaxy, notices, text)

	@app_commands.command(
		name="teleportresource",
		description="Move resources between any ships or shuttles",
		extras=GM,
	)
	@app_commands.choices(resource=RESOURCE_CHOICES)
	@app_commands.autocomplete(
		origin=ship_or_shuttle_autocomplete, destination=ship_or_shuttle_autocomplete
	)
	@control_only()
	async def teleportresource(
		self,
		interaction: discord.Interaction,
		amount: app_commands.Range[int, 1],
		resource: str,
		origin: str,
		destination: str,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text, notices = res_rules.teleport_resource(
				galaxy, amount, resource, origin, destination
			)
			await self.finish(interaction, galaxy, notices, text)

	# Stations

	async def _with_crew(
		self,
		interaction: discord.Interaction,
		station_id: str,
		repair: bool,
		choose_crew: bool,
		run,
	) -> None:
		"""Runs `run(unit_ids)` now (crew picked automatically) or after a crew pick-list."""
		if not choose_crew:
			await run(interaction, None)
			return
		galaxy = self.galaxy(interaction)
		ship = ship_here(
			galaxy, interaction, ch.SHIP_BRIDGE, gm=self.gm(interaction), crew=True
		)
		station = st_rules.station_on(ship, station_id)
		cost = station.repair_cost if repair else station.input
		if cost.crew_amount == 0:
			await run(interaction, [])
			return
		units = st_rules.available_crew(ship, cost.crew_keyword, own_only=False)
		if len(units) < cost.crew_amount:
			raise UserError(
				f'Not enough available crew matching "{cost.crew_keyword}": need {cost.crew_amount}, have {len(units)}.'
			)
		picker = CrewPicker(interaction.user.id, units, cost.crew_amount, run, galaxy)
		await interaction.response.send_message(
			f"Choose {cost.crew_amount} crew for `{station.id}` {station.name}:",
			view=picker,
			ephemeral=True,
		)

	@app_commands.command(
		name="activate",
		extras=WHERE_BRIDGE,
		description="Activate one of your ship's stations",
	)
	@app_commands.describe(
		station="The station to activate",
		mode="For stations with a choice of output",
		choose_crew="Pick the crew yourself (otherwise untrained crew is used first)",
	)
	@app_commands.autocomplete(station=station_autocomplete, mode=mode_autocomplete)
	async def activate(
		self,
		interaction: discord.Interaction,
		station: str,
		mode: str | None = None,
		choose_crew: bool = False,
	):
		async def run(i: discord.Interaction, unit_ids: list[str] | None) -> None:
			assert i.guild is not None
			async with self.galaxies.lock(i.guild.id):
				galaxy = self.galaxy(i)
				ship = ship_here(galaxy, i, ch.SHIP_BRIDGE, gm=self.gm(i), crew=True)
				coords = galaxy.coordinates.get(ship.coordinates)
				notices = st_rules.activate(
					ship,
					station,
					mode,
					unit_ids,
					i.user.mention,
					late=galaxy.late_activations
					if coords is not None and coords.locked_down
					else None,
				)
				await self.finish(i, galaxy, notices, "Activated.")

		await self._with_crew(interaction, station, False, choose_crew, run)

	@app_commands.command(
		name="repair",
		extras=WHERE_BRIDGE,
		description="Repair a damaged station on your ship",
	)
	@app_commands.describe(
		choose_crew="Pick the crew yourself (otherwise untrained crew is used first)"
	)
	@app_commands.autocomplete(station=station_autocomplete)
	async def repair(
		self, interaction: discord.Interaction, station: str, choose_crew: bool = False
	):
		async def run(i: discord.Interaction, unit_ids: list[str] | None) -> None:
			assert i.guild is not None
			async with self.galaxies.lock(i.guild.id):
				galaxy = self.galaxy(i)
				ship = ship_here(galaxy, i, ch.SHIP_BRIDGE, gm=self.gm(i), crew=True)
				notices = st_rules.repair(ship, station, unit_ids, i.user.mention)
				await self.finish(i, galaxy, notices, "Repaired.")

		await self._with_crew(interaction, station, True, choose_crew, run)

	@app_commands.command(
		name="disperse",
		extras=WHERE_BRIDGE,
		description="Send an on-foot unit to break up a riot (1d6: 4+ succeeds)",
	)
	@app_commands.autocomplete(station=station_autocomplete, unit=on_foot_autocomplete)
	async def disperse(self, interaction: discord.Interaction, station: str, unit: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_here(
				galaxy, interaction, ch.SHIP_BRIDGE, gm=self.gm(interaction), crew=True
			)
			notices = st_rules.disperse(
				ship, station, unit, self.rng, interaction.user.mention
			)
			await self.finish(interaction, galaxy, notices, "Done.")

	@app_commands.command(
		name="damage",
		description="Damage a station, or a random one on a ship",
		extras=GM,
	)
	@app_commands.describe(target="A station ID, or a ship name for a random station")
	@app_commands.autocomplete(target=target_autocomplete)
	@control_only()
	async def damage(self, interaction: discord.Interaction, target: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			notices = st_rules.damage(galaxy, target, self.rng)
			await self.finish(interaction, galaxy, notices, "Damaged.")

	@app_commands.command(
		name="undamage",
		description="Repair a station for free (it isn't exhausted)",
		extras=GM,
	)
	@app_commands.autocomplete(station=station_id_autocomplete)
	@control_only()
	async def undamage(self, interaction: discord.Interaction, station: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			notices = st_rules.undamage(galaxy, station)
			await self.finish(interaction, galaxy, notices, "Repaired.")

	@app_commands.command(
		name="startriot",
		description="Start a riot in a station, or a random one on a ship",
		extras=GM,
	)
	@app_commands.describe(target="A station ID, or a ship name for a random station")
	@app_commands.autocomplete(target=target_autocomplete)
	@control_only()
	async def startriot(self, interaction: discord.Interaction, target: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			notices = st_rules.start_riot(galaxy, target, self.rng)
			await self.finish(interaction, galaxy, notices, "Riot started.")

	@app_commands.command(
		name="clearriot",
		description="End a riot in a station (it becomes exhausted)",
		extras=GM,
	)
	@app_commands.autocomplete(station=station_id_autocomplete)
	@control_only()
	async def clearriot(self, interaction: discord.Interaction, station: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			notices = st_rules.clear_riot(galaxy, station)
			await self.finish(interaction, galaxy, notices, "Riot cleared.")

	# Turn loop

	@app_commands.command(
		name="setrationlevel",
		extras=WHERE_BRIDGE,
		description="Set your ship's food or water rations for morale rolls",
	)
	@app_commands.choices(resource=FOOD_WATER, level=RATION_CHOICES)
	async def setrationlevel(
		self, interaction: discord.Interaction, resource: str, level: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_here(
				galaxy, interaction, ch.SHIP_BRIDGE, gm=self.gm(interaction), crew=True
			)
			notices = turn_rules.set_ration_level(ship, resource, level)
			await self.finish(interaction, galaxy, notices, "Rations set.")

	@app_commands.command(
		name="moraleroll",
		extras=WHERE_BRIDGE,
		description="Make your ship's morale roll for this turn",
	)
	@app_commands.describe(
		force="Control only: roll again even if the ship has already rolled this turn"
	)
	async def moraleroll(self, interaction: discord.Interaction, force: bool = False):
		assert interaction.guild is not None
		if force and not self.gm(interaction):
			raise UserError("Only control can force a morale roll.")
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			if not galaxy.started:
				raise UserError("The game hasn't started yet.")
			ship = ship_here(
				galaxy, interaction, ch.SHIP_BRIDGE, gm=self.gm(interaction), crew=True
			)
			_, notices = turn_rules.morale_roll(galaxy, ship, self.rng, force=force)
			await self.finish(interaction, galaxy, notices, "Morale roll made.")

	@app_commands.command(
		name="teamphase",
		description="Announce the team phase: everyone returns to their ships",
		extras=GM,
	)
	@control_only()
	async def teamphase(self, interaction: discord.Interaction):
		galaxy = self.galaxy(interaction)
		if not galaxy.started:
			raise UserError("No game in progress.")
		await self.finish(
			interaction,
			galaxy,
			turn_rules.team_phase(),
			"Team phase announced.",
			fleet=False,
		)

	@app_commands.command(
		name="endturn",
		description="End the turn: refresh stations, units and cards, clear temporary modifiers",
		extras=GM,
	)
	@control_only()
	async def endturn(self, interaction: discord.Interaction):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			result = turn_rules.end_turn(galaxy)
			if not result.ended:
				await self.finish(
					interaction,
					galaxy,
					result.notices,
					"Some ships haven't rolled for morale (see #control-chat). Run /endturn again to end the turn anyway.",
					fleet=False,
				)
				return
			await interaction.response.defer(ephemeral=True)
			if result.unsealed and galaxy.council_chambers_ship:
				for player in galaxy.players.values():
					if player.location == galaxy.council_chambers_ship:
						await ops.sync_player(guild, galaxy, player)
			await self.finish(
				interaction,
				galaxy,
				result.notices,
				f"Turn {galaxy.turn_number} has begun.",
			)

	@app_commands.command(
		name="addmorale",
		description="Change a ship's morale level (or every ship's, with 'fleet')",
		extras=GM,
	)
	@app_commands.describe(
		target="A ship name, or fleet for every ship",
		amount="Negative to remove morale",
	)
	@app_commands.autocomplete(target=ship_autocomplete)
	@control_only()
	async def addmorale(
		self, interaction: discord.Interaction, target: str, amount: int
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			notices = turn_rules.add_morale(galaxy, target, amount)
			await self.finish(interaction, galaxy, notices, "Morale changed.")

	@app_commands.command(
		name="shipmoralemodifier",
		description="Add a modifier to one ship's morale rolls",
		extras=GM,
	)
	@app_commands.choices(duration=DURATION)
	@app_commands.autocomplete(ship=ship_autocomplete)
	@control_only()
	async def shipmoralemodifier(
		self, interaction: discord.Interaction, ship: str, amount: int, duration: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = turn_rules.add_modifier(galaxy, ship, amount, duration)
			await self.finish(interaction, galaxy, [], text)

	@app_commands.command(
		name="fleetmoralemodifier",
		description="Add a modifier to every ship's morale rolls",
		extras=GM,
	)
	@app_commands.choices(duration=DURATION)
	@control_only()
	async def fleetmoralemodifier(
		self, interaction: discord.Interaction, amount: int, duration: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = turn_rules.add_modifier(galaxy, None, amount, duration)
			await self.finish(interaction, galaxy, [], text)


async def setup(bot: AdamaBot):
	cog = ShipCommands(bot)
	await bot.add_cog(cog)

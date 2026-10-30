import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama import discord_ops as ops
from adama.bot import AdamaBot, defer, reply
from adama.cogs._shared import (
	HANGAR_OR_BRIDGE,
	WHERE_BRIDGE,
	WHERE_SHIP,
	WHERE_SHIP_CREW,
	choices,
	player_of,
	ship_for_channel,
	ship_here,
	ship_named,
)
from adama.models.galaxy import Galaxy, Jump, Launch
from adama.models.ship import DISTANCES, Ship
from adama.perms import UserError, control_only, is_gm
from adama.rules import Notice
from adama.rules import jumps as jump_rules
from adama.rules import shuttles as shuttle_rules

logger = logging.getLogger()

GM = {"gm": True}
DISTANCE_CHOICES = [app_commands.Choice(name=d, value=d) for d in DISTANCES]


class TravelCommands(commands.Cog):
	"""Shuttles, jumps and isolation."""

	shuttle = app_commands.Group(
		name="shuttle",
		description="Control: create, move and destroy shuttles",
		extras=GM,
	)

	def __init__(self, bot: AdamaBot):
		self.bot = bot
		self.galaxies = bot.container.services.galaxy
		self.roles = bot.config.roles
		# Running countdowns; kept so they aren't garbage collected mid-sleep.
		self.countdowns: set[asyncio.Task] = set()

	def galaxy(self, interaction: discord.Interaction) -> Galaxy:
		assert interaction.guild is not None
		return self.galaxies.get(interaction.guild.id)

	def gm(self, interaction: discord.Interaction) -> bool:
		return is_gm(interaction.user, self.roles)

	def channel_ship(self, interaction: discord.Interaction) -> Ship | None:
		found = ship_for_channel(self.galaxy(interaction), interaction.channel_id)
		return found[0] if found else None

	def crew_ship(self, interaction: discord.Interaction) -> Ship:
		"""The ship whose hangar or bridge this is, run by its crew aboard (or control)."""
		galaxy = self.galaxy(interaction)
		found = ship_for_channel(galaxy, interaction.channel_id)
		kind = found[1] if found else ch.SHIP_HANGAR
		return ship_here(galaxy, interaction, kind, gm=self.gm(interaction), crew=True)

	async def finish(
		self,
		interaction: discord.Interaction,
		galaxy: Galaxy,
		notices: list[Notice],
		done: str,
		*,
		fleet: bool = False,
		ephemeral: bool = True,
	) -> None:
		"""Saves, posts the notices and confirms to the user."""
		assert interaction.guild is not None
		self.galaxies.save(galaxy)
		if not interaction.response.is_done():
			await defer(interaction, ephemeral=ephemeral)
		await ops.post_notices(interaction.guild, galaxy, notices)
		if fleet and galaxy.started:
			await ops.update_fleet_status(interaction.guild, galaxy)
		await reply(interaction, done, ephemeral=ephemeral)

	def start_countdown(self, coroutine) -> None:
		task = asyncio.create_task(coroutine)
		self.countdowns.add(task)
		task.add_done_callback(self.countdowns.discard)

	async def cog_unload(self) -> None:
		for task in self.countdowns:
			task.cancel()

	# Autocomplete

	async def docked_autocomplete(self, interaction: discord.Interaction, current: str):
		ship = self.channel_ship(interaction)
		if ship is None:
			return []
		return choices([(s.name, s.name) for s in ship.shuttles.values()], current)

	async def destination_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		"""Other ships at the coordinates of the ship whose channel this is."""
		galaxy = self.galaxy(interaction)
		here = self.channel_ship(interaction)
		ships = galaxy.ships_at(here.coordinates) if here else []
		return choices(
			[(s.name, s.name) for s in ships if here is None or s.id != here.id],
			current,
		)

	async def ship_autocomplete(self, interaction: discord.Interaction, current: str):
		galaxy = self.galaxy(interaction)
		return choices([(s.name, s.name) for s in galaxy.ships.values()], current)

	async def any_shuttle_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		galaxy = self.galaxy(interaction)
		return choices(
			[(f"{sh.name} (at {s.name})", sh.name) for s, sh in galaxy.iter_shuttles()],
			current,
		)

	# Shuttles

	@app_commands.command(
		name="launch",
		extras=WHERE_SHIP,
		description="Fly a docked shuttle (and its cargo) to another ship at your coordinates",
	)
	@app_commands.describe(shuttle="Leave out if only one shuttle is docked")
	@app_commands.autocomplete(
		shuttle=docked_autocomplete, destination=destination_autocomplete
	)
	async def launch(
		self,
		interaction: discord.Interaction,
		destination: str,
		shuttle: str | None = None,
	):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_here(
				galaxy, interaction, HANGAR_OR_BRIDGE, gm=False, crew=False
			)
			player = player_of(galaxy, interaction.user)
			started, notices = shuttle_rules.launch(
				galaxy, player, ship, shuttle, destination
			)
			await self.finish(
				interaction,
				galaxy,
				notices,
				f"Launching in {shuttle_rules.LAUNCH_SECONDS} seconds.",
			)
		self.start_countdown(self._complete_launch(guild, started))

	async def _complete_launch(self, guild: discord.Guild, started: Launch) -> None:
		await asyncio.sleep(shuttle_rules.LAUNCH_SECONDS)
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			notices = shuttle_rules.complete_launch(galaxy, started)
			if not notices:
				return
			self.galaxies.save(galaxy)
			await ops.post_notices(guild, galaxy, notices)
			player = galaxy.players.get(started.player_id)
			if player is not None and player.location == started.destination_id:
				await ops.sync_player(guild, galaxy, player, travelled=True)
			await ops.update_fleet_status(guild, galaxy)

	@app_commands.command(
		name="intercept",
		extras=WHERE_SHIP,
		description="Stop a docked shuttle launching until it is given clearance",
	)
	@app_commands.describe(shuttle="Leave out if only one shuttle is docked")
	@app_commands.autocomplete(shuttle=docked_autocomplete)
	async def intercept(
		self, interaction: discord.Interaction, shuttle: str | None = None
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			gm = self.gm(interaction)
			ship = ship_here(galaxy, interaction, HANGAR_OR_BRIDGE, gm=gm, crew=False)
			player = galaxy.player_by_user(interaction.user.id)
			if player is None and not gm:
				player = player_of(galaxy, interaction.user)
			notices = shuttle_rules.intercept(
				galaxy, player, ship, shuttle, by=interaction.user.mention
			)
			await self.finish(interaction, galaxy, notices, "Intercepted.")

	@app_commands.command(
		name="clearance",
		extras=WHERE_SHIP_CREW,
		description="Clear a docked shuttle for launch, or give a player clearance for it",
	)
	@app_commands.describe(
		shuttle="Leave out if only one shuttle is docked",
		player="Give this player clearance instead of clearing the shuttle itself",
	)
	@app_commands.autocomplete(shuttle=docked_autocomplete)
	async def clearance(
		self,
		interaction: discord.Interaction,
		shuttle: str | None = None,
		player: discord.Member | None = None,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = self.crew_ship(interaction)
			target = player_of(galaxy, player) if player else None
			notices = shuttle_rules.clearance(ship, shuttle, target)
			await self.finish(interaction, galaxy, notices, "Clearance given.")

	@app_commands.command(
		name="revoke",
		extras=WHERE_SHIP_CREW,
		description="Take away a shuttle's clearance, or all of a player's clearances",
	)
	@app_commands.describe(
		shuttle="Leave out if only one shuttle is docked",
		player="Revoke all of this player's clearances instead",
	)
	@app_commands.autocomplete(shuttle=docked_autocomplete)
	async def revoke(
		self,
		interaction: discord.Interaction,
		shuttle: str | None = None,
		player: discord.Member | None = None,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = self.crew_ship(interaction)
			if player is not None:
				if shuttle is not None:
					raise UserError("Give a shuttle or a player, not both.")
				notices = shuttle_rules.revoke_player(galaxy, player_of(galaxy, player))
			else:
				notices = shuttle_rules.revoke_shuttle(galaxy, ship, shuttle)
			await self.finish(interaction, galaxy, notices, "Clearance revoked.")

	@app_commands.command(
		name="teleportshuttle",
		description="Move a shuttle and its cargo to any ship",
		extras=GM,
	)
	@app_commands.autocomplete(
		shuttle=any_shuttle_autocomplete, destination=ship_autocomplete
	)
	@control_only()
	async def shuttle_teleport(
		self, interaction: discord.Interaction, shuttle: str, destination: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			dest = ship_named(galaxy, destination)
			notices = shuttle_rules.teleport_shuttle(galaxy, shuttle, dest)
			await self.finish(
				interaction,
				galaxy,
				notices,
				f"{shuttle} is now at the {dest.name}.",
				fleet=True,
			)

	@shuttle.command(name="conjure", description="Create a new, empty shuttle")
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def shuttle_conjure(
		self, interaction: discord.Interaction, name: str, ship_name: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_named(galaxy, ship_name)
			created = shuttle_rules.conjure_shuttle(galaxy, name, ship)
			await self.finish(
				interaction,
				galaxy,
				[],
				f"Conjured shuttle `{created.id}` {created.name} at the {ship.name}.",
				fleet=True,
			)

	@shuttle.command(
		name="destroy",
		description="Destroy a shuttle with all its resources and units",
	)
	@app_commands.autocomplete(shuttle=any_shuttle_autocomplete)
	@control_only()
	async def shuttle_destroy(self, interaction: discord.Interaction, shuttle: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			destroyed, notices = shuttle_rules.destroy_shuttle(galaxy, shuttle)
			await self.finish(
				interaction,
				galaxy,
				notices,
				f"Destroyed {destroyed.name}.",
				fleet=True,
			)

	# Jumps

	@app_commands.command(
		name="jump",
		extras=WHERE_BRIDGE,
		description=f"Start the {jump_rules.JUMP_SECONDS}-second jump countdown to the set coordinates",
	)
	async def jump(self, interaction: discord.Interaction):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_here(
				galaxy, interaction, ch.SHIP_BRIDGE, gm=self.gm(interaction), crew=True
			)
			try:
				started, notices = jump_rules.start_jump(galaxy, ship)
			except jump_rules.JumpRefused as refused:
				await ops.post_notices(guild, galaxy, refused.notices)
				raise
			await self.finish(interaction, galaxy, notices, "Jump countdown started.")
		self.start_countdown(self._complete_jump(guild, started))

	async def _complete_jump(self, guild: discord.Guild, started: Jump) -> None:
		await asyncio.sleep(jump_rules.JUMP_SECONDS)
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			moved = jump_rules.complete_jump(galaxy, started)
			if moved is None:
				return
			self.galaxies.save(galaxy)
			await ops.post_notices(guild, galaxy, moved.notices)
			await ops.sync_players(guild, galaxy, moved.resync)
			await ops.update_fleet_status(guild, galaxy)

	@app_commands.command(
		name="abort", extras=WHERE_BRIDGE, description="Stop your ship's jump countdown"
	)
	async def abort(self, interaction: discord.Interaction):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_here(
				galaxy, interaction, ch.SHIP_BRIDGE, gm=self.gm(interaction), crew=True
			)
			notices = jump_rules.abort_jump(galaxy, ship)
			await self.finish(interaction, galaxy, notices, "Jump aborted.")

	@app_commands.command(
		name="setcoords",
		extras=WHERE_BRIDGE,
		description="Set the coordinates of your ship's next jump",
	)
	@app_commands.describe(coordinates="5 digits, e.g. 12903")
	async def setcoords(self, interaction: discord.Interaction, coordinates: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_here(
				galaxy, interaction, ch.SHIP_BRIDGE, gm=self.gm(interaction), crew=True
			)
			notices = jump_rules.set_coords(ship, coordinates)
			await self.finish(interaction, galaxy, notices, "Coordinates set.")

	@app_commands.command(
		name="checkcoords",
		extras=WHERE_BRIDGE,
		description="Show your ship's set jump coordinates",
	)
	async def checkcoords(self, interaction: discord.Interaction):
		galaxy = self.galaxy(interaction)
		ship = ship_here(
			galaxy, interaction, ch.SHIP_BRIDGE, gm=self.gm(interaction), crew=True
		)
		await reply(interaction, jump_rules.check_coords(ship), ephemeral=True)

	@app_commands.command(
		name="subcoords",
		description="Secretly change where a ship's next jump goes (the crew isn't told)",
		extras=GM,
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def subcoords(
		self, interaction: discord.Interaction, ship_name: str, coordinates: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = jump_rules.sub_coords(ship_named(galaxy, ship_name), coordinates)
			await self.finish(interaction, galaxy, [], text)

	@app_commands.command(
		name="clearsubbedcoords",
		description="Remove a ship's subbed jump coordinates",
		extras=GM,
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def clearsubbedcoords(self, interaction: discord.Interaction, ship_name: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = jump_rules.clear_subbed_coords(ship_named(galaxy, ship_name))
			await self.finish(interaction, galaxy, [], text)

	@app_commands.command(
		name="setjumpdistance",
		description="Set whether jumping to some coordinates is short, medium or long (for every ship)",
		extras=GM,
	)
	@app_commands.describe(coordinates="5 digits, e.g. 12903")
	@app_commands.choices(distance=DISTANCE_CHOICES)
	@control_only()
	async def setjumpdistance(
		self, interaction: discord.Interaction, coordinates: str, distance: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = jump_rules.set_jump_distance(galaxy, coordinates, distance)
			await self.finish(interaction, galaxy, [], text)

	# Isolation

	@app_commands.command(
		name="isolate",
		description="Stop all travel, launches and jumps to and from a ship",
		extras=GM,
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def isolate(self, interaction: discord.Interaction, ship_name: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_named(galaxy, ship_name)
			notices = jump_rules.isolate(galaxy, ship)
			await self.finish(
				interaction,
				galaxy,
				notices,
				f"The {ship.name} is isolated.",
				fleet=True,
			)

	@app_commands.command(
		name="unisolate", description="Lift a ship's isolation", extras=GM
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def unisolate(self, interaction: discord.Interaction, ship_name: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_named(galaxy, ship_name)
			jump_rules.unisolate(ship)
			await self.finish(
				interaction,
				galaxy,
				[],
				f"The {ship.name} is no longer isolated.",
				fleet=True,
			)


async def setup(bot: AdamaBot):
	await bot.add_cog(TravelCommands(bot))

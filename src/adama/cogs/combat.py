import logging

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama import discord_ops as ops
from adama.bot import AdamaBot, reply
from adama.cogs._shared import (
	FIRST_RESPONDER_ONLY,
	WHERE_HANGAR_UNIT_CREW,
	choices,
	player_of,
	ship_for_channel,
	ship_here,
	ship_named,
)
from adama.models.galaxy import Galaxy
from adama.models.player import Player
from adama.models.ship import Ship
from adama.models.unit import Unit
from adama.perms import UserError, control_only, is_gm
from adama.rules import Notice
from adama.rules import combat as combat_rules
from adama.rules import editing
from adama.rules.stations import AEGIS

logger = logging.getLogger()

GM = {"gm": True}


def unit_label(galaxy: Galaxy, unit: Unit) -> str:
	owner = galaxy.ships.get(unit.affiliation)
	flags = [f for f, on in (("damaged", unit.damaged), ("used", unit.exhausted)) if on]
	label = f"{unit.id} {unit.name} ({owner.name if owner else '?'})"
	return label + (f" [{', '.join(flags)}]" if flags else "")


class CombatCommands(commands.Cog):
	"""Lockdowns, observers, first responders and units."""

	unit = app_commands.Group(
		name="unit", description="Control: create and edit units", extras=GM
	)

	def __init__(self, bot: AdamaBot):
		self.bot = bot
		self.galaxies = bot.container.services.galaxy
		self.roles = bot.config.roles

	def galaxy(self, interaction: discord.Interaction) -> Galaxy:
		assert interaction.guild is not None
		return self.galaxies.get(interaction.guild.id)

	def gm(self, interaction: discord.Interaction) -> bool:
		return is_gm(interaction.user, self.roles)

	async def finish(
		self,
		interaction: discord.Interaction,
		galaxy: Galaxy,
		notices: list[Notice],
		done: str,
		*,
		sync: list[Player] | None = None,
		fleet: bool = False,
	) -> None:
		"""Saves, posts the notices, updates access and confirms to the user."""
		guild = interaction.guild
		assert guild is not None
		self.galaxies.save(galaxy)
		if not interaction.response.is_done():
			await interaction.response.defer(ephemeral=True)
		await ops.post_notices(guild, galaxy, notices, self.roles)
		if sync:
			await ops.sync_players(guild, galaxy, sync)
		if fleet and galaxy.started:
			await ops.update_fleet_status(guild, galaxy)
		await reply(interaction, done, ephemeral=True)

	# Autocomplete

	async def ship_autocomplete(self, interaction: discord.Interaction, current: str):
		galaxy = self.galaxy(interaction)
		return choices([(s.name, s.name) for s in galaxy.ships.values()], current)

	async def coordinates_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		galaxy = self.galaxy(interaction)
		command = interaction.command.name if interaction.command else ""
		values = [
			(f"{c.coordinates} ({', '.join(s.name for s in ships)})", c.coordinates)
			for c in galaxy.coordinates.values()
			if (ships := galaxy.ships_at(c.coordinates))
			and c.locked_down == (command in ("endlockdown", "transfercommand"))
		]
		return choices(values, current)

	async def any_unit_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		galaxy = self.galaxy(interaction)
		return choices(
			[(unit_label(galaxy, u), u.id) for u in galaxy.iter_units()], current
		)

	def _channel_ship(self, interaction: discord.Interaction) -> Ship | None:
		found = ship_for_channel(self.galaxy(interaction), interaction.channel_id)
		return found[0] if found else None

	async def aboard_unit_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		galaxy, ship = self.galaxy(interaction), self._channel_ship(interaction)
		units = [u for u in ship.units.values() if not u.exhausted] if ship else []
		return choices([(unit_label(galaxy, u), u.id) for u in units], current)

	async def shuttle_unit_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		galaxy, ship = self.galaxy(interaction), self._channel_ship(interaction)
		values = [
			(f"{unit_label(galaxy, u)} on {shuttle.name}", u.id)
			for shuttle in (ship.shuttles.values() if ship else [])
			for u in shuttle.units.values()
		]
		return choices(values, current)

	async def docked_autocomplete(self, interaction: discord.Interaction, current: str):
		ship = self._channel_ship(interaction)
		shuttles = ship.shuttles.values() if ship else []
		return choices([(s.name, s.name) for s in shuttles], current)

	# Lockdowns

	@app_commands.command(
		name="lockdown",
		description="Call action stations: stop travel at some coordinates and open combat channels",
		extras=GM,
	)
	@app_commands.describe(
		coordinates="5 digits, e.g. 12903",
		commanding_ship="Its first responders aboard see the combat (default: Aegis)",
	)
	@app_commands.autocomplete(
		coordinates=coordinates_autocomplete, commanding_ship=ship_autocomplete
	)
	@control_only()
	async def lockdown(
		self,
		interaction: discord.Interaction,
		coordinates: str,
		commanding_ship: str | None = None,
	):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			control = ops.get_control_role(guild, self.roles)
			if control is None:
				raise UserError("There is no control role.")
			commanding = (
				ship_named(galaxy, commanding_ship)
				if commanding_ship
				else galaxy.ship_by_name(AEGIS)
			)
			coords, change = combat_rules.lockdown(galaxy, coordinates, commanding)
			await interaction.response.defer(ephemeral=True)
			text, voice = await ops.create_combat_channels(
				guild, galaxy, coords.coordinates, control
			)
			coords.combat_text_channel_id = text.id
			coords.combat_voice_channel_id = voice.id
			self.galaxies.save(galaxy)
			await ops.sync_players(guild, galaxy, change.resync)
			await self.finish(
				interaction,
				galaxy,
				change.notices,
				f"{coords.coordinates} is in lockdown: {text.mention}.",
				fleet=True,
			)

	@app_commands.command(
		name="endlockdown",
		description="End the lockdown at some coordinates, or every lockdown",
		extras=GM,
	)
	@app_commands.describe(coordinates="Leave out to end every lockdown")
	@app_commands.autocomplete(coordinates=coordinates_autocomplete)
	@control_only()
	async def endlockdown(
		self, interaction: discord.Interaction, coordinates: str | None = None
	):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			change = combat_rules.end_lockdown(galaxy, coordinates)
			await interaction.response.defer(ephemeral=True)
			# Deleting the combat VC disconnects everyone still in it.
			await ops.delete_channels(guild, change.deleted_channels)
			await self.finish(
				interaction,
				galaxy,
				change.notices,
				"Lockdown ended.",
				sync=change.resync,
				fleet=True,
			)

	@app_commands.command(
		name="transfercommand",
		description="Give command of a lockdown to another ship (e.g. after the commander jumped away)",
		extras=GM,
	)
	@app_commands.autocomplete(
		coordinates=coordinates_autocomplete, ship_name=ship_autocomplete
	)
	@control_only()
	async def transfercommand(
		self, interaction: discord.Interaction, coordinates: str, ship_name: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_named(galaxy, ship_name)
			text, change = combat_rules.transfer_command(galaxy, coordinates, ship)
			await self.finish(
				interaction,
				galaxy,
				change.notices,
				text,
				sync=change.resync,
				fleet=True,
			)

	# Observers

	@app_commands.command(
		name="observer",
		extras=FIRST_RESPONDER_ONLY,
		description="First Responder: let a player watch the combat channels (Aegis crew only)",
	)
	async def observer(self, interaction: discord.Interaction, player: discord.Member):
		await self._observer(interaction, player, True, "can now watch the battlemap")

	@app_commands.command(
		name="removeobserver",
		extras=FIRST_RESPONDER_ONLY,
		description="First Responder: stop a player watching the combat channels (Aegis crew only)",
	)
	async def removeobserver(
		self, interaction: discord.Interaction, player: discord.Member
	):
		await self._observer(
			interaction, player, False, "can no longer watch the battlemap"
		)

	async def _observer(
		self,
		interaction: discord.Interaction,
		member: discord.Member,
		observing: bool,
		done: str,
	) -> None:
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			by = None if self.gm(interaction) else player_of(galaxy, interaction.user)
			target = player_of(galaxy, member)
			notices = combat_rules.set_observer(galaxy, by, target, observing)
			await self.finish(
				interaction, galaxy, notices, f"{member.mention} {done}.", sync=[target]
			)

	# Units on shuttles

	def _hangar(self, interaction: discord.Interaction) -> tuple[Galaxy, Ship]:
		galaxy = self.galaxy(interaction)
		ship = ship_here(
			galaxy, interaction, ch.SHIP_HANGAR, gm=self.gm(interaction), crew=False
		)
		return galaxy, ship

	def _mover(self, interaction: discord.Interaction, galaxy: Galaxy) -> Player | None:
		return None if self.gm(interaction) else player_of(galaxy, interaction.user)

	@app_commands.command(
		name="loadunit",
		extras=WHERE_HANGAR_UNIT_CREW,
		description="Load a unit onto a docked shuttle",
	)
	@app_commands.describe(shuttle="Leave out if only one shuttle is docked")
	@app_commands.autocomplete(
		unit=aboard_unit_autocomplete, shuttle=docked_autocomplete
	)
	async def loadunit(
		self, interaction: discord.Interaction, unit: str, shuttle: str | None = None
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy, ship = self._hangar(interaction)
			notices = combat_rules.load_unit(
				galaxy, self._mover(interaction, galaxy), ship, unit, shuttle
			)
			await self.finish(interaction, galaxy, notices, "Unit loaded.", fleet=True)

	@app_commands.command(
		name="unloadunit",
		extras=WHERE_HANGAR_UNIT_CREW,
		description="Unload a unit from a docked shuttle onto this ship",
	)
	@app_commands.autocomplete(unit=shuttle_unit_autocomplete)
	async def unloadunit(self, interaction: discord.Interaction, unit: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy, ship = self._hangar(interaction)
			notices = combat_rules.unload_unit(
				galaxy, self._mover(interaction, galaxy), ship, unit
			)
			await self.finish(
				interaction, galaxy, notices, "Unit unloaded.", fleet=True
			)

	# /unit (control)

	async def _edit_unit(self, interaction: discord.Interaction, change, *args) -> None:
		"""Runs a rules function returning either text or (text, notices)."""
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			result = change(galaxy, *args)
			text, notices = result if isinstance(result, tuple) else (result, [])
			await self.finish(interaction, galaxy, notices, text, fleet=True)

	@unit.command(name="conjure", description="Create a new unit aboard a ship")
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def unit_conjure(
		self, interaction: discord.Interaction, name: str, ship_name: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_named(galaxy, ship_name)
			created = editing.conjure_unit(galaxy, name, ship)
			await self.finish(
				interaction,
				galaxy,
				[],
				f"Conjured `{created.id}` {created.name} aboard the {ship.name}.",
				fleet=True,
			)

	@unit.command(
		name="damage", description="Damage a unit (destroys it if already damaged)"
	)
	@app_commands.autocomplete(unit=any_unit_autocomplete)
	@control_only()
	async def unit_damage(self, interaction: discord.Interaction, unit: str):
		await self._edit_unit(interaction, editing.damage_unit, unit)

	@unit.command(name="heal", description="Repair a damaged unit")
	@app_commands.autocomplete(unit=any_unit_autocomplete)
	@control_only()
	async def unit_heal(self, interaction: discord.Interaction, unit: str):
		await self._edit_unit(interaction, editing.heal_unit, unit)

	@unit.command(
		name="defect", description="Change a unit's ship (not its ID or location)"
	)
	@app_commands.autocomplete(unit=any_unit_autocomplete, ship_name=ship_autocomplete)
	@control_only()
	async def unit_defect(
		self, interaction: discord.Interaction, unit: str, ship_name: str
	):
		assert interaction.guild is not None
		ship = ship_named(self.galaxy(interaction), ship_name)
		await self._edit_unit(interaction, editing.defect_unit, unit, ship)

	@unit.command(
		name="rename",
		description="Rename a unit (its name decides which crew keywords it matches)",
	)
	@app_commands.autocomplete(unit=any_unit_autocomplete)
	@control_only()
	async def unit_rename(self, interaction: discord.Interaction, unit: str, name: str):
		await self._edit_unit(interaction, editing.rename_unit, unit, name)

	@unit.command(name="exhaust", description="Mark a unit as used this turn")
	@app_commands.autocomplete(unit=any_unit_autocomplete)
	@control_only()
	async def unit_exhaust(self, interaction: discord.Interaction, unit: str):
		await self._edit_unit(interaction, editing.set_unit_exhausted, unit, True)

	@unit.command(name="refresh", description="Let a unit be used again this turn")
	@app_commands.autocomplete(unit=any_unit_autocomplete)
	@control_only()
	async def unit_refresh(self, interaction: discord.Interaction, unit: str):
		await self._edit_unit(interaction, editing.set_unit_exhausted, unit, False)


async def setup(bot: AdamaBot):
	await bot.add_cog(CombatCommands(bot))

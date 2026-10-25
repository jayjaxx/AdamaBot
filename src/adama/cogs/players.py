import logging

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama import discord_ops as ops
from adama.bot import AdamaBot, reply
from adama.models.galaxy import Galaxy
from adama.models.player import LOCATION_COMBAT, Player
from adama.models.ship import Ship
from adama.perms import UserError, control_only
from adama.rules import players as rules
from adama.rules.players import current_ship

logger = logging.getLogger()

GM = {"gm": True}
PDA_WATCH = discord.PermissionOverwrite(
	view_channel=True, send_messages=True, read_message_history=True
)


def player_of(galaxy: Galaxy, user: discord.abc.User) -> Player:
	player = galaxy.player_by_user(user.id)
	if player is None:
		raise UserError(f"{user.mention} isn't a player.")
	return player


def ship_named(galaxy: Galaxy, name: str) -> Ship:
	ship = galaxy.ship_by_name(name)
	if ship is None:
		raise UserError(f'There is no ship named "{name}".')
	return ship


def in_departure_channel(
	galaxy: Galaxy, player: Player, channel_id: int | None
) -> bool:
	"""Whether the command was run in the hangar of the player's ship, or (in combat) in
	their combat channel."""
	ship = current_ship(galaxy, player)
	if ship is None:
		return False
	if player.location == LOCATION_COMBAT:
		coords = galaxy.coordinates.get(ship.coordinates)
		return coords is not None and coords.combat_text_channel_id == channel_id
	return (
		galaxy.discord.ship_channels.get(ship.id, {}).get(ch.SHIP_HANGAR) == channel_id
	)


class PlayerCommands(commands.Cog):
	def __init__(self, bot: AdamaBot):
		self.bot = bot
		self.container = bot.container
		self.galaxies = bot.container.services.galaxy
		self.roles = bot.config.roles

	async def destination_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		assert interaction.guild is not None
		galaxy = self.galaxies.get(interaction.guild.id)
		names = [ship.name for ship in galaxy.ships.values() if not ship.destroyed] + [
			"combat"
		]
		return [
			app_commands.Choice(name=name, value=name)
			for name in names
			if current.casefold() in name.casefold()
		][:25]

	async def ship_autocomplete(self, interaction: discord.Interaction, current: str):
		assert interaction.guild is not None
		galaxy = self.galaxies.get(interaction.guild.id)
		return [
			app_commands.Choice(name=ship.name, value=ship.name)
			for ship in galaxy.ships.values()
			if current.casefold() in ship.name.casefold()
		][:25]

	@app_commands.command(
		name="travel",
		description="Move to another ship at your coordinates (or into combat)",
	)
	@app_commands.autocomplete(destination=destination_autocomplete)
	async def travel(self, interaction: discord.Interaction, destination: str):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			player = player_of(galaxy, interaction.user)
			if not in_departure_channel(galaxy, player, interaction.channel_id):
				raise UserError("Run /travel in the hangar of the ship you're aboard.")
			notices = rules.travel(galaxy, player, destination)
			await interaction.response.defer(ephemeral=True)
			self.galaxies.save(galaxy)
			await ops.post_notices(guild, galaxy, notices)
			await ops.sync_player(guild, galaxy, player, travelled=True)
		where = current_ship(galaxy, player)
		await reply(
			interaction,
			"You are in combat."
			if player.location == LOCATION_COMBAT
			else f"You are aboard the {where.name if where else '?'}.",
			ephemeral=True,
		)

	@app_commands.command(
		name="locate", description="Find which ship a player at your coordinates is on"
	)
	async def locate(self, interaction: discord.Interaction, player: discord.Member):
		galaxy = self.galaxies.get(interaction.guild.id) if interaction.guild else None
		assert galaxy is not None
		text = rules.locate(
			galaxy, player_of(galaxy, interaction.user), player_of(galaxy, player)
		)
		await reply(interaction, text, ephemeral=True)

	@app_commands.command(
		name="teleportplayer",
		description="Move a player anywhere, ignoring travel restrictions",
		extras=GM,
	)
	@app_commands.autocomplete(destination=destination_autocomplete)
	@control_only()
	async def teleportplayer(
		self, interaction: discord.Interaction, player: discord.Member, destination: str
	):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			target = player_of(galaxy, player)
			notices = rules.teleport(galaxy, target, destination)
			await interaction.response.defer(ephemeral=True)
			self.galaxies.save(galaxy)
			await ops.post_notices(guild, galaxy, notices)
			await ops.sync_player(guild, galaxy, target)
		await reply(
			interaction,
			f"Teleported {player.mention} to {destination}.",
			ephemeral=True,
		)

	@app_commands.command(
		name="addplayer",
		description="Add a player mid-game (no location until /addteam or /teleportplayer)",
		extras=GM,
	)
	@control_only()
	async def addplayer(self, interaction: discord.Interaction, user: discord.Member):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			player = rules.add_player(galaxy, user.id)
			await interaction.response.defer(ephemeral=True)
			pda = await ops.give_pda(guild, galaxy, user)
			player.pda_channel_id = pda.id
			self.galaxies.save(galaxy)
			await ops.sync_player(guild, galaxy, player)
			await pda.send(
				"You have joined the game. Control will place you on a ship."
			)
		await reply(
			interaction, f"{user.mention} is now player `{player.id}`.", ephemeral=True
		)

	@app_commands.command(
		name="addteam", description="Add a player to a ship's crew", extras=GM
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def addteam(
		self, interaction: discord.Interaction, player: discord.Member, ship_name: str
	):
		await self._change_team(interaction, player, ship_name, add=True)

	@app_commands.command(
		name="removeteam", description="Remove a player from a ship's crew", extras=GM
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def removeteam(
		self, interaction: discord.Interaction, player: discord.Member, ship_name: str
	):
		await self._change_team(interaction, player, ship_name, add=False)

	async def _change_team(
		self,
		interaction: discord.Interaction,
		member: discord.Member,
		ship_name: str,
		*,
		add: bool,
	):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			player = player_of(galaxy, member)
			ship = ship_named(galaxy, ship_name)
			change = rules.add_team if add else rules.remove_team
			notices = change(galaxy, player, ship)
			await interaction.response.defer(ephemeral=True)
			self.galaxies.save(galaxy)
			await ops.sync_crew_roles(guild, galaxy, player)
			await ops.sync_player(guild, galaxy, player)
			await ops.post_notices(guild, galaxy, notices)
		verb = "is now" if add else "is no longer"
		await reply(
			interaction,
			f"{member.mention} {verb} crew of the {ship.name}.",
			ephemeral=True,
		)

	@app_commands.command(
		name="watch",
		description="See a player's PDA (or every PDA if no player is given)",
		extras=GM,
	)
	@control_only()
	async def watch(
		self, interaction: discord.Interaction, player: discord.Member | None = None
	):
		await self._watch(interaction, player, watch=True)

	@app_commands.command(
		name="unwatch",
		description="Stop seeing a player's PDA (or every watched PDA)",
		extras=GM,
	)
	@control_only()
	async def unwatch(
		self, interaction: discord.Interaction, player: discord.Member | None = None
	):
		await self._watch(interaction, player, watch=False)

	async def _watch(
		self,
		interaction: discord.Interaction,
		member: discord.Member | None,
		*,
		watch: bool,
	):
		guild = interaction.guild
		gm = interaction.user
		assert guild is not None and isinstance(gm, discord.Member)
		galaxy = self.galaxies.get(guild.id)
		if member is not None:
			if member.id not in galaxy.discord.pdas:
				raise UserError(f"{member.mention} has no PDA.")
			pdas = {member.id: galaxy.discord.pdas[member.id]}
		else:
			pdas = dict(galaxy.discord.pdas)

		await interaction.response.defer(ephemeral=True)
		changed = []
		for channel_id in pdas.values():
			channel = guild.get_channel(channel_id)
			if channel is None:
				continue
			watching = not channel.overwrites_for(gm).is_empty()
			if watch and not watching:
				await channel.set_permissions(gm, overwrite=PDA_WATCH)
				changed.append(channel.mention)
			elif not watch and watching:
				await channel.set_permissions(gm, overwrite=None)
				changed.append(channel.mention)
		if member is not None and not changed:
			raise UserError(
				f"You are {'already' if watch else 'not'} watching {member.mention}'s PDA."
			)
		verb = "Now watching" if watch else "Stopped watching"
		await reply(
			interaction,
			f"{verb} {len(changed)} PDA(s): {', '.join(changed) or 'none'}.",
			ephemeral=True,
		)


async def setup(bot: AdamaBot):
	cog = PlayerCommands(bot)
	await bot.add_cog(cog)

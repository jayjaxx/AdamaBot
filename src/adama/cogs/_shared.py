"""Helpers shared by the cogs. The leading underscore stops it loading as a cog."""

import logging
from typing import Awaitable, Callable

import discord

from adama import channels as ch
from adama.bot import reply
from adama.models.galaxy import Galaxy
from adama.models.player import Player
from adama.models.ship import Ship
from adama.models.unit import Unit
from adama.perms import UserError
from adama.rules import GameError

logger = logging.getLogger(__name__)

# Discord shows at most 25 options in a pick-list or autocomplete.
MAX_CHOICES = 25


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


def ship_for_channel(galaxy: Galaxy, channel_id: int | None) -> tuple[Ship, str] | None:
	"""The ship and kind ("hangar" or "bridge") of a ship text channel."""
	for ship_id, created in galaxy.discord.ship_channels.items():
		for kind in (ch.SHIP_HANGAR, ch.SHIP_BRIDGE):
			if created.get(kind) == channel_id and ship_id in galaxy.ships:
				return galaxy.ships[ship_id], kind
	return None


def ship_here(
	galaxy: Galaxy, interaction: discord.Interaction, kind: str, *, gm: bool, crew: bool
) -> Ship:
	"""The ship whose hangar or bridge the command was run in, checking that the user is
	aboard it (and crew, if needed). Control may act on any ship from its channels."""
	found = ship_for_channel(galaxy, interaction.channel_id)
	if found is None or found[1] != kind:
		raise UserError(f"Run this in a ship's {kind} channel.")
	ship = found[0]
	if gm:
		return ship
	player = player_of(galaxy, interaction.user)
	if player.location != ship.id:
		raise UserError(f"You aren't aboard the {ship.name}.")
	if crew and ship.id not in player.crew_affiliations:
		raise UserError(f"Only crew of the {ship.name} can do this.")
	return ship


def choices(
	values: list[tuple[str, str]], current: str
) -> list[discord.app_commands.Choice[str]]:
	"""Autocomplete choices (label, value) whose label contains what was typed."""
	return [
		discord.app_commands.Choice(name=label[:100], value=value)
		for label, value in values
		if current.casefold() in label.casefold()
	][:MAX_CHOICES]


OnPick = Callable[[discord.Interaction, list[str]], Awaitable[None]]


class _CrewSelect(discord.ui.Select):
	async def callback(self, interaction: discord.Interaction) -> None:
		assert isinstance(self.view, CrewPicker)
		self.view.stop()
		await self.view.on_pick(interaction, list(self.values))


class CrewPicker(discord.ui.View):
	"""A pick-list of eligible crew. Only the user who opened it can use it."""

	def __init__(self, user_id: int, units: list[Unit], amount: int, on_pick: OnPick):
		super().__init__(timeout=180)
		self.user_id = user_id
		self.on_pick = on_pick
		self.select = _CrewSelect(
			placeholder=f"Choose {amount} crew",
			min_values=amount,
			max_values=amount,
			options=[
				discord.SelectOption(
					label=f"{u.id} {u.name}",
					value=u.id,
					description=f"affiliated with {u.affiliation}",
				)
				for u in units[:MAX_CHOICES]
			],
		)
		self.add_item(self.select)

	async def interaction_check(self, interaction: discord.Interaction) -> bool:
		return interaction.user.id == self.user_id

	async def on_error(
		self, interaction: discord.Interaction, error: Exception, item
	) -> None:
		if isinstance(error, (UserError, GameError)):
			await reply(interaction, str(error), ephemeral=True)
			return
		logger.exception("Error in crew picker", exc_info=error)
		await reply(
			interaction,
			"Something went wrong. The error has been logged.",
			ephemeral=True,
		)

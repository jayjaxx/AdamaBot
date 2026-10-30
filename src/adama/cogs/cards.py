import logging

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama import discord_ops as ops
from adama.bot import AdamaBot, defer, reply
from adama.cogs._shared import (
	WHERE_BRIDGE,
	choices,
	player_of,
	ship_for_channel,
	ship_here,
	ship_named,
)
from adama.models.action_card import ActionCard
from adama.models.galaxy import Galaxy
from adama.perms import control_only
from adama.rules import Notice
from adama.rules import cards as card_rules

logger = logging.getLogger()

GM = {"gm": True}
RETAIN_CHOICES = [
	app_commands.Choice(name="false: used up when played", value="false"),
	app_commands.Choice(name="true: kept after playing", value="true"),
	app_commands.Choice(name="once: kept, usable once per turn", value="once"),
]


def card_label(card: ActionCard) -> str:
	return f"{card.id} {card.name}" + (" (used this turn)" if card.exhausted else "")


class CardCommands(commands.Cog):
	"""Action cards."""

	card = app_commands.Group(
		name="card", description="Control: create and edit action cards", extras=GM
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
		assert interaction.guild is not None
		self.galaxies.save(galaxy)
		if not interaction.response.is_done():
			await defer(interaction, ephemeral=True)
		await ops.post_notices(interaction.guild, galaxy, notices, self.roles)
		await reply(interaction, done, ephemeral=True)

	# Autocomplete

	async def hand_autocomplete(self, interaction: discord.Interaction, current: str):
		player = self.galaxy(interaction).player_by_user(interaction.user.id)
		cards = player.action_cards.values() if player else []
		return choices([(card_label(c), c.id) for c in cards], current)

	async def unclaimed_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		found = ship_for_channel(self.galaxy(interaction), interaction.channel_id)
		cards = found[0].action_cards.values() if found else []
		return choices([(card_label(c), c.id) for c in cards], current)

	async def any_card_autocomplete(
		self, interaction: discord.Interaction, current: str
	):
		galaxy = self.galaxy(interaction)
		return choices(
			[(card_label(c), c.id) for c in galaxy.iter_action_cards()], current
		)

	async def ship_autocomplete(self, interaction: discord.Interaction, current: str):
		galaxy = self.galaxy(interaction)
		return choices([(s.name, s.name) for s in galaxy.ships.values()], current)

	# Players

	@app_commands.command(
		name="takecard",
		extras=WHERE_BRIDGE,
		description="Take one of your ship's unclaimed action cards",
	)
	@app_commands.autocomplete(card=unclaimed_autocomplete)
	async def takecard(self, interaction: discord.Interaction, card: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_here(galaxy, interaction, ch.SHIP_BRIDGE, gm=False, crew=False)
			notices = card_rules.take(
				galaxy, player_of(galaxy, interaction.user), ship, card
			)
			await self.finish(interaction, galaxy, notices, "Card taken: see your PDA.")

	@app_commands.command(
		name="leavecard",
		extras=WHERE_BRIDGE,
		description="Leave one of your action cards on this ship",
	)
	@app_commands.autocomplete(card=hand_autocomplete)
	async def leavecard(self, interaction: discord.Interaction, card: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_here(galaxy, interaction, ch.SHIP_BRIDGE, gm=False, crew=False)
			notices = card_rules.leave(player_of(galaxy, interaction.user), ship, card)
			await self.finish(interaction, galaxy, notices, "Card left.")

	@app_commands.command(
		name="givecard",
		description="Give one of your action cards to a player aboard your ship",
	)
	@app_commands.autocomplete(card=hand_autocomplete)
	async def givecard(
		self, interaction: discord.Interaction, card: str, player: discord.Member
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			notices = card_rules.give(
				galaxy,
				player_of(galaxy, interaction.user),
				card,
				player_of(galaxy, player),
			)
			await self.finish(interaction, galaxy, notices, "Card given.")

	@app_commands.command(
		name="playcard",
		description="Play one of your action cards (control is alerted, not the fleet)",
	)
	@app_commands.describe(notes="What or who you're playing it on")
	@app_commands.autocomplete(card=hand_autocomplete)
	async def playcard(
		self, interaction: discord.Interaction, card: str, notes: str | None = None
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			notices = card_rules.play(
				galaxy, player_of(galaxy, interaction.user), card, notes
			)
			await self.finish(interaction, galaxy, notices, "Card played.")

	# Control

	def recipient(
		self,
		galaxy: Galaxy,
		player: discord.Member | None,
		ship_name: str | None,
	):
		return (
			player_of(galaxy, player) if player else None,
			ship_named(galaxy, ship_name) if ship_name else None,
		)

	@card.command(
		name="transfer",
		description="Move a card from anywhere to a player's hand or a ship (ignores affiliation)",
	)
	@app_commands.describe(
		player="Give it to this player…", ship_name="…or leave it on this ship"
	)
	@app_commands.autocomplete(card=any_card_autocomplete, ship_name=ship_autocomplete)
	@control_only()
	async def card_transfer(
		self,
		interaction: discord.Interaction,
		card: str,
		player: discord.Member | None = None,
		ship_name: str | None = None,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text, notices = card_rules.transfer(
				galaxy, card, *self.recipient(galaxy, player, ship_name)
			)
			await self.finish(interaction, galaxy, notices, text)

	@card.command(
		name="conjure", description="Create a new action card for a player or ship"
	)
	@app_commands.describe(
		player="Give it to this player…",
		ship_name="…or leave it on this ship",
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def card_conjure(
		self,
		interaction: discord.Interaction,
		name: str,
		player: discord.Member | None = None,
		ship_name: str | None = None,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			created, notices = card_rules.conjure(
				galaxy, name, *self.recipient(galaxy, player, ship_name)
			)
			await self.finish(
				interaction,
				galaxy,
				notices,
				f"Conjured `{created.id}` **{created.name}**. Set its text with "
				"/card description and its use with /card retain.",
			)

	@card.command(name="description", description="Set an action card's text")
	@app_commands.autocomplete(card=any_card_autocomplete)
	@control_only()
	async def card_description(
		self, interaction: discord.Interaction, card: str, description: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = card_rules.set_description(galaxy, card, description)
			await self.finish(interaction, galaxy, [], text)

	@card.command(
		name="retain",
		description="Set whether a card is used up, kept, or usable once per turn",
	)
	@app_commands.choices(value=RETAIN_CHOICES)
	@app_commands.autocomplete(card=any_card_autocomplete)
	@control_only()
	async def card_retain(
		self, interaction: discord.Interaction, card: str, value: str
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = card_rules.set_retain(galaxy, card, value)
			await self.finish(interaction, galaxy, [], text)

	@card.command(
		name="affiliation",
		description="Set a card's ship, and whether only its crew can be given it",
	)
	@app_commands.describe(restricted="Leave out to keep it as it is")
	@app_commands.autocomplete(card=any_card_autocomplete, ship_name=ship_autocomplete)
	@control_only()
	async def card_affiliation(
		self,
		interaction: discord.Interaction,
		card: str,
		ship_name: str,
		restricted: bool | None = None,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = card_rules.set_affiliation(
				galaxy, card, ship_named(galaxy, ship_name), restricted
			)
			await self.finish(interaction, galaxy, [], text)

	@card.command(name="destroy", description="Destroy an action card")
	@app_commands.autocomplete(card=any_card_autocomplete)
	@control_only()
	async def card_destroy(self, interaction: discord.Interaction, card: str):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			text = card_rules.destroy(galaxy, card)
			await self.finish(interaction, galaxy, [], text)


async def setup(bot: AdamaBot):
	await bot.add_cog(CardCommands(bot))

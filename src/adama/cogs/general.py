import logging
import random

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama import discord_ops as ops
from adama.bot import AdamaBot, reply
from adama.models.galaxy import Galaxy
from adama.models.player import LOCATION_COMBAT
from adama.models.ship import Ship
from adama.perms import UserError, control_only, is_gm
from adama.status import (
	bridge_status,
	fleet_list,
	hangar_status,
	location_name,
	pda_status,
)

logger = logging.getLogger()

GM = {"gm": True}


class GeneralCommands(commands.Cog):
	def __init__(self, bot: AdamaBot):
		self.bot = bot
		self.container = bot.container
		self.galaxies = bot.container.services.galaxy
		self.roles = bot.config.roles

	def galaxy(self, interaction: discord.Interaction) -> Galaxy:
		assert interaction.guild is not None
		return self.galaxies.get(interaction.guild.id)

	def ship_channel(
		self, galaxy: Galaxy, channel_id: int | None
	) -> tuple[Ship, str] | None:
		"""The ship and kind ("hangar" or "bridge") of a ship text channel."""
		for ship_id, created in galaxy.discord.ship_channels.items():
			for kind in (ch.SHIP_HANGAR, ch.SHIP_BRIDGE):
				if created.get(kind) == channel_id and ship_id in galaxy.ships:
					return galaxy.ships[ship_id], kind
		return None

	@app_commands.command(name="ping", description="Say hello to the bot!")
	async def ping(self, interaction: discord.Interaction):
		await interaction.response.send_message(f"Hello, {interaction.user.mention}!")

	@app_commands.command(name="roll", description="Roll 1d6")
	async def roll(self, interaction: discord.Interaction):
		await interaction.response.send_message(
			f"{interaction.user.mention} rolled a **{random.randint(1, 6)}**."
		)

	@app_commands.command(
		name="status",
		description="The status of your ship (hangar or bridge) or yourself (PDA)",
	)
	async def status(self, interaction: discord.Interaction):
		galaxy = self.galaxy(interaction)
		user = interaction.user
		gm = is_gm(user, self.roles)
		player = galaxy.player_by_user(user.id)

		pda_owner = next(
			(
				owner
				for owner, channel in galaxy.discord.pdas.items()
				if channel == interaction.channel_id
			),
			None,
		)
		if pda_owner is not None:
			if pda_owner != user.id and not gm:
				raise UserError("This isn't your PDA.")
			owner = galaxy.player_by_user(pda_owner)
			if owner is None:
				await reply(
					interaction,
					"No character yet: players are created when the game starts.",
				)
			else:
				await reply(interaction, pda_status(galaxy, owner))
			return

		found = self.ship_channel(galaxy, interaction.channel_id)
		if found is None:
			raise UserError("Run /status in a ship's hangar or bridge, or in your PDA.")
		ship, kind = found
		if not gm:
			if player is None:
				raise UserError("The game hasn't started yet.")
			if player.location != ship.id:
				raise UserError(f"You aren't aboard the {ship.name}.")
			if kind == ch.SHIP_BRIDGE and ship.id not in player.crew_affiliations:
				raise UserError(
					f"Only crew of the {ship.name} can check the bridge status."
				)
		text = (
			bridge_status(galaxy, ship)
			if kind == ch.SHIP_BRIDGE
			else hangar_status(galaxy, ship)
		)
		await reply(interaction, text)

	@app_commands.command(
		name="fleet", description="List the ships at your coordinates"
	)
	async def fleet(self, interaction: discord.Interaction):
		galaxy = self.galaxy(interaction)
		player = galaxy.player_by_user(interaction.user.id)
		ship_id = None
		if player is not None:
			ship_id = (
				player.combat_origin_ship
				if player.location == LOCATION_COMBAT
				else player.location
			)
			if player.location == "brig":
				ship_id = galaxy.brig_ship
		elif is_gm(interaction.user, self.roles):
			found = self.ship_channel(galaxy, interaction.channel_id)
			if found is None:
				# Control outside a ship channel sees every set of coordinates.
				lists = [fleet_list(galaxy, c) for c in sorted(galaxy.coordinates)]
				await reply(
					interaction,
					"\n\n".join(lists) or "No ships are loaded.",
					ephemeral=True,
				)
				return
			ship_id = found[0].id
		if not ship_id or ship_id not in galaxy.ships:
			raise UserError("You aren't anywhere yet.")
		await reply(interaction, fleet_list(galaxy, galaxy.ships[ship_id].coordinates))

	@app_commands.command(
		name="control", description="Summon control, optionally with a message"
	)
	async def control(
		self, interaction: discord.Interaction, message: str | None = None
	):
		guild = interaction.guild
		assert guild is not None
		galaxy = self.galaxy(interaction)
		chat = ops.get_text(guild, galaxy, ch.CONTROL_CHAT)
		control = ops.get_control_role(guild, self.roles)
		player = galaxy.player_by_user(interaction.user.id)
		where = f" ({location_name(galaxy, player)})" if player is not None else ""
		channel = interaction.channel
		link = getattr(channel, "jump_url", "a DM")
		text = f"{control.mention if control else 'Control'}: {interaction.user.mention}{where} summons control from {link}"
		if message:
			text += f":\n> {message}"
		await chat.send(
			text,
			allowed_mentions=discord.AllowedMentions(
				roles=[control] if control else False, users=False
			),
		)
		await reply(interaction, "Control has been summoned.", ephemeral=True)

	def command_list(self, *, gm: bool) -> str:
		commands_ = sorted(self.bot.tree.get_commands(), key=lambda c: c.name)
		lines = [
			f"`/{c.name}` {getattr(c, 'description', '')}"
			for c in commands_
			if bool(c.extras.get("gm")) == gm
		]
		return "\n".join(lines)

	@app_commands.command(name="help", description="List the player commands")
	async def help(self, interaction: discord.Interaction):
		guild = interaction.guild
		assert guild is not None
		text = "**ADAMA commands**\n" + self.command_list(gm=False)
		pda = guild.get_channel(
			self.galaxy(interaction).discord.pdas.get(interaction.user.id, 0)
		)
		if isinstance(pda, discord.TextChannel):
			await ops.send_long(pda, text)
			await reply(
				interaction, f"Sent the command list to {pda.mention}.", ephemeral=True
			)
			return
		try:
			await ops.send_long(interaction.user, text)
		except discord.Forbidden:
			await reply(interaction, text, ephemeral=True)
			return
		await reply(interaction, "Sent you the command list in a DM.", ephemeral=True)

	@app_commands.command(
		name="gmhelp", description="List the control commands", extras=GM
	)
	@control_only()
	async def gmhelp(self, interaction: discord.Interaction):
		text = "**ADAMA control commands**\n" + self.command_list(gm=True)
		try:
			await ops.send_long(interaction.user, text)
		except discord.Forbidden:
			await reply(interaction, text, ephemeral=True)
			return
		await reply(
			interaction, "Sent you the control command list in a DM.", ephemeral=True
		)


async def setup(bot: AdamaBot):
	cog = GeneralCommands(bot)
	await bot.add_cog(cog)

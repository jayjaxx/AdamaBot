import logging
from collections.abc import Set as AbstractSet
import random

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama import discord_ops as ops
from adama.bot import AdamaBot, reply
from adama.cogs._shared import OFFICES, ship_for_channel
from adama.models.galaxy import Galaxy
from adama.models.player import LOCATION_COMBAT, Player
from adama.perms import UserError, is_gm
from adama.status import (
	bridge_status,
	brig_status,
	fleet_list,
	hangar_status,
	location_name,
	pda_status,
)

logger = logging.getLogger()

GM = {"gm": True}

HELP_PLAYERS = "players"
HELP_CONTROL = "control"
HELP_ALL = "all"
HELP_CHOICES = [
	app_commands.Choice(name="players (default)", value=HELP_PLAYERS),
	app_commands.Choice(name="control", value=HELP_CONTROL),
	*(
		app_commands.Choice(name=f"{header} only", value=office)
		for office, (header, _) in OFFICES.items()
	),
	app_commands.Choice(name="all, by section", value=HELP_ALL),
]


class GeneralCommands(commands.Cog):
	def __init__(self, bot: AdamaBot):
		self.bot = bot
		self.container = bot.container
		self.galaxies = bot.container.services.galaxy
		self.roles = bot.config.roles

	def galaxy(self, interaction: discord.Interaction) -> Galaxy:
		assert interaction.guild is not None
		return self.galaxies.get(interaction.guild.id)

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

		if interaction.channel_id == galaxy.discord.channels.get(ch.BRIG):
			# Only prisoners, people aboard the brig ship and control can see #brig.
			await reply(interaction, brig_status(galaxy), ephemeral=True)
			return

		found = ship_for_channel(galaxy, interaction.channel_id)
		if found is None:
			raise UserError(
				"Run /status in a ship's hangar or bridge, the brig, or your PDA."
			)
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
		# Only the person who asked sees it, so the status doesn't bury the channel's
		# conversation.
		text = (
			bridge_status(galaxy, ship)
			if kind == ch.SHIP_BRIDGE
			else hangar_status(galaxy, ship)
		)
		await reply(interaction, text, ephemeral=True)

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
			found = ship_for_channel(galaxy, interaction.channel_id)
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
		call = await chat.send(
			text,
			allowed_mentions=discord.AllowedMentions(
				roles=[control] if control else False, users=False
			),
		)
		await ops.offer_call(call)
		await reply(interaction, "Control has been summoned.", ephemeral=True)

	def _lines(self, *, gm: bool, office: str | None = None) -> list[str]:
		"""One line per command: control commands (gm), or player commands of one office
		(None for the commands every player has)."""
		lines: list[str] = []
		for c in sorted(self.bot.tree.get_commands(), key=lambda c: c.name):
			if bool(c.extras.get("gm")) != gm:
				continue
			# A group (e.g. /station) lists each of its subcommands.
			listed = c.commands if isinstance(c, app_commands.Group) else [c]
			for s in listed:
				if not gm and s.extras.get("office") != office:
					continue
				where = s.extras.get("where")
				tag = f" *({where})*" if where else ""
				description = getattr(s, "description", "")
				if office is not None:
					description = description.removeprefix(f"{OFFICES[office][0]}: ")
				lines.append(f"`/{s.qualified_name}`{tag} {description}")
		return lines

	def offices_held(
		self, player: Player | None, member: discord.Member | discord.User
	) -> set[str]:
		"""Offices from the character's flags or, as before /startgame or if the role
		was given by hand, from the member's Discord roles."""
		role_names = {r.name for r in getattr(member, "roles", [])}
		role_of = {
			"president": self.roles.PRESIDENT,
			"first_responder": self.roles.FIRST_RESPONDER,
		}
		return {
			office
			for office, (_, holds) in OFFICES.items()
			if (player is not None and holds(player)) or role_of[office] in role_names
		}

	def command_list(self, which: str, held: AbstractSet[str] = frozenset()) -> str:
		"""The /help text. `which` is a HELP_LISTS value; the player list adds the
		offices in `held`. Each office and control get their own header."""
		sections: list[tuple[str, list[str]]] = []
		if which in (HELP_PLAYERS, HELP_ALL):
			sections.append(("ADAMA commands", self._lines(gm=False)))
		for office, (header, _) in OFFICES.items():
			if (which == HELP_PLAYERS and office in held) or which in (
				office,
				HELP_ALL,
			):
				sections.append(
					(f"{header} commands", self._lines(gm=False, office=office))
				)
		if which in (HELP_CONTROL, HELP_ALL):
			sections.append(("Control commands", self._lines(gm=True)))
		return "\n\n".join(
			"\n".join([f"**{header}**", *lines]) for header, lines in sections
		)

	@app_commands.command(name="help", description="List the commands you can use")
	@app_commands.rename(which="list")
	@app_commands.describe(
		which="Control only: which list to show (players by default)"
	)
	@app_commands.choices(which=HELP_CHOICES)
	async def help(self, interaction: discord.Interaction, which: str = HELP_PLAYERS):
		guild = interaction.guild
		assert guild is not None
		galaxy = self.galaxy(interaction)
		if which != HELP_PLAYERS and not is_gm(interaction.user, self.roles):
			raise UserError("Only control can pick a different command list.")
		player = galaxy.player_by_user(interaction.user.id)
		text = self.command_list(which, self.offices_held(player, interaction.user))
		# The full list is several messages: answer Discord before sending them.
		await interaction.response.defer(ephemeral=True)
		pda = guild.get_channel(galaxy.discord.pdas.get(interaction.user.id, 0))
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
		name="shutdown",
		description="Shut the bot down (bot owner only: it stops ADAMA in every server)",
		extras=GM,
	)
	async def shutdown(self, interaction: discord.Interaction):
		if not await self.bot.is_owner(interaction.user):
			raise UserError("Only the bot's owner can shut it down.")
		logger.info("Shutdown requested by %s", interaction.user.name)
		await reply(
			interaction,
			"Shutting down. Game state is saved after every command.",
			ephemeral=True,
		)
		# Wait for any command still changing a game, so nothing is cut off mid-save.
		for guild in self.bot.guilds:
			async with self.galaxies.lock(guild.id):
				pass
		await self.bot.close()


async def setup(bot: AdamaBot):
	cog = GeneralCommands(bot)
	await bot.add_cog(cog)

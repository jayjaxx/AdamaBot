import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama.config import GlobalConfig
from adama.constants import _BOT_EXTENSION_DIRECTORY, _BOT_EXTENSION_MODULE
from adama.container import Container
from adama.models.galaxy import STATE_ENDED, Galaxy
from adama.models.player import LOCATION_COMBAT, Player
from adama.perms import GM_COMMAND_PERMISSION, UserError, is_gm
from adama.rules import GameError
from adama.rules.players import current_ship
from adama.status import split_message

logger = logging.getLogger()

# Commands that still work in the otherwise blind #starting-games.
STARTING_GAMES_COMMANDS = {"startinitialization", "gm", "shutdown"}
# The only commands brigged and dead players can use.
BRIGGED_COMMANDS = {"status", "roll", "control", "help"}
DEAD_COMMANDS = {"control", "help"}
# After /endgame, for everyone (control included).
ENDED_COMMANDS = {"status", "control"} | STARTING_GAMES_COMMANDS


def restricted_reason(dead: bool, brigged: bool, command: str | None) -> str | None:
	"""Why a dead or brigged player can't use this command, if they can't."""
	if dead and command not in DEAD_COMMANDS:
		return "You're dead: you can only use /control and /help."
	if brigged and command not in BRIGGED_COMMANDS:
		return (
			"You're in the brig: you can only use /status, /roll, /control and /help."
		)
	return None


DESTROYED_SHIP = (
	"This ship has been destroyed. If you're reading this, control should be on their "
	"way to deal with you. If they aren't here shortly, use /control."
)


def destroyed_reason(galaxy: Galaxy, player: Player, command: str | None) -> str | None:
	"""Players aboard (or in the brig of) a destroyed ship can only call control."""
	if player.location == LOCATION_COMBAT or command == "control":
		return None
	ship = current_ship(galaxy, player)
	return DESTROYED_SHIP if ship is not None and ship.destroyed else None


def is_blind(interaction: discord.Interaction) -> bool:
	"""Whether the bot ignores commands in this channel: some text channels and every VC."""
	channel = interaction.channel
	if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
		return True
	name = getattr(channel, "name", None)
	if name not in ch.BLIND_TEXT_CHANNELS:
		return False
	command = interaction.command.name if interaction.command else None
	return not (name == "starting-games" and command in STARTING_GAMES_COMMANDS)


def hide_from_players(command) -> None:
	"""Control commands only show in the command list for roles with the GM permission.
	The control_only check still guards them if a server overrides this."""
	if isinstance(command, (app_commands.Command, app_commands.Group)) and (
		command.extras.get("gm")
	):
		command.default_permissions = GM_COMMAND_PERMISSION


async def reply(
	interaction: discord.Interaction, text: str, *, ephemeral: bool = False
) -> None:
	"""Replies with text of any length, without pinging anyone."""
	no_mentions = discord.AllowedMentions.none()
	for chunk in split_message(text):
		if interaction.response.is_done():
			await interaction.followup.send(
				chunk, ephemeral=ephemeral, allowed_mentions=no_mentions
			)
		else:
			await interaction.response.send_message(
				chunk, ephemeral=ephemeral, allowed_mentions=no_mentions
			)


async def refuse(interaction: discord.Interaction, text: str) -> None:
	"""Turns an interaction away. While the user is still typing (autocomplete) Discord
	only accepts suggestions, so they get none; the reason is shown if they send it."""
	if interaction.type == discord.InteractionType.autocomplete:
		await interaction.response.autocomplete([])
	else:
		await reply(interaction, text, ephemeral=True)


class AdamaTree(app_commands.CommandTree):
	async def interaction_check(self, interaction: discord.Interaction) -> bool:
		if interaction.guild is None:
			await refuse(interaction, "ADAMA commands only work in a server.")
			return False
		if is_blind(interaction):
			await refuse(interaction, "ADAMA doesn't take commands in this channel.")
			return False
		bot = interaction.client
		if not isinstance(bot, AdamaBot):
			return True
		galaxy = bot.container.services.galaxy.get(interaction.guild.id)
		command = interaction.command.name if interaction.command else None
		if galaxy.state == STATE_ENDED:
			if command in ENDED_COMMANDS:
				return True
			await refuse(
				interaction,
				"The game is over: only /status and /control work until a new game is set up.",
			)
			return False
		if not is_gm(interaction.user, bot.config.roles):
			player = galaxy.player_by_user(interaction.user.id)
			if player is not None:
				reason = restricted_reason(
					player.dead, player.brigged, command
				) or destroyed_reason(galaxy, player, command)
				if reason is not None:
					await refuse(interaction, reason)
					return False
		return True

	async def on_error(
		self, interaction: discord.Interaction, error: app_commands.AppCommandError
	):
		original = getattr(error, "original", error)
		if isinstance(original, (UserError, GameError)):
			message = str(original)
		elif isinstance(error, app_commands.CheckFailure):
			message = str(error) or "You can't use this command here."
		else:
			name = interaction.command.name if interaction.command else "?"
			logger.exception("Error in /%s", name, exc_info=error)
			message = (
				"Something went wrong running that command. The error has been logged."
			)
		try:
			await reply(interaction, message, ephemeral=True)
		except discord.HTTPException:
			logger.exception("Could not report an error to the user")


SHUTDOWN_ALERT = (
	"@everyone **ADAMA is shutting down.** Commands won't work until it has been "
	"restarted, so please don't send any until then. It should be back shortly."
)


class AdamaBot(commands.Bot):
	def __init__(
		self, config: GlobalConfig, intents=discord.Intents.default(), container=None
	):
		super().__init__(
			command_prefix=config.discord.PREFIX, intents=intents, tree_cls=AdamaTree
		)

		self.config = config
		self.epoch = discord.utils.utcnow()
		self.container = container if container is not None else Container()

	async def setup_hook(self):
		for ext in self.get_all_extensions():
			await self.load_extension(ext)

		for command in self.tree.get_commands():
			command.allowed_contexts = app_commands.AppCommandContext(guild=True)
			hide_from_players(command)

		if self.config.discord.DEV_GUILD_IDS:
			for guild_id in self.config.discord.DEV_GUILD_IDS:
				guild = discord.Object(id=guild_id)
				self.tree.copy_global_to(guild=guild)
				synced = await self.tree.sync(guild=guild)
				logger.info("Synced %d commands to guild %s", len(synced), guild_id)
		else:
			synced = await self.tree.sync()
			logger.info("Synced %d commands globally", len(synced))

	async def on_ready(self):
		logger.info(f"Logged in as: {self.user}")

	async def on_app_command_completion(
		self, interaction: discord.Interaction, command
	):
		time_taken = discord.utils.utcnow() - interaction.created_at
		logger.info(f"{interaction.user.name} ran /{command.name} in {time_taken}")

	async def announce_shutdown(self) -> None:
		"""Pings everyone in each server's #control-broadcasts that the bot is going down."""
		everyone = discord.AllowedMentions(everyone=True)
		for guild in self.guilds:
			galaxy = self.container.services.galaxy.get(guild.id)
			channel_id = galaxy.discord.channels.get(ch.CONTROL_BROADCASTS)
			channel = guild.get_channel(channel_id) if channel_id else None
			if not isinstance(channel, discord.TextChannel):
				continue
			try:
				await channel.send(SHUTDOWN_ALERT, allowed_mentions=everyone)
			except discord.HTTPException:
				logger.warning("Couldn't post the shutdown alert in %s", guild.name)

	async def close(self):
		if self.is_ready() and not self.is_closed():
			try:
				await asyncio.wait_for(self.announce_shutdown(), timeout=15)
			except TimeoutError:
				logger.warning("Timed out posting the shutdown alerts")
		services = self.container.services
		fields = services.__dataclass_fields__
		for field in fields:
			service = getattr(services, field)
			service.close()

		logger.info("Closing down the bot...")
		await super().close()

	@staticmethod
	def get_all_extensions():
		"""Gets all cog extensions."""
		for file in _BOT_EXTENSION_DIRECTORY.glob("*"):
			# ignore non py files
			# ignore private files e.g. '_private.py'
			if not file.suffix == ".py" or file.name.startswith("_"):
				continue

			yield f"{_BOT_EXTENSION_MODULE}.{file.name[:-3]}"

	# add logging to base extension functions
	async def load_extension(self, name: str, *, package: str | None = None):
		"""Loads a cog extension."""
		try:
			start = discord.utils.utcnow()
			await super().load_extension(f"{name}", package=package)
			end = discord.utils.utcnow()
			logger.info(
				"Successfully loaded Cog: %s in %s",
				name,
				end - start,
			)
		except commands.ExtensionError as e:
			logger.info("Failed to load Cog %s", name)
			raise e

	async def unload_extension(self, name: str, *, package: str | None = None) -> None:
		"""Unloads a cog extension."""
		try:
			start = discord.utils.utcnow()
			await super().unload_extension(f"{name}", package=package)
			end = discord.utils.utcnow()
			logger.info(
				"Successfully unloaded Cog: %s in %s",
				name,
				end - start,
			)
		except commands.ExtensionError as e:
			logger.info("Failed to unload Cog %s", name)
			raise e

	async def reload_extension(self, name: str, *, package: str | None = None) -> None:
		"""Reloads a cog extension. Will roll back to the previous version if it fails to load."""
		try:
			start = discord.utils.utcnow()
			await super().reload_extension(f"{name}", package=package)
			end = discord.utils.utcnow()
			logger.info(
				"Successfully reloaded Cog: %s in %s",
				name,
				end - start,
			)
		except commands.ExtensionError as e:
			logger.info("Failed to reload Cog %s", name)
			raise e

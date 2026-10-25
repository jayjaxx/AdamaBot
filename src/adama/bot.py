import logging

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama.config import GlobalConfig
from adama.constants import _BOT_EXTENSION_DIRECTORY, _BOT_EXTENSION_MODULE
from adama.container import Container
from adama.perms import UserError
from adama.status import split_message

logger = logging.getLogger()

# Commands that still work in the otherwise blind #starting-games.
STARTING_GAMES_COMMANDS = {"startinitialization"}


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


class AdamaTree(app_commands.CommandTree):
	async def interaction_check(self, interaction: discord.Interaction) -> bool:
		if interaction.guild is None:
			await reply(
				interaction, "ADAMA commands only work in a server.", ephemeral=True
			)
			return False
		if is_blind(interaction):
			await reply(
				interaction,
				"ADAMA doesn't take commands in this channel.",
				ephemeral=True,
			)
			return False
		return True

	async def on_error(
		self, interaction: discord.Interaction, error: app_commands.AppCommandError
	):
		if isinstance(error, UserError):
			message = str(error)
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

	async def close(self):
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

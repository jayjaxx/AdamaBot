from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands

from adama.config import RolesConfig

if TYPE_CHECKING:
	from adama.bot import AdamaBot


# Control commands are hidden from anyone without this server permission, so players
# never see them. The bot grants it to the Control and Game Starter roles; it's chosen
# because it's otherwise harmless (it only manages scheduled events).
GM_COMMAND_PERMISSION = discord.Permissions(manage_events=True)


class UserError(app_commands.AppCommandError):
	"""An expected error: its message is shown to the user who ran the command."""


def roles_config(interaction: discord.Interaction) -> RolesConfig:
	return cast("AdamaBot", interaction.client).config.roles


def is_gm(member: discord.Member | discord.User, roles: RolesConfig) -> bool:
	if not isinstance(member, discord.Member):
		return False
	return any(role.name == roles.CONTROL for role in member.roles)


def is_game_starter(member: discord.Member | discord.User, roles: RolesConfig) -> bool:
	if not isinstance(member, discord.Member):
		return False
	if member.guild_permissions.administrator:
		return True
	return any(role.name == roles.GAME_STARTER for role in member.roles)


def control_only():
	"""The command needs the control role (a GM command)."""

	def predicate(interaction: discord.Interaction) -> bool:
		if not is_gm(interaction.user, roles_config(interaction)):
			raise UserError("Only control can use this command.")
		return True

	return app_commands.check(predicate)


def game_starter_only():
	def predicate(interaction: discord.Interaction) -> bool:
		if not is_game_starter(interaction.user, roles_config(interaction)):
			raise UserError(
				"Only a Discord admin or a game starter can use this command."
			)
		return True

	return app_commands.check(predicate)

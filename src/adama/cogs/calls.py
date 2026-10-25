import discord
from discord.ext import commands

from adama import channels as ch
from adama import discord_ops as ops
from adama.bot import AdamaBot
from adama.perms import is_gm


class ControlCalls(commands.Cog):
	"""Calls in #control-chat: the first controller to salute one takes it."""

	def __init__(self, bot: AdamaBot):
		self.bot = bot
		self.galaxies = bot.container.services.galaxy
		self.roles = bot.config.roles

	@commands.Cog.listener()
	async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
		if (
			payload.guild_id is None
			or self.bot.user is None
			or payload.user_id == self.bot.user.id
			or str(payload.emoji) != ops.CALL_EMOJI
		):
			return
		galaxy = self.galaxies.get(payload.guild_id)
		if payload.channel_id != galaxy.discord.channels.get(ch.CONTROL_CHAT):
			return
		guild = self.bot.get_guild(payload.guild_id)
		if guild is None:
			return
		member = payload.member or guild.get_member(payload.user_id)
		channel = guild.get_channel(payload.channel_id)
		if member is None or not is_gm(member, self.roles):
			return
		if isinstance(channel, discord.TextChannel):
			await ops.take_call(channel, payload.message_id, member)


async def setup(bot: AdamaBot):
	await bot.add_cog(ControlCalls(bot))

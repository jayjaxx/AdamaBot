import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama import discord_ops as ops
from adama.constants import DEFAULT_SHIPS_DIR
from adama.bot import AdamaBot, back_online_alert, reply
from adama.models.galaxy import STATE_ENDED, STATE_IDLE, STATE_INITIALIZING, Galaxy
from adama.models.ship import Ship
from adama.perms import UserError, control_only, game_starter_only, is_gm
from adama.rules.combat import end_lockdown
from adama.rules.shuttles import drop_stale_countdowns
from adama.rules.players import LobbyMember, start_game
from adama.shipfiles import ShipFile, validate_ship_files
from adama.status import location_name, ship_summary

logger = logging.getLogger()

JOIN_EMOJI = "✅"
# Discord allows at most 10 attachments per message.
MAX_FILES = 10
GM = {"gm": True}
GAME_OVER = (
	"**The game is over!** Thank you for playing. Every channel is now open to "
	"everyone: gather in Fleet Announcements for the debrief."
)

Attachment = discord.Attachment | None


async def read_files(attachments: list[Attachment]) -> list[ShipFile]:
	return [ShipFile(a.filename, await a.read()) for a in attachments if a is not None]


def default_ship_files() -> list[ShipFile]:
	"""The ship files in shipdefs/, used when /startinitialization has no attachments."""
	return [
		ShipFile(path.name, path.read_bytes())
		for path in sorted(DEFAULT_SHIPS_DIR.glob("*.json"))
	]


def problems_text(problems: list[str]) -> str:
	return "These ship files have problems, so nothing was loaded:\n" + "\n".join(
		f"- {problem}" for problem in problems
	)


MAX_ATTACHMENTS = 10


def file_copies(files: list[ShipFile]) -> list[discord.File]:
	return [discord.File(io.BytesIO(f.content), filename=f.filename) for f in files]


class GameCommands(commands.Cog):
	def __init__(self, bot: AdamaBot):
		self.bot = bot
		self.container = bot.container
		self.galaxies = bot.container.services.galaxy
		self.roles = bot.config.roles
		# Servers told the bot is back since this start-up.
		self.back_online_sent: set[int] = set()

	def galaxy(self, interaction: discord.Interaction) -> Galaxy:
		assert interaction.guild is not None
		return self.galaxies.get(interaction.guild.id)

	async def ship_autocomplete(self, interaction: discord.Interaction, current: str):
		galaxy = self.galaxy(interaction)
		return [
			app_commands.Choice(name=ship.name, value=ship.name)
			for ship in galaxy.ships.values()
			if current.casefold() in ship.name.casefold()
		][:25]

	async def load_ships(
		self,
		guild: discord.Guild,
		galaxy: Galaxy,
		ships: list[Ship],
		files: list[ShipFile],
	) -> None:
		"""Adds validated ships with their channels, then reports to #control-setup."""
		control = ops.get_control_role(guild, self.roles)
		if control is None:
			control = await ops.ensure_server(guild, galaxy, self.roles)
		for ship in ships:
			galaxy.add_ship(ship)
			await ops.create_ship_channels(guild, galaxy, ship, control)
			# Save as we go, so a failure part-way still remembers the channels made.
			self.galaxies.save(galaxy)
		await ops.place_council_and_brig(guild, galaxy)

		setup = ops.get_text(guild, galaxy, ch.CONTROL_SETUP)
		# Discord allows at most 10 attachments per message (the default fleet has 11).
		for start in range(0, len(files), MAX_ATTACHMENTS):
			await setup.send(
				"Ship files loaded:" if start == 0 else "More ship files loaded:",
				files=file_copies(files[start : start + MAX_ATTACHMENTS]),
			)
		lines = [ship_summary(ship) for ship in ships]
		lines.append(
			"Load more ships with /loadship (Discord allows at most 10 attachments per message)."
		)
		if galaxy.council_chambers_ship is None:
			lines.append(
				"No ship has a Council Chamber station yet, so there are no council chambers."
			)
		if galaxy.brig_ship is None:
			lines.append("No ship has a Brig station yet, so there is no brig.")
		await ops.send_long(setup, "\n".join(lines))
		if galaxy.started:
			await ops.update_fleet_status(guild, galaxy)

	@app_commands.command(
		name="startinitialization",
		description="Start setting up a new game (pregame lobby + ships)",
		extras=GM,
	)
	@app_commands.describe(
		player_limit="How many players (not counting control) can enter the lobby",
		file1="Ship JSON files. Attach none to load the default fleet from shipdefs/",
	)
	@game_starter_only()
	async def startinitialization(
		self,
		interaction: discord.Interaction,
		player_limit: app_commands.Range[int, 0, 99],
		file1: Attachment = None,
		file2: Attachment = None,
		file3: Attachment = None,
		file4: Attachment = None,
		file5: Attachment = None,
		file6: Attachment = None,
		file7: Attachment = None,
		file8: Attachment = None,
		file9: Attachment = None,
		file10: Attachment = None,
	):
		guild = interaction.guild
		assert guild is not None
		attachments = [
			file1,
			file2,
			file3,
			file4,
			file5,
			file6,
			file7,
			file8,
			file9,
			file10,
		]
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			if galaxy.state not in (STATE_IDLE, STATE_ENDED):
				raise UserError(
					"A game is already being initialized or in progress. Run /endgame first."
				)
			if getattr(interaction.channel, "name", None) != "starting-games":
				raise UserError("Run /startinitialization in #starting-games.")
			ops.check_bot_permissions(guild)

			files = await read_files(attachments) or default_ship_files()
			ships, problems = validate_ship_files(files, Galaxy(guild.id))
			if problems:
				raise UserError(problems_text(problems))

			await interaction.response.defer(ephemeral=True, thinking=True)
			control = await ops.ensure_server(guild, galaxy, self.roles)
			await ops.reset_server(guild, galaxy, self.roles)
			assert isinstance(interaction.user, discord.Member)
			await ops.reset_control(control, keep=interaction.user)

			new = Galaxy(guild.id, state=STATE_INITIALIZING, player_limit=player_limit)
			new.discord.channels = galaxy.discord.channels
			self.galaxies.save(new)
			if ships:
				await self.load_ships(guild, new, ships, files)
			else:
				setup = ops.get_text(guild, new, ch.CONTROL_SETUP)
				await setup.send("No ships loaded yet. Add them with /loadship.")

			starting = ops.get_text(guild, new, ch.STARTING_GAMES)
			message = await starting.send(
				f"A game of **Den of Wolves** is starting! React with {JOIN_EMOJI} to join the pregame lobby."
			)
			await message.add_reaction(JOIN_EMOJI)
			new.discord.start_message_id = message.id
			self.galaxies.save(new)
		await reply(
			interaction,
			f"Initialization started with a player limit of {player_limit} and {len(ships)} ships. "
			"See #control-setup.",
			ephemeral=True,
		)

	@app_commands.command(
		name="loadship",
		description="Add ships to the game from ship JSON files",
		extras=GM,
	)
	@control_only()
	async def loadship(
		self,
		interaction: discord.Interaction,
		file1: discord.Attachment,
		file2: Attachment = None,
		file3: Attachment = None,
		file4: Attachment = None,
		file5: Attachment = None,
		file6: Attachment = None,
		file7: Attachment = None,
		file8: Attachment = None,
		file9: Attachment = None,
		file10: Attachment = None,
	):
		guild = interaction.guild
		assert guild is not None
		attachments = [
			file1,
			file2,
			file3,
			file4,
			file5,
			file6,
			file7,
			file8,
			file9,
			file10,
		]
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			if galaxy.state in (STATE_IDLE, STATE_ENDED):
				raise UserError("No game is being initialized or in progress.")
			files = await read_files(attachments)
			ships, problems = validate_ship_files(files, galaxy)
			if problems:
				raise UserError(problems_text(problems))
			await interaction.response.defer(ephemeral=True, thinking=True)
			await self.load_ships(guild, galaxy, ships, files)
			self.galaxies.save(galaxy)
		await reply(
			interaction, f"Loaded {', '.join(s.name for s in ships)}.", ephemeral=True
		)

	@app_commands.command(
		name="unloadship",
		description="Remove a ship loaded by mistake (initialization only)",
		extras=GM,
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def unloadship(self, interaction: discord.Interaction, ship_name: str):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			if galaxy.state != STATE_INITIALIZING:
				raise UserError(
					"Ships can only be unloaded during initialization. Use /destroyship instead."
				)
			ship = galaxy.ship_by_name(ship_name)
			if ship is None:
				raise UserError(f'There is no ship named "{ship_name}".')
			await interaction.response.defer(ephemeral=True, thinking=True)
			galaxy.remove_ship(ship.id)
			await ops.place_council_and_brig(guild, galaxy)
			await ops.delete_ship_channels(guild, galaxy, ship.id, delete_role=True)
			self.galaxies.save(galaxy)
			setup = ops.get_text(guild, galaxy, ch.CONTROL_SETUP)
			await setup.send(
				f"Unloaded **{ship.name}** `{ship.id}`. Its IDs are free to load again."
			)
		await reply(interaction, f"Unloaded {ship.name}.", ephemeral=True)

	@app_commands.command(
		name="playerlimit",
		description="Change the pregame lobby's player limit",
		extras=GM,
	)
	@control_only()
	async def playerlimit(
		self, interaction: discord.Interaction, num: app_commands.Range[int, 0, 99]
	):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			if galaxy.state != STATE_INITIALIZING:
				await reply(
					interaction,
					"The player limit only has an effect during initialization.",
					ephemeral=True,
				)
				return
			galaxy.player_limit = num
			await interaction.response.defer(ephemeral=True)
			# Raising the limit admits people from the queue; lowering it boots nobody.
			await ops.fill_lobby(guild, galaxy, self.roles)
			self.galaxies.save(galaxy)
			waiting = len(galaxy.discord.queue)
		await reply(
			interaction,
			f"Player limit set to {num}. {waiting} still queued.",
			ephemeral=True,
		)

	@app_commands.command(
		name="gm",
		description="Become or request control, or (as control) make someone a controller",
	)
	@app_commands.describe(user="Control only: the user to make a controller")
	async def gm(
		self, interaction: discord.Interaction, user: discord.Member | None = None
	):
		guild = interaction.guild
		assert guild is not None and isinstance(interaction.user, discord.Member)
		control = ops.get_control_role(guild, self.roles)
		if control is None:
			control = await guild.create_role(
				name=self.roles.CONTROL, reason="ADAMA control role"
			)
		await ops.grant_gm_commands(control)

		if user is not None:
			if not is_gm(interaction.user, self.roles):
				raise UserError("Only control can make someone else a controller.")
			if is_gm(user, self.roles):
				raise UserError(f"{user.mention} is already a controller.")
			await user.add_roles(control, reason=f"/gm by {interaction.user}")
			await reply(
				interaction, f"{user.mention} is now a controller.", ephemeral=True
			)
			return

		if is_gm(interaction.user, self.roles):
			raise UserError("You are already a controller.")
		if not control.members:
			await interaction.user.add_roles(control, reason="/gm: first controller")
			await reply(
				interaction,
				"There was no control, so you are now the first controller.",
				ephemeral=True,
			)
			return
		chat = ops.get_text(guild, self.galaxy(interaction), ch.CONTROL_CHAT)
		message = await chat.send(
			f"{interaction.user.mention} asks to become a controller. Approve with "
			f"`/gm user:@{interaction.user.name}`."
			+ ops.channel_link(interaction.channel),
			allowed_mentions=discord.AllowedMentions.none(),
		)
		await ops.offer_call(message)
		await reply(
			interaction, "Your request has been sent to control.", ephemeral=True
		)

	@app_commands.command(
		name="startgame",
		description="Start the game: create players from the lobby's roles",
		extras=GM,
	)
	@control_only()
	async def startgame(self, interaction: discord.Interaction):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			lobby = [
				LobbyMember(member.id, frozenset(role.name for role in member.roles))
				for user_id in galaxy.discord.admitted
				if (member := guild.get_member(user_id)) is not None
			]
			players, left_out, notices = start_game(galaxy, lobby, self.roles)
			await interaction.response.defer(ephemeral=True, thinking=True)
			for player in players:
				player.pda_channel_id = galaxy.discord.pdas.get(player.discord_user_id)
			for user_id in left_out:
				await ops.delete_pda(guild, galaxy, user_id)
			await ops.close_lobby(guild, galaxy)
			await ops.set_role_permissions(guild, galaxy, self.roles)
			self.galaxies.save(galaxy)
			for player in players:
				await ops.sync_player(guild, galaxy, player)
			await ops.post_notices(guild, galaxy, notices)
			await ops.update_fleet_status(guild, galaxy)
			self.galaxies.save(galaxy)
		await reply(
			interaction,
			f"The game has started with {len(players)} players. "
			f"{len(left_out)} lobby members had no game role and were left out.",
			ephemeral=True,
		)

	@app_commands.command(name="endgame", description="End the game", extras=GM)
	@control_only()
	async def endgame(self, interaction: discord.Interaction):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			if galaxy.state in (STATE_IDLE, STATE_ENDED):
				raise UserError("No game in progress.")
			await interaction.response.defer(ephemeral=True)
			await ops.delete_start_message(guild, galaxy)
			galaxy.discord.queue.clear()
			if not galaxy.started:
				# Still being set up: there is nothing to open up or announce.
				galaxy.state = STATE_IDLE
				self.galaxies.save(galaxy)
				await reply(
					interaction,
					"Game setup cancelled. /startinitialization can be run again.",
					ephemeral=True,
				)
				return

			deleted: list[int] = []
			if any(c.locked_down for c in galaxy.coordinates.values()):
				deleted = end_lockdown(galaxy, None).deleted_channels
			galaxy.launches.clear()
			galaxy.jumps.clear()
			drop_stale_countdowns(galaxy)  # clears the launching and jumping flags
			galaxy.state = STATE_ENDED
			self.galaxies.save(galaxy)
			await ops.delete_channels(guild, deleted)
			await ops.open_to_everyone(guild, galaxy)
			await ops.announce_everywhere(guild, galaxy, GAME_OVER)
			await ops.refresh_fleet_status(guild, galaxy)
		await reply(
			interaction,
			"The game has ended: every channel is open and only /status and /control "
			"work. /startinitialization can be run again.",
			ephemeral=True,
		)

	# Server preparation: the permanent channels (including #starting-games, where
	# /startinitialization is run) must exist before any game can start.

	async def prepare_server(self, guild: discord.Guild) -> None:
		if not guild.me.guild_permissions.administrator:
			logger.warning(
				"Not preparing %s: ADAMA needs Administrator there", guild.name
			)
			return
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxies.get(guild.id)
			await ops.ensure_server(guild, galaxy, self.roles)
			# Countdowns don't survive a restart: cancel any left half-done.
			stale = drop_stale_countdowns(galaxy)
			self.galaxies.save(galaxy)
			await ops.post_notices(guild, galaxy, stale)
			# Once per start-up (not on every reconnect): tell everyone it's back.
			if guild.id not in self.back_online_sent:
				self.back_online_sent.add(guild.id)
				await self.announce_back_online(guild, galaxy, bool(stale))

	async def announce_back_online(
		self, guild: discord.Guild, galaxy: Galaxy, cancelled: bool
	) -> None:
		text = back_online_alert(galaxy, cancelled)
		broadcasts = ops.get_channel(guild, galaxy, ch.CONTROL_BROADCASTS)
		if text is None or not isinstance(broadcasts, discord.TextChannel):
			return
		try:
			await broadcasts.send(
				text, allowed_mentions=discord.AllowedMentions(everyone=True)
			)
		except discord.HTTPException:
			logger.warning("Couldn't post the back-online alert in %s", guild.name)

	@commands.Cog.listener()
	async def on_guild_available(self, guild: discord.Guild):
		await self.prepare_server(guild)

	@commands.Cog.listener()
	async def on_guild_join(self, guild: discord.Guild):
		await self.prepare_server(guild)

	# Players leaving and rejoining the server mid-game. Their character stays put.

	@commands.Cog.listener()
	async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
		guild = self.bot.get_guild(payload.guild_id)
		galaxy = self.galaxies.get(payload.guild_id)
		player = galaxy.player_by_user(payload.user.id) if galaxy.started else None
		if guild is None or player is None:
			return
		chat = ops.get_channel(guild, galaxy, ch.CONTROL_CHAT)
		if isinstance(chat, discord.TextChannel):
			message = await chat.send(
				f"{payload.user.mention} ({payload.user.name}) left the server. Their "
				f"character stays at {location_name(galaxy, player)}; if they rejoin "
				"they get their roles and channels back."
				+ ops.channel_link(guild.get_channel(player.pda_channel_id or 0)),
				allowed_mentions=discord.AllowedMentions.none(),
			)
			await ops.offer_call(message)

	@commands.Cog.listener()
	async def on_member_join(self, member: discord.Member):
		galaxy = self.galaxies.get(member.guild.id)
		player = galaxy.player_by_user(member.id) if galaxy.started else None
		if player is None:
			return
		async with self.galaxies.lock(member.guild.id):
			await ops.restore_player(member.guild, galaxy, player, self.roles)
		chat = ops.get_channel(member.guild, galaxy, ch.CONTROL_CHAT)
		if isinstance(chat, discord.TextChannel):
			await chat.send(
				f"{member.mention} rejoined the server: their roles and channels have "
				f"been restored ({location_name(galaxy, player)}).",
				allowed_mentions=discord.AllowedMentions.none(),
			)

	# Pregame lobby: reacting to the message in #starting-games joins the queue.

	def _lobby_galaxy(
		self, payload: discord.RawReactionActionEvent
	) -> tuple[discord.Guild, Galaxy] | None:
		if (
			payload.guild_id is None
			or self.bot.user is None
			or payload.user_id == self.bot.user.id
		):
			return None
		if str(payload.emoji) != JOIN_EMOJI:
			return None
		galaxy = self.galaxies.get(payload.guild_id)
		guild = self.bot.get_guild(payload.guild_id)
		if guild is None or galaxy.state != STATE_INITIALIZING:
			return None
		if payload.message_id != galaxy.discord.start_message_id:
			return None
		return guild, galaxy

	@commands.Cog.listener()
	async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
		found = self._lobby_galaxy(payload)
		if found is None:
			return
		guild, galaxy = found
		member = payload.member or guild.get_member(payload.user_id)
		if member is None:
			return
		async with self.galaxies.lock(guild.id):
			lobby = galaxy.discord
			if member.id in lobby.queue or member.id in lobby.admitted:
				return
			if is_gm(member, self.roles):
				await ops.admit_to_lobby(guild, galaxy, member)
			else:
				lobby.queue.append(member.id)
				await ops.fill_lobby(guild, galaxy, self.roles)
			self.galaxies.save(galaxy)

	@commands.Cog.listener()
	async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
		found = self._lobby_galaxy(payload)
		if found is None:
			return
		guild, galaxy = found
		member = guild.get_member(payload.user_id)
		async with self.galaxies.lock(guild.id):
			lobby = galaxy.discord
			if payload.user_id in lobby.queue:
				lobby.queue.remove(payload.user_id)
			elif payload.user_id in lobby.admitted:
				if member is None:
					lobby.admitted.remove(payload.user_id)
				else:
					await ops.remove_from_lobby(guild, galaxy, member)
					roles = [
						role.name for role in member.roles if not role.is_default()
					]
					if roles:
						chat = ops.get_channel(guild, galaxy, ch.CONTROL_CHAT)
						if isinstance(chat, discord.TextChannel):
							await chat.send(
								f"{member.mention} has left the pregame lobby. Their roles: {', '.join(roles)}.",
								allowed_mentions=discord.AllowedMentions.none(),
							)
				await ops.fill_lobby(guild, galaxy, self.roles)
			self.galaxies.save(galaxy)


async def setup(bot: AdamaBot):
	cog = GameCommands(bot)
	await bot.add_cog(cog)

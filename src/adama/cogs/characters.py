import logging

import discord
from discord import app_commands
from discord.ext import commands

from adama import channels as ch
from adama import discord_ops as ops
from adama.bot import AdamaBot, defer, reply
from adama.cogs._shared import PRESIDENT_ONLY, choices, player_of, ship_named
from adama.models.galaxy import STATE_INITIALIZING, Galaxy
from adama.models.player import Player
from adama.perms import UserError, control_only, is_gm
from adama.rules import Notice
from adama.rules import characters as char_rules
from adama.rules import combat as combat_rules
from adama.rules import council as council_rules

logger = logging.getLogger()

GM = {"gm": True}
OFFICE_CHOICES = [
	app_commands.Choice(name="president", value=council_rules.PRESIDENT),
	app_commands.Choice(name="councillor", value=council_rules.COUNCILLOR),
]


class CharacterCommands(commands.Cog):
	"""Wounds, death, the brig and the council."""

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
		*,
		sync: list[Player] | None = None,
	) -> None:
		"""Saves, updates the players' Discord access, posts the notices and confirms."""
		guild = interaction.guild
		assert guild is not None
		self.galaxies.save(galaxy)
		if not interaction.response.is_done():
			await defer(interaction, ephemeral=True)
		if sync:
			await ops.sync_players(guild, galaxy, sync)
		await ops.post_notices(guild, galaxy, notices, self.roles)
		await ops.update_fleet_status(guild, galaxy)
		await reply(interaction, done, ephemeral=True)

	async def ship_autocomplete(self, interaction: discord.Interaction, current: str):
		galaxy = self.galaxy(interaction)
		return choices(
			[(s.name, s.name) for s in galaxy.ships.values() if not s.destroyed],
			current,
		)

	# Health

	@app_commands.command(
		name="wound",
		description="Wound a player: they can't travel, launch or intercept until healed",
		extras=GM,
	)
	@control_only()
	async def wound(self, interaction: discord.Interaction, player: discord.Member):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			target = player_of(galaxy, player)
			notices = char_rules.wound(galaxy, target)
			await self.finish(
				interaction, galaxy, notices, f"{player.mention} is wounded."
			)

	@app_commands.command(name="heal", description="Heal a wounded player", extras=GM)
	@control_only()
	async def heal(self, interaction: discord.Interaction, player: discord.Member):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			notices = char_rules.heal(player_of(galaxy, player))
			await self.finish(
				interaction, galaxy, notices, f"{player.mention} is healed."
			)

	@app_commands.command(
		name="kill",
		description="Kill a player (their cards stay on their ghost; /resurrect undoes it)",
		extras=GM,
	)
	@control_only()
	async def kill(self, interaction: discord.Interaction, player: discord.Member):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			target = player_of(galaxy, player)
			notices = char_rules.kill(galaxy, target)
			await defer(interaction, ephemeral=True)
			removed = await ops.strip_game_roles(guild, galaxy, target, self.roles)
			assert target.stored_state is not None
			target.stored_state["roles"] = removed
			await self.finish(
				interaction,
				galaxy,
				notices,
				f"{player.mention} is dead.",
				sync=[target],
			)

	@app_commands.command(
		name="resurrect",
		description="Bring a dead player back, with everything they had",
		extras=GM,
	)
	@app_commands.describe(
		ship_name="Where they wake up (default: their first crew ship)"
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def resurrect(
		self,
		interaction: discord.Interaction,
		player: discord.Member,
		ship_name: str | None = None,
	):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			target = player_of(galaxy, player)
			ship = ship_named(galaxy, ship_name) if ship_name else None
			notices = char_rules.resurrect(galaxy, target, ship)
			await defer(interaction, ephemeral=True)
			await ops.give_roles(guild, target, char_rules.stored_roles(target))
			char_rules.clear_stored_state(target)
			await self.finish(
				interaction,
				galaxy,
				notices,
				f"{player.mention} has been resurrected.",
				sync=[target],
			)

	# The brig

	@app_commands.command(
		name="brig", description="Put a player in the brig", extras=GM
	)
	@control_only()
	async def brig(self, interaction: discord.Interaction, player: discord.Member):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			target = player_of(galaxy, player)
			notices = char_rules.brig(galaxy, target)
			await self.finish(
				interaction,
				galaxy,
				notices,
				f"{player.mention} is in the brig.",
				sync=[target],
			)

	@app_commands.command(
		name="unbrig",
		description="Release a player into the hangar of the ship the brig is on",
		extras=GM,
	)
	@control_only()
	async def unbrig(self, interaction: discord.Interaction, player: discord.Member):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			target = player_of(galaxy, player)
			notices = char_rules.unbrig(galaxy, target)
			await self.finish(
				interaction,
				galaxy,
				notices,
				f"{player.mention} has been released.",
				sync=[target],
			)

	@app_commands.command(
		name="movebrig", description="Move the brig to another ship", extras=GM
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	@control_only()
	async def movebrig(self, interaction: discord.Interaction, ship_name: str):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_named(galaxy, ship_name)
			old = galaxy.brig_ship
			notices = char_rules.move_brig(galaxy, ship)
			await defer(interaction, ephemeral=True)
			await ops.place_council_and_brig(guild, galaxy)
			# Whoever is aboard the old and new brig ships gains or loses the brig.
			aboard = [
				p for p in galaxy.players.values() if p.location in (old, ship.id)
			]
			await self.finish(
				interaction,
				galaxy,
				notices,
				f"The brig is now on the {ship.name}.",
				sync=aboard,
			)

	# The council (president)

	def president(self, interaction: discord.Interaction) -> Player | None:
		"""Who runs a president's command: None for control, who may stand in."""
		if is_gm(interaction.user, self.roles):
			return None
		return player_of(self.galaxy(interaction), interaction.user)

	@app_commands.command(
		name="summoncouncil",
		extras=PRESIDENT_ONLY,
		description="President: call every councillor to the council chambers",
	)
	async def summoncouncil(self, interaction: discord.Interaction):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			notices = council_rules.summon(galaxy, self.president(interaction))
			await self.finish(interaction, galaxy, notices, "The council is summoned.")

	@app_commands.command(
		name="relocate",
		extras=PRESIDENT_ONLY,
		description="President: move the council chambers to another ship",
	)
	@app_commands.autocomplete(ship_name=ship_autocomplete)
	async def relocate(self, interaction: discord.Interaction, ship_name: str):
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			ship = ship_named(galaxy, ship_name)
			change = council_rules.relocate(galaxy, self.president(interaction), ship)
			await defer(interaction, ephemeral=True)
			await ops.place_council_and_brig(guild, galaxy)
			await self.finish(
				interaction,
				galaxy,
				change.notices,
				f"The chambers are now on the {ship.name}.",
				sync=change.resync,
			)
			await ops.verify_channel_access(
				guild, galaxy, (ch.COUNCIL_CHAMBERS, ch.VC_COUNCIL_CHAMBERS)
			)

	@app_commands.command(
		name="seal",
		extras=PRESIDENT_ONLY,
		description="President: clear the chambers of everyone not on the council or permitted",
	)
	async def seal(self, interaction: discord.Interaction):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			change = council_rules.seal(galaxy, self.president(interaction))
			await self.finish(
				interaction,
				galaxy,
				change.notices,
				"The chambers are sealed until /unseal or the end of the turn.",
				sync=change.resync,
			)

	@app_commands.command(
		name="unseal",
		extras=PRESIDENT_ONLY,
		description="President: unseal the chambers",
	)
	async def unseal(self, interaction: discord.Interaction):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			change = council_rules.unseal(galaxy, self.president(interaction))
			await self.finish(
				interaction,
				galaxy,
				change.notices,
				"The chambers are unsealed.",
				sync=change.resync,
			)

	@app_commands.command(
		name="permit",
		extras=PRESIDENT_ONLY,
		description="President: let a player speak in the chambers",
	)
	async def permit(self, interaction: discord.Interaction, player: discord.Member):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			change = council_rules.permit(
				galaxy, self.president(interaction), player_of(galaxy, player)
			)
			await self.finish(
				interaction,
				galaxy,
				change.notices,
				f"{player.mention} may speak.",
				sync=change.resync,
			)

	@app_commands.command(
		name="gag",
		extras=PRESIDENT_ONLY,
		description="President: stop a permitted player speaking in the chambers",
	)
	async def gag(self, interaction: discord.Interaction, player: discord.Member):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			change = council_rules.gag(
				galaxy, self.president(interaction), player_of(galaxy, player)
			)
			await self.finish(
				interaction,
				galaxy,
				change.notices,
				f"{player.mention} may no longer speak.",
				sync=change.resync,
			)

	# Elections (control)

	def office_role(self, office: str) -> str:
		return {
			council_rules.PRESIDENT: self.roles.PRESIDENT,
			council_rules.COUNCILLOR: self.roles.COUNCILLOR,
			combat_rules.FIRST_RESPONDER: self.roles.FIRST_RESPONDER,
		}[office]

	async def _lobby_offices(
		self,
		interaction: discord.Interaction,
		galaxy: Galaxy,
		member: discord.Member,
		offices: list[str],
		given: bool,
		done: str,
	) -> bool:
		"""Before /startgame there are no players yet: offices are set by giving or
		removing the office role, which /startgame reads. Returns False once the game
		has started (the caller then changes the player)."""
		if galaxy.started:
			return False
		if galaxy.state != STATE_INITIALIZING:
			raise UserError("There is no game being set up or in progress.")
		if member.id not in galaxy.discord.admitted:
			raise UserError(f"{member.mention} isn't in the pregame lobby.")
		names = [self.office_role(office) for office in offices]
		holds = [name for name in names if any(r.name == name for r in member.roles)]
		if given and holds:
			raise UserError(f"{member.mention} already has the {holds[0]} role.")
		if not given and not holds:
			raise UserError(f"{member.mention} doesn't hold that office.")
		for name in names:
			await ops.set_member_role(member, name, given)
		await reply(
			interaction, done + " It takes effect at /startgame.", ephemeral=True
		)
		return True

	async def _apply(
		self,
		interaction: discord.Interaction,
		galaxy: Galaxy,
		change: council_rules.CouncilChange,
		done: str,
	) -> None:
		guild = interaction.guild
		assert guild is not None
		await defer(interaction, ephemeral=True)
		for player, office, given in change.roles:
			await ops.set_role(guild, player, self.office_role(office), given)
		await self.finish(interaction, galaxy, change.notices, done, sync=change.resync)

	@app_commands.command(
		name="electpresident",
		description="Make a player president (nobody is deposed, nothing is announced)",
		extras=GM,
	)
	@control_only()
	async def electpresident(
		self, interaction: discord.Interaction, player: discord.Member
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			done = f"{player.mention} is now president."
			if await self._lobby_offices(
				interaction, galaxy, player, [council_rules.PRESIDENT], True, done
			):
				return
			change = council_rules.elect(
				player_of(galaxy, player), council_rules.PRESIDENT
			)
			await self._apply(interaction, galaxy, change, done)

	@app_commands.command(
		name="electcouncillor", description="Make a player a councillor", extras=GM
	)
	@control_only()
	async def electcouncillor(
		self, interaction: discord.Interaction, player: discord.Member
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			done = f"{player.mention} is now a councillor."
			if await self._lobby_offices(
				interaction, galaxy, player, [council_rules.COUNCILLOR], True, done
			):
				return
			change = council_rules.elect(
				player_of(galaxy, player), council_rules.COUNCILLOR
			)
			await self._apply(interaction, galaxy, change, done)

	@app_commands.command(
		name="depose",
		description="Remove a player from office (leave the office out to remove both)",
		extras=GM,
	)
	@app_commands.choices(office=OFFICE_CHOICES)
	@control_only()
	async def depose(
		self,
		interaction: discord.Interaction,
		player: discord.Member,
		office: str | None = None,
	):
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			done = f"{player.mention} has been deposed."
			offices = [office] if office else list(council_rules.OFFICES)
			if await self._lobby_offices(
				interaction, galaxy, player, offices, False, done
			):
				return
			change = council_rules.depose(player_of(galaxy, player), office)
			await self._apply(interaction, galaxy, change, done)

	async def _first_responder(
		self, interaction: discord.Interaction, member: discord.Member, given: bool
	) -> None:
		guild = interaction.guild
		assert guild is not None
		async with self.galaxies.lock(guild.id):
			galaxy = self.galaxy(interaction)
			done = (
				f"{member.mention} is now a first responder."
				if given
				else f"{member.mention} is no longer a first responder."
			)
			if await self._lobby_offices(
				interaction, galaxy, member, [combat_rules.FIRST_RESPONDER], given, done
			):
				return
			player = player_of(galaxy, member)
			notices = combat_rules.set_first_responder(player, given)
			await defer(interaction, ephemeral=True)
			await ops.set_role(guild, player, self.roles.FIRST_RESPONDER, given)
			await self.finish(interaction, galaxy, notices, done, sync=[player])

	@app_commands.command(
		name="setfirstresponder",
		description="Make a player a first responder (summoned to the battlemap in a lockdown)",
		extras=GM,
	)
	@control_only()
	async def setfirstresponder(
		self, interaction: discord.Interaction, player: discord.Member
	):
		await self._first_responder(interaction, player, True)

	@app_commands.command(
		name="unsetfirstresponder",
		description="Stop a player being a first responder",
		extras=GM,
	)
	@control_only()
	async def unsetfirstresponder(
		self, interaction: discord.Interaction, player: discord.Member
	):
		await self._first_responder(interaction, player, False)

	# Wolves (control): silent, nobody is told.

	async def _wolf(
		self, interaction: discord.Interaction, member: discord.Member, wolf: bool
	) -> None:
		assert interaction.guild is not None
		async with self.galaxies.lock(interaction.guild.id):
			galaxy = self.galaxy(interaction)
			if galaxy.started:
				text = char_rules.set_wolf(player_of(galaxy, member), wolf)
			elif galaxy.state == STATE_INITIALIZING:
				text = char_rules.set_lobby_wolf(galaxy, member.id, wolf)
			else:
				raise UserError("There is no game being set up or in progress.")
			self.galaxies.save(galaxy)
		await reply(interaction, text, ephemeral=True)

	@app_commands.command(
		name="setwolf", description="Secretly make a player a wolf", extras=GM
	)
	@control_only()
	async def setwolf(self, interaction: discord.Interaction, player: discord.Member):
		await self._wolf(interaction, player, True)

	@app_commands.command(
		name="unsetwolf", description="Secretly stop a player being a wolf", extras=GM
	)
	@control_only()
	async def unsetwolf(self, interaction: discord.Interaction, player: discord.Member):
		await self._wolf(interaction, player, False)

	@app_commands.command(
		name="wolves", description="List the wolves (only you see it)", extras=GM
	)
	@control_only()
	async def wolves(self, interaction: discord.Interaction):
		await reply(
			interaction,
			char_rules.wolves_list(self.galaxy(interaction)),
			ephemeral=True,
		)


async def setup(bot: AdamaBot):
	await bot.add_cog(CharacterCommands(bot))

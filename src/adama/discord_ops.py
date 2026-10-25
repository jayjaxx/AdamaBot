"""Discord side effects: permanent channels, ship channels, PDAs and the pregame lobby.

Game rules live in the models; this module only mirrors the game state onto a server.
The bot needs the Administrator permission.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any
import logging
import re

import discord

from adama import channels as ch
from adama.access import Access, managed_channels, player_access
from adama.config import RolesConfig
from adama.models.galaxy import Galaxy
from adama.models.player import Player
from adama.models.ship import Ship
from adama.rules import BRIDGE, BRIG, BROADCAST, CONTROL, HANGAR, PDA, Notice
from adama.perms import GM_COMMAND_PERMISSION, UserError, is_gm
from adama.status import fleet_status, split_message

logger = logging.getLogger(__name__)

NO_MENTIONS = discord.AllowedMentions.none()

GuildChannel = (
	discord.TextChannel
	| discord.VoiceChannel
	| discord.StageChannel
	| discord.ForumChannel
	| discord.CategoryChannel
)


def get_channel(guild: discord.Guild, galaxy: Galaxy, key: str) -> GuildChannel | None:
	channel_id = galaxy.discord.channels.get(key)
	return guild.get_channel(channel_id) if channel_id is not None else None


def get_text(guild: discord.Guild, galaxy: Galaxy, key: str) -> discord.TextChannel:
	channel = get_channel(guild, galaxy, key)
	if not isinstance(channel, discord.TextChannel):
		raise UserError(
			f"The #{ch.SPECS[key].name} channel is missing. Run /startinitialization to recreate it."
		)
	return channel


def get_control_role(guild: discord.Guild, roles: RolesConfig) -> discord.Role | None:
	return discord.utils.get(guild.roles, name=roles.CONTROL)


def check_bot_permissions(guild: discord.Guild) -> None:
	if not guild.me.guild_permissions.administrator:
		raise UserError("ADAMA needs the Administrator permission on this server.")


def _overwrites(
	guild: discord.Guild, visibility: str, control: discord.Role
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
	if visibility == ch.SERVER_DEFAULT:
		return {}
	if visibility == ch.PUBLIC_READ:
		return {
			guild.default_role: discord.PermissionOverwrite(
				view_channel=True,
				send_messages=False,
				add_reactions=False,
				read_message_history=True,
			),
			control: discord.PermissionOverwrite(view_channel=True, send_messages=True),
		}
	return {
		guild.default_role: discord.PermissionOverwrite(view_channel=False),
		control: discord.PermissionOverwrite(view_channel=True),
	}


async def _create(
	guild: discord.Guild,
	kind: str,
	name: str,
	category: discord.CategoryChannel | None,
	overwrites: dict | None = None,
) -> GuildChannel:
	kwargs: dict = {"overwrites": overwrites} if overwrites is not None else {}
	if kind == "category":
		return await guild.create_category(name, **kwargs)
	if kind == ch.TEXT:
		return await guild.create_text_channel(name, category=category, **kwargs)
	if kind == ch.STAGE:
		try:
			return await guild.create_stage_channel(name, category=category, **kwargs)
		except discord.HTTPException:
			# Stage channels need a Community server.
			logger.info(
				"Could not create stage channel %s; using a voice channel", name
			)
	return await guild.create_voice_channel(name, category=category, **kwargs)


def _matches(channel: GuildChannel, kind: str) -> bool:
	return {
		"category": isinstance(channel, discord.CategoryChannel),
		ch.TEXT: isinstance(channel, discord.TextChannel),
		ch.VOICE: isinstance(channel, (discord.VoiceChannel, discord.StageChannel)),
		ch.STAGE: isinstance(channel, (discord.VoiceChannel, discord.StageChannel)),
	}[kind]


def _normalise(name: str, kind: str) -> str:
	# Discord lowercases text channel names and replaces spaces with hyphens.
	return name.lower().replace(" ", "-") if kind == ch.TEXT else name


async def grant_gm_commands(role: discord.Role) -> None:
	"""Gives the role the permission that shows the control commands (see perms.py)."""
	if role.permissions >= GM_COMMAND_PERMISSION:
		return
	try:
		await role.edit(
			permissions=role.permissions | GM_COMMAND_PERMISSION,
			reason="ADAMA: lets this role see the control commands",
		)
	except discord.Forbidden:
		logger.warning(
			"Can't edit the %s role in %s: move ADAMA's role above it, or players "
			"with it won't see the control commands",
			role.name,
			role.guild.name,
		)


async def ensure_server(
	guild: discord.Guild, galaxy: Galaxy, roles: RolesConfig
) -> discord.Role:
	"""Finds or creates the control role, the office roles and every permanent
	channel, storing the channel IDs."""
	control = get_control_role(guild, roles)
	if control is None:
		control = await guild.create_role(
			name=roles.CONTROL, reason="ADAMA control role"
		)
	# Office roles exist from the start, so control can hand them out in the lobby.
	for name in office_role_names(roles):
		if discord.utils.get(guild.roles, name=name) is None:
			await guild.create_role(name=name, reason="ADAMA office role")
	await grant_gm_commands(control)
	starter = discord.utils.get(guild.roles, name=roles.GAME_STARTER)
	if starter is not None:
		await grant_gm_commands(starter)

	for spec in [*ch.CATEGORIES, *ch.CHANNELS]:
		existing = get_channel(guild, galaxy, spec.key)
		if existing is None or not _matches(existing, spec.kind):
			existing = next(
				(
					c
					for c in guild.channels
					if c.name == _normalise(spec.name, spec.kind)
					and _matches(c, spec.kind)
				),
				None,
			)
		if existing is None:
			category: discord.CategoryChannel | None = None
			overwrites: dict | None = _overwrites(guild, spec.visibility, control)
			if spec.category is not None:
				parent = get_channel(guild, galaxy, spec.category)
				assert isinstance(parent, discord.CategoryChannel)
				category = parent
				# Inherit the category's permissions when they're the same.
				if ch.SPECS[spec.category].visibility == spec.visibility:
					overwrites = None
			existing = await _create(guild, spec.kind, spec.name, category, overwrites)
			logger.info("Created #%s in %s", spec.name, guild.name)
		elif (
			spec.category is not None
			and ch.SPECS[spec.category].visibility == ch.SERVER_DEFAULT
			and isinstance(existing, discord.TextChannel)
			and existing.permissions_synced
		):
			# Older setups had this channel copy its category's permissions; give it its
			# own so the category's other channels are left to the server.
			for target, overwrite in _overwrites(
				guild, spec.visibility, control
			).items():
				await existing.set_permissions(
					target,
					overwrite=overwrite,
					reason="ADAMA: #starting-games keeps its own permissions",
				)
		galaxy.discord.channels[spec.key] = existing.id
	return control


async def clear_channel(guild: discord.Guild, galaxy: Galaxy, key: str) -> None:
	"""Empties a channel by replacing it with a copy (much faster than deleting messages)."""
	channel = get_channel(guild, galaxy, key)
	if not isinstance(
		channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)
	):
		return
	copy = await channel.clone(reason="ADAMA: clearing for a new game")
	await copy.edit(position=channel.position)
	await channel.delete(reason="ADAMA: clearing for a new game")
	galaxy.discord.channels[key] = copy.id


async def _move(channel: GuildChannel | None, category: GuildChannel | None) -> None:
	if not isinstance(
		channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)
	):
		return
	if not isinstance(category, discord.CategoryChannel):
		return
	if channel.category_id != category.id:
		await channel.edit(category=category, sync_permissions=False)


async def place_council_and_brig(guild: discord.Guild, galaxy: Galaxy) -> None:
	"""Puts the council chambers channels and the brig channels in their ships' categories, or back in
	the Fleet category when no ship holds them."""
	home = get_channel(guild, galaxy, ch.CAT_FLEET)

	def category_of(ship_id: str | None) -> GuildChannel | None:
		if ship_id is None:
			return home
		category_id = galaxy.discord.ship_channels.get(ship_id, {}).get(
			ch.SHIP_CATEGORY
		)
		category = guild.get_channel(category_id) if category_id else None
		return category or home

	council = category_of(galaxy.council_chambers_ship)
	brig = category_of(galaxy.brig_ship)
	await _move(get_channel(guild, galaxy, ch.COUNCIL_CHAMBERS), council)
	await _move(get_channel(guild, galaxy, ch.VC_COUNCIL_CHAMBERS), council)
	await _move(get_channel(guild, galaxy, ch.BRIG), brig)
	await _move(get_channel(guild, galaxy, ch.VC_BRIG), brig)


async def create_ship_channels(
	guild: discord.Guild, galaxy: Galaxy, ship: Ship, control: discord.Role
) -> None:
	"""Creates the ship's category, channels, VCs and crew role, as /conjureship does."""
	overwrites = _overwrites(guild, ch.HIDDEN, control)
	created: dict[str, int] = {}
	category: discord.CategoryChannel | None = None
	for kind, (channel_type, name) in ch.ship_channel_names(ship.name).items():
		channel = await _create(
			guild,
			channel_type,
			name,
			category,
			overwrites if category is None else None,
		)
		if isinstance(channel, discord.CategoryChannel):
			category = channel
		created[kind] = channel.id

	role_name = ch.crew_role_name(ship.name)
	role = discord.utils.get(guild.roles, name=role_name)
	if role is None:
		role = await guild.create_role(
			name=role_name, reason=f"ADAMA: crew of {ship.name}"
		)
	created[ch.SHIP_ROLE] = role.id
	galaxy.discord.ship_channels[ship.id] = created


async def delete_ship_channels(
	guild: discord.Guild, galaxy: Galaxy, ship_id: str, *, delete_role: bool
) -> None:
	"""Deletes a ship's channels and category. Move the council chambers and brig out first
	(see place_council_and_brig) or they are deleted with it."""
	created = galaxy.discord.ship_channels.get(ship_id, {})
	for kind, channel_id in created.items():
		if kind in (ch.SHIP_CATEGORY, ch.SHIP_ROLE):
			continue
		channel = guild.get_channel(channel_id)
		if channel is not None:
			await channel.delete(reason="ADAMA: removing ship")
	category = guild.get_channel(created.get(ch.SHIP_CATEGORY, 0))
	if category is not None:
		await category.delete(reason="ADAMA: removing ship")
	if delete_role:
		role = guild.get_role(created.get(ch.SHIP_ROLE, 0))
		if role is not None:
			await role.delete(reason="ADAMA: removing ship")
	galaxy.discord.ship_channels.pop(ship_id, None)


async def strip_member_overwrites(channel: GuildChannel) -> None:
	"""Removes every per-user permission, keeping the per-role ones."""
	kept = {
		target: o
		for target, o in channel.overwrites.items()
		if isinstance(target, discord.Role)
	}
	if len(kept) != len(channel.overwrites):
		await channel.edit(overwrites=kept)


PDA_CATEGORY_PATTERN = re.compile(rf"{ch.PDA_CATEGORY}( \d+)?")


def is_ship_category(name: str, text_channel_names: set[str]) -> bool:
	"""Whether a category looks like one ADAMA made for a ship: it holds that ship's
	hangar and bridge text channels."""
	return {
		_normalise(f"{name}-hangar", ch.TEXT),
		_normalise(f"{name}-bridge", ch.TEXT),
	} <= text_channel_names


def is_pda_category(name: str) -> bool:
	return PDA_CATEGORY_PATTERN.fullmatch(name) is not None


async def _delete_category(category: discord.CategoryChannel) -> None:
	for channel in category.channels:
		await channel.delete(reason="ADAMA: new game")
	await category.delete(reason="ADAMA: new game")


async def delete_untracked_leftovers(guild: discord.Guild, galaxy: Galaxy) -> list[str]:
	"""Deletes ship and PDA categories from an earlier game the bot no longer has a record
	of (e.g. after a restart with an in-memory database). Returns their names."""
	known = set(galaxy.discord.channels.values()) | set(galaxy.discord.pda_categories)
	known.update(
		c.get(ch.SHIP_CATEGORY, 0) for c in galaxy.discord.ship_channels.values()
	)
	removed: list[str] = []
	for category in guild.categories:
		if category.id in known:
			continue
		texts = {
			c.name for c in category.channels if isinstance(c, discord.TextChannel)
		}
		if is_ship_category(category.name, texts):
			role = discord.utils.get(guild.roles, name=ch.crew_role_name(category.name))
			if role is not None:
				for member in role.members:
					await member.remove_roles(role, reason="ADAMA: new game")
		elif not is_pda_category(category.name):
			continue
		removed.append(category.name)
		await _delete_category(category)
	return removed


async def reset_server(guild: discord.Guild, galaxy: Galaxy) -> None:
	"""Removes the previous game's ship channels, PDAs, combat channels, lobby message and
	individual permissions, and empties the channels /startinitialization clears."""
	council, brig = galaxy.council_chambers_ship, galaxy.brig_ship
	galaxy.council_chambers_ship = galaxy.brig_ship = None
	await place_council_and_brig(guild, galaxy)
	galaxy.council_chambers_ship, galaxy.brig_ship = council, brig

	for ship_id, created in list(galaxy.discord.ship_channels.items()):
		# Crew roles are kept, but nobody keeps last game's crew.
		role = guild.get_role(created.get(ch.SHIP_ROLE, 0))
		if role is not None:
			for member in role.members:
				await member.remove_roles(role, reason="ADAMA: new game")
		await delete_ship_channels(guild, galaxy, ship_id, delete_role=False)
	leftovers = await delete_untracked_leftovers(guild, galaxy)
	if leftovers:
		logger.info(
			"Deleted untracked leftovers in %s: %s", guild.name, ", ".join(leftovers)
		)

	for channel_id in [*galaxy.discord.pdas.values(), *galaxy.discord.pda_categories]:
		channel = guild.get_channel(channel_id)
		if channel is not None:
			await channel.delete(reason="ADAMA: new game")
	galaxy.discord.pdas.clear()
	galaxy.discord.pda_categories.clear()

	for coords in galaxy.coordinates.values():
		for combat_id in (
			coords.combat_text_channel_id,
			coords.combat_voice_channel_id,
		):
			combat = guild.get_channel(combat_id) if combat_id else None
			if combat is not None:
				await combat.delete(reason="ADAMA: new game")

	await delete_start_message(guild, galaxy)
	for spec in ch.CHANNELS:
		if spec.cleared_on_init:
			await clear_channel(guild, galaxy, spec.key)
		permanent = get_channel(guild, galaxy, spec.key)
		if permanent is not None:
			await strip_member_overwrites(permanent)
	galaxy.discord.fleet_status_message_ids.clear()
	_fleet_status_shown.pop(guild.id, None)


async def reset_control(control: discord.Role, *, keep: discord.Member) -> None:
	"""Leaves `keep` as the only controller, so nobody else can claim control with /gm
	before they approve it."""
	for member in control.members:
		if member.id != keep.id:
			await member.remove_roles(control, reason="ADAMA: new game")
	if control not in keep.roles:
		await keep.add_roles(control, reason="ADAMA: ran /startinitialization")


async def delete_start_message(guild: discord.Guild, galaxy: Galaxy) -> None:
	message_id = galaxy.discord.start_message_id
	galaxy.discord.start_message_id = None
	channel = get_channel(guild, galaxy, ch.STARTING_GAMES)
	if message_id is None or not isinstance(channel, discord.TextChannel):
		return
	try:
		await channel.get_partial_message(message_id).delete()
	except discord.NotFound:
		pass


async def send_long(
	channel: discord.abc.Messageable, text: str, **kwargs
) -> discord.Message:
	"""Sends text split into Discord-sized messages, without pinging anyone unless
	`allowed_mentions` says otherwise. Returns the first."""
	kwargs.setdefault("allowed_mentions", NO_MENTIONS)
	messages = [await channel.send(chunk, **kwargs) for chunk in split_message(text)]
	return messages[0]


# #fleet-status is refreshed this long after the last change, so a burst of commands
# makes one edit instead of one each.
FLEET_STATUS_DELAY = 2.0
_fleet_status_pending: dict[int, asyncio.Task] = {}
# What each server's #fleet-status currently shows, so unchanged messages aren't edited.
_fleet_status_shown: dict[int, list[str]] = {}


async def update_fleet_status(guild: discord.Guild, galaxy: Galaxy) -> None:
	"""Schedules a #fleet-status refresh (returns at once)."""
	pending = _fleet_status_pending.get(guild.id)
	if pending is not None and not pending.done():
		pending.cancel()
	_fleet_status_pending[guild.id] = asyncio.create_task(
		_refresh_fleet_status_later(guild, galaxy)
	)


async def _refresh_fleet_status_later(guild: discord.Guild, galaxy: Galaxy) -> None:
	await asyncio.sleep(FLEET_STATUS_DELAY)
	try:
		await refresh_fleet_status(guild, galaxy)
	except discord.HTTPException:
		logger.exception("Couldn't refresh #fleet-status in %s", guild.name)


async def refresh_fleet_status(guild: discord.Guild, galaxy: Galaxy) -> None:
	channel = get_channel(guild, galaxy, ch.FLEET_STATUS)
	if not isinstance(channel, discord.TextChannel):
		return
	chunks = split_message(fleet_status(galaxy))
	old = galaxy.discord.fleet_status_message_ids
	shown = _fleet_status_shown.get(guild.id, [])
	if len(old) == len(chunks):
		try:
			for n, (message_id, chunk) in enumerate(zip(old, chunks)):
				if n < len(shown) and shown[n] == chunk:
					continue
				await channel.get_partial_message(message_id).edit(content=chunk)
			_fleet_status_shown[guild.id] = chunks
			return
		except discord.NotFound:
			pass
	# Start again. Purging the bot's own messages also catches any whose IDs were
	# never saved (the refresh runs after the command that triggered it has saved).
	await channel.purge(limit=50, check=lambda m: m.author == guild.me)
	_fleet_status_shown[guild.id] = chunks
	galaxy.discord.fleet_status_message_ids = [
		(await channel.send(chunk, allowed_mentions=NO_MENTIONS)).id for chunk in chunks
	]


# Pregame lobby

LOBBY_ACCESS = {
	ch.PREGAME_CHAT: discord.PermissionOverwrite(
		view_channel=True, send_messages=True, read_message_history=True
	),
	ch.ROLE_SELECT: discord.PermissionOverwrite(
		view_channel=True, send_messages=True, read_message_history=True
	),
	ch.VC_PREGAME: discord.PermissionOverwrite(view_channel=True, connect=True),
}
PDA_ACCESS = discord.PermissionOverwrite(
	view_channel=True, send_messages=True, read_message_history=True, attach_files=True
)


async def _pda_category(
	guild: discord.Guild, galaxy: Galaxy
) -> discord.CategoryChannel:
	for category_id in galaxy.discord.pda_categories:
		category = guild.get_channel(category_id)
		if (
			isinstance(category, discord.CategoryChannel)
			and len(category.channels) < ch.CATEGORY_CHANNEL_LIMIT
		):
			return category
	number = len(galaxy.discord.pda_categories) + 1
	name = ch.PDA_CATEGORY if number == 1 else f"{ch.PDA_CATEGORY} {number}"
	category = await guild.create_category(
		name,
		overwrites={
			guild.default_role: discord.PermissionOverwrite(view_channel=False)
		},
	)
	galaxy.discord.pda_categories.append(category.id)
	return category


def stale_pda_owner(galaxy: Galaxy) -> int | None:
	"""The owner of the oldest PDA whose owner has left the lobby, if any."""
	lobby = galaxy.discord
	for owner in lobby.pdas:
		if owner not in lobby.admitted and galaxy.player_by_user(owner) is None:
			return owner
	return None


async def give_pda(
	guild: discord.Guild, galaxy: Galaxy, member: discord.Member
) -> discord.TextChannel:
	"""Gives the member their PDA, creating it if needed. Control sees PDAs only via /watch."""
	existing = guild.get_channel(galaxy.discord.pdas.get(member.id, 0))
	if isinstance(existing, discord.TextChannel):
		await existing.set_permissions(member, overwrite=PDA_ACCESS)
		return existing
	if len(galaxy.discord.pdas) >= ch.MAX_PDAS:
		stale = stale_pda_owner(galaxy)
		if stale is not None:
			old = guild.get_channel(galaxy.discord.pdas.pop(stale))
			if old is not None:
				await old.delete(reason="ADAMA: making room for a new PDA")
	category = await _pda_category(guild, galaxy)
	pda = await guild.create_text_channel(
		f"pda-{member.name}",
		category=category,
		overwrites={
			guild.default_role: discord.PermissionOverwrite(view_channel=False),
			member: PDA_ACCESS,
		},
	)
	galaxy.discord.pdas[member.id] = pda.id
	await pda.send(
		f"{member.mention}, this is your PDA: your private notebook. Control can message you here."
	)
	return pda


async def admit_to_lobby(
	guild: discord.Guild, galaxy: Galaxy, member: discord.Member
) -> None:
	for key, overwrite in LOBBY_ACCESS.items():
		channel = get_channel(guild, galaxy, key)
		if channel is not None:
			await channel.set_permissions(member, overwrite=overwrite)
	await give_pda(guild, galaxy, member)
	galaxy.discord.admitted.append(member.id)


async def remove_from_lobby(
	guild: discord.Guild, galaxy: Galaxy, member: discord.Member
) -> None:
	"""Strips pregame permissions and PDA access. The PDA is kept in case they come back."""
	for key in LOBBY_ACCESS:
		channel = get_channel(guild, galaxy, key)
		if channel is not None:
			await channel.set_permissions(member, overwrite=None)
	pda = guild.get_channel(galaxy.discord.pdas.get(member.id, 0))
	if pda is not None:
		await pda.set_permissions(member, overwrite=None)
	if member.id in galaxy.discord.admitted:
		galaxy.discord.admitted.remove(member.id)


def lobby_players(guild: discord.Guild, galaxy: Galaxy, roles: RolesConfig) -> int:
	"""Admitted users who count towards the player limit (GMs don't)."""
	count = 0
	for user_id in galaxy.discord.admitted:
		member = guild.get_member(user_id)
		if member is None or not is_gm(member, roles):
			count += 1
	return count


async def fill_lobby(guild: discord.Guild, galaxy: Galaxy, roles: RolesConfig) -> None:
	"""Admits users from the front of the queue while there is room under the limit."""
	while (
		galaxy.discord.queue
		and lobby_players(guild, galaxy, roles) < galaxy.player_limit
	):
		user_id = galaxy.discord.queue.pop(0)
		member = guild.get_member(user_id)
		if member is not None:
			await admit_to_lobby(guild, galaxy, member)


# Players in game: permissions, roles, voice and notices.


def _to_overwrite(
	channel: GuildChannel, access: Access | None
) -> discord.PermissionOverwrite:
	if access is None:
		return discord.PermissionOverwrite()
	if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
		return discord.PermissionOverwrite(
			view_channel=True, connect=access.connect, speak=access.speak
		)
	return discord.PermissionOverwrite(
		view_channel=True,
		send_messages=access.send,
		read_message_history=access.history,
	)


def ship_channel(
	guild: discord.Guild, galaxy: Galaxy, ship_id: str, kind: str
) -> GuildChannel | None:
	channel_id = galaxy.discord.ship_channels.get(ship_id, {}).get(kind)
	return guild.get_channel(channel_id) if channel_id is not None else None


async def sync_player(
	guild: discord.Guild, galaxy: Galaxy, player: Player, *, travelled: bool = False
) -> None:
	"""Makes the player's Discord access match the game state.

	If they are in a voice channel they may no longer use, a /travel moves them into
	their new ship's Hangar VC; any other move drops them and tells their PDA.

	Only overwrites that differ are sent, all at once (each channel has its own rate
	limit). New access is granted before old access is taken, so the player is never
	left with nothing and can be moved straight into their new voice channel.
	"""
	member = guild.get_member(player.discord_user_id)
	if member is None:
		return
	desired = player_access(galaxy, player)
	managed = managed_channels(galaxy)
	grants: list[Coroutine[Any, Any, None]] = []
	revokes: list[Coroutine[Any, Any, None]] = []
	for channel_id in managed:
		channel = guild.get_channel(channel_id)
		if channel is None:
			continue
		want = _to_overwrite(channel, desired.get(channel_id))
		if channel.overwrites_for(member) == want:
			continue
		change = channel.set_permissions(
			member, overwrite=None if want.is_empty() else want
		)
		(revokes if want.is_empty() else grants).append(change)
	pda = guild.get_channel(player.pda_channel_id or 0)
	if pda is not None and pda.overwrites_for(member) != PDA_ACCESS:
		grants.append(pda.set_permissions(member, overwrite=PDA_ACCESS))
	await asyncio.gather(*grants)
	await _leave_lost_voice(guild, galaxy, player, member, desired, travelled, pda)
	await asyncio.gather(*revokes)


async def _leave_lost_voice(
	guild: discord.Guild,
	galaxy: Galaxy,
	player: Player,
	member: discord.Member,
	desired: dict[int, Access],
	travelled: bool,
	pda: GuildChannel | None,
) -> None:
	voice = member.voice.channel if member.voice else None
	if voice is None or voice.id not in managed_channels(galaxy):
		return
	allowed = desired.get(voice.id)
	if allowed is not None and allowed.connect:
		return
	# Travelling moves you into your new hangar; being brigged, into the brig.
	if player.brigged:
		target = get_channel(guild, galaxy, ch.VC_BRIG)
	elif travelled:
		target = ship_channel(guild, galaxy, player.location, ch.SHIP_VC_HANGAR)
	else:
		target = None
	if isinstance(target, discord.VoiceChannel):
		await member.move_to(target)
		return
	await member.move_to(None)
	if isinstance(pda, discord.TextChannel):
		await pda.send(
			f"You were disconnected from {voice.name} because you are no longer there."
		)


async def sync_crew_roles(guild: discord.Guild, galaxy: Galaxy, player: Player) -> None:
	"""Gives the player exactly the crew roles of their crew affiliations."""
	member = guild.get_member(player.discord_user_id)
	if member is None:
		return
	for ship_id, created in galaxy.discord.ship_channels.items():
		role = guild.get_role(created.get(ch.SHIP_ROLE, 0))
		if role is None:
			continue
		crew = ship_id in player.crew_affiliations and not player.dead
		if crew and role not in member.roles:
			await member.add_roles(role, reason="ADAMA: crew")
		elif not crew and role in member.roles:
			await member.remove_roles(role, reason="ADAMA: crew")


async def post_notices(
	guild: discord.Guild,
	galaxy: Galaxy,
	notices: list[Notice],
	roles: RolesConfig | None = None,
) -> None:
	"""Posts every notice. Different channels are posted to at the same time (each has
	its own rate limit); notices for the same channel keep their order. A notice with
	`ping` mentions the control role (found through `roles`); one with `claim` is
	offered to control to take (see `take_call`)."""
	control = get_control_role(guild, roles) if roles else None
	queues: dict[int, tuple[discord.TextChannel, list[tuple[str, bool, bool]]]] = {}
	for notice in notices:
		channel = _notice_channel(guild, galaxy, notice)
		if isinstance(channel, discord.TextChannel):
			ping = notice.ping and control is not None
			text = (
				f"{control.mention} {notice.text}" if ping and control else notice.text
			)
			queues.setdefault(channel.id, (channel, []))[1].append(
				(text, ping, notice.claim)
			)
		else:
			logger.warning(
				"Could not post notice to %s %s: %s",
				notice.target,
				notice.id,
				notice.text,
			)

	async def post_in_order(
		channel: discord.TextChannel, texts: list[tuple[str, bool, bool]]
	) -> None:
		for text, ping, claim in texts:
			mentions = (
				discord.AllowedMentions(roles=[control], users=False, everyone=False)
				if ping and control
				else NO_MENTIONS
			)
			message = await send_long(channel, text, allowed_mentions=mentions)
			if claim:
				await offer_call(message)

	await asyncio.gather(*(post_in_order(c, texts) for c, texts in queues.values()))


# Calls for control: the bot salutes them, and the first controller to salute takes it.
CALL_EMOJI = "🫡"
TAKEN_BY = "🫡 **Taken by** "
_call_locks: dict[int, asyncio.Lock] = {}


async def offer_call(message: discord.Message) -> None:
	try:
		await message.add_reaction(CALL_EMOJI)
	except discord.HTTPException:
		logger.warning("Could not salute call %s", message.id)


def is_open_call(message: discord.Message, bot_id: int) -> bool:
	"""The bot's own message, saluted by the bot, and not yet taken."""
	saluted = any(r.me and str(r.emoji) == CALL_EMOJI for r in message.reactions)
	taken = TAKEN_BY in message.content or bool(message.embeds)
	return message.author.id == bot_id and saluted and not taken


async def take_call(
	channel: discord.TextChannel, message_id: int, member: discord.Member
) -> None:
	"""Marks the call as taken by `member`, unless someone already took it."""
	async with _call_locks.setdefault(channel.id, asyncio.Lock()):
		try:
			message = await channel.fetch_message(message_id)
		except discord.HTTPException:
			return
		if not is_open_call(message, channel.guild.me.id):
			return
		taken = TAKEN_BY + member.mention
		content = f"{message.content}\n{taken}"
		if len(content) <= 2000:
			await message.edit(content=content, allowed_mentions=NO_MENTIONS)
		else:  # no room in the text: an embed has its own limit
			await message.edit(embed=discord.Embed(description=taken))


def _notice_channel(
	guild: discord.Guild, galaxy: Galaxy, notice: Notice
) -> GuildChannel | None:
	if notice.target == HANGAR:
		return ship_channel(guild, galaxy, notice.id, ch.SHIP_HANGAR)
	if notice.target == BRIDGE:
		return ship_channel(guild, galaxy, notice.id, ch.SHIP_BRIDGE)
	if notice.target == PDA:
		player = galaxy.players.get(notice.id)
		return guild.get_channel(player.pda_channel_id or 0) if player else None
	if notice.target == CONTROL:
		return get_channel(guild, galaxy, ch.CONTROL_CHAT)
	if notice.target == BROADCAST:
		return get_channel(guild, galaxy, ch.CONTROL_BROADCASTS)
	if notice.target == BRIG:
		return get_channel(guild, galaxy, ch.BRIG)
	return None


def office_role_names(roles: RolesConfig) -> list[str]:
	"""The roles /startgame reads offices from (the Press included)."""
	return [roles.PRESIDENT, roles.COUNCILLOR, roles.FIRST_RESPONDER, roles.PRESS]


def game_role_names(galaxy: Galaxy, roles: RolesConfig) -> set[str]:
	"""Every role the game gives: crews, offices and the Press."""
	names = {ch.crew_role_name(ship.name) for ship in galaxy.ships.values()}
	return names | set(office_role_names(roles))


async def strip_game_roles(
	guild: discord.Guild, galaxy: Galaxy, player: Player, roles: RolesConfig
) -> list[str]:
	"""Removes the player's game roles in one call; returns the names removed."""
	member = guild.get_member(player.discord_user_id)
	if member is None:
		return []
	names = game_role_names(galaxy, roles)
	removed = [role for role in member.roles if role.name in names]
	if removed:
		await member.remove_roles(*removed, reason="ADAMA: died")
	return [role.name for role in removed]


async def give_roles(guild: discord.Guild, player: Player, names: list[str]) -> None:
	member = guild.get_member(player.discord_user_id)
	if member is None:
		return
	wanted = [r for r in guild.roles if r.name in names and r not in member.roles]
	if wanted:
		await member.add_roles(*wanted, reason="ADAMA")


async def set_role(
	guild: discord.Guild, player: Player, name: str, given: bool
) -> None:
	"""Gives or removes one role by name (e.g. President when elected or deposed)."""
	member = guild.get_member(player.discord_user_id)
	if member is not None:
		await set_member_role(member, name, given)


async def set_member_role(member: discord.Member, name: str, given: bool) -> None:
	role = discord.utils.get(member.guild.roles, name=name)
	if role is None:
		return
	if given and role not in member.roles:
		await member.add_roles(role, reason="ADAMA")
	elif not given and role in member.roles:
		await member.remove_roles(role, reason="ADAMA")


async def sync_players(
	guild: discord.Guild, galaxy: Galaxy, players: list[Player]
) -> None:
	"""sync_player for several players at once."""
	await asyncio.gather(*(sync_player(guild, galaxy, p) for p in players))


async def close_lobby(guild: discord.Guild, galaxy: Galaxy) -> None:
	for key in LOBBY_ACCESS:
		channel = get_channel(guild, galaxy, key)
		if channel is not None:
			await strip_member_overwrites(channel)
	await delete_start_message(guild, galaxy)
	galaxy.discord.queue.clear()


async def set_role_permissions(
	guild: discord.Guild, galaxy: Galaxy, roles: RolesConfig
) -> None:
	"""Role-wide permissions for the game: the press posts news, and control can see,
	post and speak in every fleet channel."""
	control = get_control_role(guild, roles)
	press = discord.utils.get(guild.roles, name=roles.PRESS)
	newscasts = get_channel(guild, galaxy, ch.NEWSCASTS)
	if press is not None and newscasts is not None:
		await newscasts.set_permissions(press, view_channel=True, send_messages=True)
	if control is None:
		return
	for key in (
		ch.LAWS,
		ch.SECRET_LAWS,
		ch.COUNCIL_NOTES,
		ch.NEWSCASTS,
		ch.CONTROL_BROADCASTS,
		ch.COUNCIL_CHAMBERS,
		ch.BRIG,
	):
		channel = get_channel(guild, galaxy, key)
		if channel is not None:
			await channel.set_permissions(
				control,
				view_channel=True,
				send_messages=True,
				read_message_history=True,
			)
	for key in (ch.VC_FLEET_ANNOUNCEMENTS, ch.VC_COUNCIL_CHAMBERS, ch.VC_BRIG):
		channel = get_channel(guild, galaxy, key)
		if channel is not None:
			await channel.set_permissions(
				control, view_channel=True, connect=True, speak=True
			)


async def delete_pda(guild: discord.Guild, galaxy: Galaxy, user_id: int) -> None:
	channel = guild.get_channel(galaxy.discord.pdas.pop(user_id, 0))
	if channel is not None:
		await channel.delete(reason="ADAMA: no game role")

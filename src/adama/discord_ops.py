"""Discord side effects: permanent channels, ship channels, PDAs and the pregame lobby.

Game rules live in the models; this module only mirrors the game state onto a server.
The bot needs the Administrator permission.
"""

import logging

import discord

from adama import channels as ch
from adama.config import RolesConfig
from adama.models.galaxy import Galaxy
from adama.models.ship import Ship
from adama.perms import UserError, is_gm
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


async def ensure_server(
	guild: discord.Guild, galaxy: Galaxy, roles: RolesConfig
) -> discord.Role:
	"""Finds or creates the control role and every permanent channel, storing their IDs."""
	control = get_control_role(guild, roles)
	if control is None:
		control = await guild.create_role(
			name=roles.CONTROL, reason="ADAMA control role"
		)

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
	"""Puts VC Council Chambers and the brig channels in their ships' categories, or back in
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
	"""Sends text split into Discord-sized messages, without pinging anyone. Returns the first."""
	messages = [
		await channel.send(chunk, allowed_mentions=NO_MENTIONS, **kwargs)
		for chunk in split_message(text)
	]
	return messages[0]


async def update_fleet_status(guild: discord.Guild, galaxy: Galaxy) -> None:
	channel = get_channel(guild, galaxy, ch.FLEET_STATUS)
	if not isinstance(channel, discord.TextChannel):
		return
	chunks = split_message(fleet_status(galaxy))
	old = galaxy.discord.fleet_status_message_ids
	if len(old) == len(chunks):
		try:
			for message_id, chunk in zip(old, chunks):
				await channel.get_partial_message(message_id).edit(content=chunk)
			return
		except discord.NotFound:
			pass
	for message_id in old:
		try:
			await channel.get_partial_message(message_id).delete()
		except discord.NotFound:
			pass
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

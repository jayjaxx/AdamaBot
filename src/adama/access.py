"""Which channels a player can use, worked out from the game state alone.

Every command that moves a player or changes their crews, offices or health calls
`player_access` and applies the result, rather than switching permissions by hand.
"""

from dataclasses import dataclass

from adama import channels as ch
from adama.models.galaxy import Galaxy
from adama.models.player import LOCATION_BRIG, LOCATION_COMBAT, Player


@dataclass(frozen=True)
class Access:
	"""One player's permissions on one channel. Text channels use send and history,
	voice channels use connect and speak."""

	send: bool = False
	history: bool = True
	connect: bool = False
	speak: bool = False


READ_ONLY = Access()
# Impermanence: ship channels show only messages posted while you're there.
IMPERMANENT = Access(send=True, history=False)
POST = Access(send=True)
LISTEN = Access(connect=True)
VOICE = Access(connect=True, speak=True)

SHIP_VOICE = (
	ch.SHIP_VC_HANGAR,
	"vc_ready_room",
	"vc_shadowy_corner",
	"vc_disused_compartment",
)


def managed_channels(galaxy: Galaxy) -> set[int]:
	"""Every channel whose per-player permissions come from `player_access`."""
	managed: set[int] = set()
	for created in galaxy.discord.ship_channels.values():
		managed.update(
			id
			for kind, id in created.items()
			if kind not in (ch.SHIP_CATEGORY, ch.SHIP_ROLE)
		)
	for key in (
		ch.LAWS,
		ch.SECRET_LAWS,
		ch.COUNCIL_NOTES,
		ch.NEWSCASTS,
		ch.CONTROL_BROADCASTS,
		ch.VC_FLEET_ANNOUNCEMENTS,
		ch.VC_COUNCIL_CHAMBERS,
		ch.BRIG,
		ch.VC_BRIG,
	):
		if key in galaxy.discord.channels:
			managed.add(galaxy.discord.channels[key])
	for coords in galaxy.coordinates.values():
		for id in (coords.combat_text_channel_id, coords.combat_voice_channel_id):
			if id is not None:
				managed.add(id)
	return managed


def player_access(galaxy: Galaxy, player: Player) -> dict[int, Access]:
	"""Channel ID -> access for every managed channel the player may use. Channels left
	out are hidden from them. PDAs are handled separately."""
	access: dict[int, Access] = {}
	if player.dead:
		return access
	static = galaxy.discord.channels

	def give(key: str, level: Access) -> None:
		if key in static:
			access[static[key]] = level

	# Fleet-wide channels.
	give(ch.LAWS, POST if player.president else READ_ONLY)
	give(ch.NEWSCASTS, READ_ONLY)  # the Press role posts via a role permission
	give(ch.CONTROL_BROADCASTS, READ_ONLY)
	give(ch.VC_FLEET_ANNOUNCEMENTS, LISTEN)
	if player.president or player.councillor:
		give(ch.SECRET_LAWS, POST if player.president else READ_ONLY)

	if player.brigged or player.location == LOCATION_BRIG:
		# The brig channels are the only way brigged players can communicate.
		give(ch.BRIG, POST)
		give(ch.VC_BRIG, VOICE)
		return access

	ship = galaxy.ships.get(player.location)
	if ship is not None:
		created = galaxy.discord.ship_channels.get(ship.id, {})

		def give_ship(kind: str, level: Access) -> None:
			if kind in created:
				access[created[kind]] = level

		give_ship(ch.SHIP_HANGAR, IMPERMANENT)
		for kind in SHIP_VOICE:
			give_ship(kind, VOICE)
		if ship.id in player.crew_affiliations:
			give_ship(ch.SHIP_BRIDGE, IMPERMANENT)
			give_ship(ch.SHIP_VC_BRIDGE, VOICE)

		if galaxy.council_chambers_ship == ship.id:
			council = player.president or player.councillor
			may_speak = council or player.permitted
			if may_speak or not galaxy.council_sealed:
				give(ch.VC_COUNCIL_CHAMBERS, VOICE if may_speak else LISTEN)
			if player.councillor:
				give(ch.COUNCIL_NOTES, POST)

	# Combat channels, one pair per locked-down set of coordinates.
	for coords in galaxy.coordinates.values():
		if not coords.locked_down:
			continue
		in_this_combat = (
			player.location == LOCATION_COMBAT
			and player.combat_origin_ship in galaxy.ships
			and galaxy.ships[player.combat_origin_ship].coordinates
			== coords.coordinates
		)
		aboard_here = ship is not None and ship.coordinates == coords.coordinates
		if in_this_combat or (player.first_responder and aboard_here):
			text, voice = POST, VOICE
		elif player.battlemap_observer:
			text, voice = READ_ONLY, LISTEN
		else:
			continue
		if coords.combat_text_channel_id is not None:
			access[coords.combat_text_channel_id] = text
		if coords.combat_voice_channel_id is not None:
			access[coords.combat_voice_channel_id] = voice
	return access

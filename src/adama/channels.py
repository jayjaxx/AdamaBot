"""Names of the permanent Discord channels the bot uses, and which it creates if missing.

Ship channels are made per ship (see `ship_channel_names`), PDAs per player, and combat
channels per locked-down coordinates.
"""

from dataclasses import dataclass

TEXT = "text"
VOICE = "voice"
STAGE = "stage"

# Who can see a permanent channel before players are given individual permissions.
CONTROL_ONLY = "control"  # control role only
PUBLIC_READ = "public read"  # everyone can read and react, control can post
HIDDEN = "hidden"  # control role only, until players are given access individually
# The server's own permissions: the bot never sets any (the Out of Game category, which
# can hold the server's ordinary channels next to #starting-games).
SERVER_DEFAULT = "server default"

CAT_OUT_OF_GAME = "cat_out_of_game"
CAT_PREGAME = "cat_pregame"
CAT_CONTROL = "cat_control"
CAT_FLEET = "cat_fleet"

STARTING_GAMES = "starting_games"
PREGAME_CHAT = "pregame_chat"
ROLE_SELECT = "role_select"
CONTROL_SETUP = "control_setup"
VC_PREGAME = "vc_pregame"
FLEET_STATUS = "fleet_status"
CONTROL_COMMANDS = "control_commands"
CONTROL_CHAT = "control_chat"
CONTROL_NOTES = "control_notes"
VC_CONTROL = "vc_control"
LAWS = "laws"
SECRET_LAWS = "secret_laws"
COUNCIL_NOTES = "council_notes"
NEWSCASTS = "newscasts"
CONTROL_BROADCASTS = "control_broadcasts"
VC_FLEET_ANNOUNCEMENTS = "vc_fleet_announcements"
COUNCIL_CHAMBERS = "council_chambers"
VC_COUNCIL_CHAMBERS = "vc_council_chambers"
BRIG = "brig"
VC_BRIG = "vc_brig"


@dataclass(frozen=True)
class ChannelSpec:
	key: str
	name: str
	kind: str
	# Key of the category it lives in (None for a category itself).
	category: str | None
	visibility: str = HIDDEN
	# Emptied by /startinitialization.
	cleared_on_init: bool = False


CATEGORIES = [
	ChannelSpec(CAT_OUT_OF_GAME, "Out of Game", "category", None, SERVER_DEFAULT),
	ChannelSpec(CAT_PREGAME, "Pregame Lobby", "category", None),
	ChannelSpec(CAT_CONTROL, "Control", "category", None, CONTROL_ONLY),
	ChannelSpec(CAT_FLEET, "Fleet", "category", None),
]

CHANNELS = [
	ChannelSpec(STARTING_GAMES, "starting-games", TEXT, CAT_OUT_OF_GAME, PUBLIC_READ),
	ChannelSpec(PREGAME_CHAT, "pregame-chat", TEXT, CAT_PREGAME),
	ChannelSpec(ROLE_SELECT, "role-select", TEXT, CAT_PREGAME),
	ChannelSpec(CONTROL_SETUP, "control-setup", TEXT, CAT_PREGAME, CONTROL_ONLY),
	ChannelSpec(VC_PREGAME, "Pregame", STAGE, CAT_PREGAME),
	ChannelSpec(FLEET_STATUS, "fleet-status", TEXT, CAT_CONTROL, CONTROL_ONLY, True),
	ChannelSpec(
		CONTROL_COMMANDS, "control-commands", TEXT, CAT_CONTROL, CONTROL_ONLY, True
	),
	ChannelSpec(CONTROL_CHAT, "control-chat", TEXT, CAT_CONTROL, CONTROL_ONLY, True),
	ChannelSpec(CONTROL_NOTES, "control-notes", TEXT, CAT_CONTROL, CONTROL_ONLY, True),
	ChannelSpec(VC_CONTROL, "Control", VOICE, CAT_CONTROL, CONTROL_ONLY, True),
	*[
		ChannelSpec(
			f"vc_control_private_{n}",
			f"Control Private {n}",
			VOICE,
			CAT_CONTROL,
			CONTROL_ONLY,
			True,
		)
		for n in range(1, 5)
	],
	ChannelSpec(LAWS, "laws", TEXT, CAT_FLEET, cleared_on_init=True),
	ChannelSpec(SECRET_LAWS, "secret-laws", TEXT, CAT_FLEET, cleared_on_init=True),
	ChannelSpec(COUNCIL_NOTES, "council-notes", TEXT, CAT_FLEET, cleared_on_init=True),
	ChannelSpec(NEWSCASTS, "newscasts", TEXT, CAT_FLEET, cleared_on_init=True),
	ChannelSpec(
		CONTROL_BROADCASTS, "control-broadcasts", TEXT, CAT_FLEET, cleared_on_init=True
	),
	ChannelSpec(
		VC_FLEET_ANNOUNCEMENTS,
		"Fleet Announcements",
		VOICE,
		CAT_FLEET,
		cleared_on_init=True,
	),
	# These move into the council chambers ship's and brig ship's categories.
	ChannelSpec(
		COUNCIL_CHAMBERS, "council-chambers", TEXT, CAT_FLEET, cleared_on_init=True
	),
	ChannelSpec(VC_COUNCIL_CHAMBERS, "Council Chambers", VOICE, CAT_FLEET),
	ChannelSpec(BRIG, "brig", TEXT, CAT_FLEET, cleared_on_init=True),
	ChannelSpec(VC_BRIG, "Brig", VOICE, CAT_FLEET, cleared_on_init=True),
]

SPECS = {spec.key: spec for spec in [*CATEGORIES, *CHANNELS]}

# Text channels (by name) where the bot ignores every command. All voice channels too.
BLIND_TEXT_CHANNELS = {"general", "starting-games", "admin-chat", "pregame-chat"}

PDA_CATEGORY = "PDAs"
# PDAs of people who left the lobby are kept until a new one would go past this
# (the 99-player cap).
MAX_PDAS = 99
# Discord's limit on channels per category.
CATEGORY_CHANNEL_LIMIT = 50

# Ship channel kinds, as made by /conjureship.
SHIP_CATEGORY = "category"
SHIP_HANGAR = "hangar"
SHIP_BRIDGE = "bridge"
SHIP_VC_HANGAR = "vc_hangar"
SHIP_VC_BRIDGE = "vc_bridge"
SHIP_ROLE = "role"


def ship_channel_names(ship_name: str) -> dict[str, tuple[str, str]]:
	"""Kind -> (channel type, name) for one ship's category and channels."""
	return {
		SHIP_CATEGORY: ("category", ship_name),
		SHIP_HANGAR: (TEXT, f"{ship_name}-hangar"),
		SHIP_BRIDGE: (TEXT, f"{ship_name}-bridge"),
		SHIP_VC_HANGAR: (VOICE, f"{ship_name} Hangar"),
		SHIP_VC_BRIDGE: (VOICE, f"{ship_name} Bridge"),
		"vc_ready_room": (VOICE, f"{ship_name} Ready Room"),
		"vc_shadowy_corner": (VOICE, f"{ship_name} Shadowy Corner"),
		"vc_disused_compartment": (VOICE, f"{ship_name} Disused Compartment"),
	}


def crew_role_name(ship_name: str) -> str:
	return f"{ship_name} Crew"

"""Text for /status, /fleet and #fleet-status. Pure functions of the game state."""

from adama.models.action_card import ActionCard
from adama.models.galaxy import Galaxy
from adama.models.player import Player
from adama.models.resources import ResourcesContainer
from adama.models.ship import Ship
from adama.models.shuttle import Shuttle
from adama.models.station import Station


def mention(player: Player) -> str:
	return f"<@{player.discord_user_id}>"


def format_resources(resources: ResourcesContainer) -> str:
	nonzero = resources.nonzero()
	if not nonzero:
		return "nothing"
	return ", ".join(f"{amount} {name}" for name, amount in nonzero.items())


def _crew(keyword: str, amount: int) -> str:
	return f"{amount}× {keyword}" if amount else ""


def format_station(station: Station) -> str:
	flags = [
		flag
		for flag, on in (
			("DAMAGED", station.damaged),
			("RIOT", station.riot),
			("exhausted", station.exhausted),
			("unusable", not station.usable),
			("reusable", station.reusable),
			("start of turn", station.startofturn),
		)
		if on
	]
	line = f"`{station.id}` **{station.name}**"
	if flags:
		line += f" [{', '.join(flags)}]"
	if station.usable:
		cost = ", ".join(
			part
			for part in (
				_crew(station.input.crew_keyword, station.input.crew_amount),
				", ".join(f"{a} {r}" for r, a in station.input.nonzero().items()),
			)
			if part
		)
		if station.modes:
			modes = "; ".join(
				f"{n}. {mode.name}: {format_resources(mode.input)} → {format_resources(mode.output)}"
				+ (
					f" ({mode.morale_modifier:+d} morale)"
					if mode.morale_modifier
					else ""
				)
				for n, mode in enumerate(station.modes, start=1)
			)
			line += f"\n  crew {cost or 'none'} | modes: {modes}"
		else:
			line += (
				f"\n  in: {cost or 'nothing'} | out: {format_resources(station.output)}"
			)
	return line


def players_aboard(galaxy: Galaxy, ship: Ship) -> tuple[list[Player], list[Player]]:
	"""(crew, visitors) currently aboard the ship."""
	aboard = [
		p for p in galaxy.players.values() if p.location == ship.id and not p.dead
	]
	crew = [p for p in aboard if ship.id in p.crew_affiliations]
	visitors = [p for p in aboard if ship.id not in p.crew_affiliations]
	return crew, visitors


def shuttle_label(shuttle: Shuttle) -> str:
	flags = [
		name
		for name, on in (
			("launching", shuttle.launching),
			("intercepted", shuttle.intercepted),
			("cleared", shuttle.clearance),
		)
		if on
	]
	return shuttle.name + (f" ({', '.join(flags)})" if flags else "")


def hangar_status(galaxy: Galaxy, ship: Ship) -> str:
	crew, visitors = players_aboard(galaxy, ship)
	lines = [f"## {ship.name} — Hangar"]
	lines.append(f"**Crew aboard:** {', '.join(map(mention, crew)) or 'none'}")
	lines.append(f"**Visitors aboard:** {', '.join(map(mention, visitors)) or 'none'}")
	if not ship.shuttles:
		lines.append("**Docked shuttles:** none")
		return "\n".join(lines)
	lines.append("**Docked shuttles:**")
	for shuttle in ship.shuttles.values():
		units_on = ", ".join(f"`{u.id}` {u.name}" for u in shuttle.units.values())
		lines.append(
			f"- {shuttle_label(shuttle)}: {format_resources(shuttle.resources)}"
			+ (f"; units {units_on}" if units_on else "")
		)
	return "\n".join(lines)


def _ration(ship: Ship, resource: str) -> str:
	level = getattr(ship, f"{resource}_ration_level")
	amount = getattr(ship.ration_levels[level], resource)
	return f"{level} ({amount} {resource})"


def card_line(galaxy: Galaxy, card: ActionCard) -> str:
	"""A card with its use, restriction and ability, for the bridge and PDA."""
	if not card.retained:
		use = "single use"
	elif card.once_per_turn:
		use = "once per turn" + (", used this turn" if card.exhausted else "")
	else:
		use = "reusable"
	tags = [use]
	if card.affiliation_restricted:
		tags.append(f"restricted to {_ship_name(galaxy, card.affiliation)} crew")
	line = f"`{card.id}` **{card.name}** ({'; '.join(tags)})"
	return line + (f": {card.description}" if card.description else "")


def bridge_status(galaxy: Galaxy, ship: Ship) -> str:
	lines = [hangar_status(galaxy, ship).replace("— Hangar", "— Bridge", 1), ""]

	mods = ship.morale_roll_modifiers
	lines.append(
		f"**Morale:** {ship.morale_level}/10 | **Roll modifiers:** ship {mods.temp:+d} temp, "
		f"{mods.perm:+d} perm; fleet {galaxy.fleet_temp_morale_roll_modifier:+d} temp, "
		f"{galaxy.fleet_perm_morale_roll_modifier:+d} perm"
	)
	lines.append(f"**Resources:** {format_resources(ship.resources)}")
	lines.append(
		f"**Rations:** food {_ration(ship, 'food')}, water {_ration(ship, 'water')}"
	)
	jumping = " (jump countdown running)" if ship.jumping else ""
	lines.append(f"**Jump coordinates:** {ship.next_coordinates or 'not set'}{jumping}")

	lines.append("\n**Stations**")
	lines.extend(format_station(station) for station in ship.stations.values())

	lines.append("\n**Units aboard**")
	units = [
		f"`{u.id}` {u.name}"
		+ (
			""
			if u.affiliation == ship.id
			else f" ({_ship_name(galaxy, u.affiliation)})"
		)
		+ (" [DAMAGED]" if u.damaged else "")
		+ (" [exhausted]" if u.exhausted else "")
		for u in ship.units.values()
	]
	lines.extend(units or ["none"])

	lines.append("\n**Unclaimed action cards**")
	cards = [card_line(galaxy, c) for c in ship.action_cards.values()]
	lines.extend(cards or ["none"])
	return "\n".join(lines)


def _ship_name(galaxy: Galaxy, ship_id: str | None) -> str:
	if ship_id is None or ship_id not in galaxy.ships:
		return "nowhere"
	return galaxy.ships[ship_id].name


def location_name(galaxy: Galaxy, player: Player) -> str:
	if player.location == "combat":
		return f"combat (from {_ship_name(galaxy, player.combat_origin_ship)})"
	if player.location == "brig":
		return "the brig"
	return _ship_name(galaxy, player.location or None)


def pda_status(galaxy: Galaxy, player: Player) -> str:
	health = [
		state
		for state, on in (
			("dead", player.dead),
			("brigged", player.brigged),
			("wounded", player.wounded),
		)
		if on
	]
	offices = [
		office
		for office, on in (
			("President", player.president),
			("Councillor", player.councillor),
			("Battlemap observer", player.battlemap_observer),
			("First responder", player.first_responder),
		)
		if on
	]
	crews = ", ".join(
		_ship_name(galaxy, ship_id) for ship_id in player.crew_affiliations
	)
	clearances = ", ".join(
		shuttle.name
		for _, shuttle in galaxy.iter_shuttles()
		if shuttle.id in player.clearances
	)
	cards = [card_line(galaxy, c) for c in player.action_cards.values()]
	lines = [
		"## PDA",
		f"**Health:** {', '.join(health) or 'healthy'}",
		f"**Location:** {location_name(galaxy, player)}",
		f"**Crew of:** {crews or 'none'}",
		f"**Offices:** {', '.join(offices) or 'none'}",
		f"**Clearances:** {clearances or 'none'}",
		"**Action cards:**",
		*(cards or ["none"]),
	]
	return "\n".join(lines)


def fleet_list(galaxy: Galaxy, coordinates: str) -> str:
	ships = galaxy.ships_at(coordinates)
	lines = [f"**Ships at {coordinates}:**"]
	lines.extend(f"`{ship.id}` {ship.name}" for ship in ships)
	if not ships:
		lines.append("none")
	return "\n".join(lines)


def activated_this_turn(station: Station) -> bool:
	"""Activating exhausts a station until /endturn; a reusable one (Hangar Bay) isn't
	exhausted, but the crew it used are recorded on it."""
	return station.exhausted or bool(station.units_used_this_turn)


def fleet_status(galaxy: Galaxy) -> str:
	"""The summary kept up to date in #fleet-status."""
	lines = [f"# Fleet status — {galaxy.state}, turn {galaxy.turn_number}"]
	lines.append(
		f"Fleet morale roll modifiers: {galaxy.fleet_temp_morale_roll_modifier:+d} temp, "
		f"{galaxy.fleet_perm_morale_roll_modifier:+d} perm"
	)
	lines.append(
		f"Council chambers: {_ship_name(galaxy, galaxy.council_chambers_ship)}"
		f"{' (sealed)' if galaxy.council_sealed else ''} | Brig: {_ship_name(galaxy, galaxy.brig_ship)}"
	)
	for coordinates in sorted(galaxy.coordinates):
		coords = galaxy.coordinates[coordinates]
		ships = galaxy.ships_at(coordinates)
		if not ships:
			continue
		header = f"\n## {coordinates} ({coords.distance})"
		if coords.locked_down:
			header += " — LOCKDOWN"
		lines.append(header)
		for ship in ships:
			flags = [
				f
				for f, on in (("isolated", ship.isolated), ("jumping", ship.jumping))
				if on
			]
			damaged = sum(1 for s in ship.stations.values() if s.damaged)
			riots = sum(1 for s in ship.stations.values() if s.riot)
			food = ship.food_ration_level
			water = ship.water_ration_level
			lines.append(
				f"**{ship.name}** `{ship.id}` morale {ship.morale_level}/10"
				f"{' (rolled)' if ship.morale_rolled_this_turn else ''}"
				f" | rations {food}/{water} | {damaged} damaged, {riots} riots"
				+ (f" [{', '.join(flags)}]" if flags else "")
			)
			lines.append(f"  resources: {format_resources(ship.resources)}")
			start = [s for s in ship.stations.values() if s.startofturn]
			if start:
				lines.append(
					"  start of turn: "
					+ ", ".join(
						f"`{s.id}` {s.name} {'✅' if activated_this_turn(s) else '❌'}"
						for s in start
					)
				)
			if ship.id in galaxy.morale_results:
				lines.append(f"  last morale roll ({galaxy.morale_results[ship.id]})")
	return "\n".join(lines)


def ship_summary(ship: Ship) -> str:
	return (
		f"**{ship.name}** `{ship.id}` at {ship.coordinates}: {len(ship.stations)} stations, "
		f"{len(ship.units)} units, {len(ship.action_cards)} action cards, {len(ship.shuttles)} shuttles"
	)


def split_message(text: str, limit: int = 2000) -> list[str]:
	"""Splits text into Discord-sized chunks, at line breaks where possible."""
	chunks: list[str] = []
	current = ""
	for line in text.split("\n"):
		while len(line) > limit:
			if current:
				chunks.append(current)
				current = ""
			chunks.append(line[:limit])
			line = line[limit:]
		candidate = f"{current}\n{line}" if current else line
		if len(candidate) > limit:
			chunks.append(current)
			current = line
		else:
			current = candidate
	if current:
		chunks.append(current)
	return chunks

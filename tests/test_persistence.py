from pathlib import Path

from adama.db.database import connect
from adama.models import Galaxy, Player
from adama.models.galaxy import STATE_INITIALIZING
from adama.repositories import GalaxyRepository
from adama.services import GalaxyService
from adama.shipfiles import ShipFile, validate_ship_files

ROOT = Path(__file__).parent.parent


def loaded_galaxy(guild_id: int) -> Galaxy:
	galaxy = Galaxy(guild_id, state=STATE_INITIALIZING, player_limit=40)
	files = [
		ShipFile(p.name, p.read_bytes())
		for p in sorted((ROOT / "shipdefs").glob("*.json"))
	]
	ships, problems = validate_ship_files(files, galaxy)
	assert problems == []
	for ship in ships:
		galaxy.add_ship(ship)
	return galaxy


def test_galaxy_round_trips_through_sqlite():
	repo = GalaxyRepository(connect(":memory:"))
	galaxy = loaded_galaxy(123)
	galaxy.players["0001"] = Player(
		id="0001", discord_user_id=99, location="0100", wolf=True
	)
	galaxy.discord.pdas[99] = 555
	galaxy.discord.channels["laws"] = 777
	galaxy.discord.ship_channels["0100"] = {"bridge": 1}
	repo.save(galaxy)

	loaded = repo.load("123")
	assert loaded is not None
	assert loaded.to_dict() == galaxy.to_dict()
	assert {id: s.to_dict() for id, s in loaded.ships.items()} == {
		id: s.to_dict() for id, s in galaxy.ships.items()
	}
	assert loaded.players["0001"].to_dict() == galaxy.players["0001"].to_dict()
	assert loaded.discord.pdas == {99: 555}


def test_guilds_are_independent():
	repo = GalaxyRepository(connect(":memory:"))
	repo.save(loaded_galaxy(1))
	repo.save(Galaxy(2))
	one, two = repo.load("1"), repo.load("2")
	assert one is not None and two is not None
	assert len(one.ships) == 11
	assert two.ships == {}
	assert repo.load("3") is None


def test_saving_again_replaces_removed_ships():
	repo = GalaxyRepository(connect(":memory:"))
	galaxy = loaded_galaxy(1)
	repo.save(galaxy)
	galaxy.remove_ship("0100")
	repo.save(galaxy)
	loaded = repo.load("1")
	assert loaded is not None
	assert "0100" not in loaded.ships


def test_service_creates_idle_galaxies_and_caches_them():
	service = GalaxyService(GalaxyRepository(connect(":memory:")))
	galaxy = service.get(7)
	assert galaxy.state == "idle"
	assert service.get(7) is galaxy
	assert service.lock(7) is service.lock(7)

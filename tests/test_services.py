from pathlib import Path

from aws_reverse_tunnel.services import load_services, save_services


def test_load_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert load_services(tmp_path / "services.json") == {}


def test_round_trips_through_json(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "services.json"
    services = {
        "openwebui": "192.168.50.10:8080",
        "homeassistant": "192.168.50.198:8123",
    }

    save_services(services, path)
    loaded = load_services(path)

    assert loaded == services


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "services.json"

    save_services({}, path)

    assert path.exists()

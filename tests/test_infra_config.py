from pathlib import Path

from aws_reverse_tunnel.infra_config import InfraConfig


def test_round_trips_through_json(tmp_path: Path) -> None:
    config = InfraConfig(
        domain="dasbd72.com", hosted_zone_id="Z123", region="ap-northeast-1"
    )
    path = tmp_path / "nested" / "infra-config.json"

    config.save(path)
    loaded = InfraConfig.load(path)

    assert loaded == config


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    config = InfraConfig(
        domain="dasbd72.com", hosted_zone_id="Z123", region="ap-northeast-1"
    )
    path = tmp_path / "a" / "b" / "infra-config.json"

    config.save(path)

    assert path.exists()

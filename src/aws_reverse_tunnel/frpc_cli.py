from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from typing import Any

import click
from botocore.exceptions import ClientError

from . import token as token_module
from .frpc_config import DEFAULT_SERVER_PORT, DEFAULT_TOKEN_PARAM, FrpcConfig
from .frpc_toml import parse_target
from .frpc_toml import render as render_frpc_toml
from .infra_config import InfraConfig, describe_stack
from .paths import (
    FRPC_CONFIG_FILE,
    FRPC_TOML_FILE,
    INFRA_CONFIG_FILE,
    SERVICES_FILE,
    SYSTEMD_UNIT_FILE,
)
from .prompts import confirm_overwrite
from .services import load_services, save_services
from .systemd_unit import render as render_systemd_unit


def _require_frpc() -> str:
    frpc_path = shutil.which("frpc")
    if frpc_path is None:
        raise click.ClickException(
            "frpc not found on PATH. Install it yourself from "
            "https://github.com/fatedier/frp/releases."
        )
    return frpc_path


def _load_infra_config() -> InfraConfig | None:
    if not INFRA_CONFIG_FILE.exists():
        return None
    return InfraConfig.load(INFRA_CONFIG_FILE)


def _discover_server_addr(region: str) -> str | None:
    try:
        stack = describe_stack(region)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ValidationError":
            return None
        raise click.ClickException(
            f"could not look up the deployed stack: {exc}"
        ) from exc
    for output in stack.get("Outputs", []):
        if output["OutputKey"] == "ElasticIp":
            value: str = output["OutputValue"]
            return value
    return None


def _fetch_token(token_param: str, region: str) -> str | None:
    try:
        return token_module.fetch_token(token_param, region)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise click.ClickException(f"could not fetch the frp token: {exc}") from exc


def _resolve(value: str | None, fallback: Callable[[], str | None], error: str) -> str:
    resolved = value or fallback()
    if not resolved:
        raise click.ClickException(error)
    return resolved


def _parse_services(pairs: tuple[str, ...]) -> dict[str, str]:
    services: dict[str, str] = {}
    for pair in pairs:
        name, sep, target = pair.partition("=")
        if not sep or not name:
            raise click.BadParameter(
                f"invalid --service {pair!r}; expected NAME=HOST:PORT",
                param_hint="--service",
            )
        try:
            parse_target(target)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--service") from exc
        services[name] = target
    return services


def _resolve_config(
    server_addr: str | None,
    base_domain: str | None,
    server_port: int,
    token: str | None,
    token_param: str,
    region: str | None,
    services: tuple[str, ...],
) -> tuple[FrpcConfig, dict[str, str]]:
    infra_config = _load_infra_config()

    resolved_region = _resolve(
        region,
        lambda: infra_config.region if infra_config else None,
        "no --region given and no infra config found; pass --region or run "
        "`aws-reverse-tunnel infra config` first",
    )
    resolved_base_domain = _resolve(
        base_domain,
        lambda: infra_config.domain if infra_config else None,
        "no --base-domain given and no infra config found; pass --base-domain "
        "or run `aws-reverse-tunnel infra config` first",
    )
    resolved_server_addr = _resolve(
        server_addr,
        lambda: _discover_server_addr(resolved_region),
        "no --server-addr given and the deployed stack's Elastic IP could not "
        "be found; pass --server-addr or run `aws-reverse-tunnel infra deploy` first",
    )
    resolved_token = _resolve(
        token,
        lambda: _fetch_token(token_param, resolved_region),
        f"no --token given and it could not be fetched from SSM parameter "
        f"{token_param!r} in region {resolved_region!r}; pass --token explicitly "
        "or run `aws-reverse-tunnel infra deploy` first",
    )

    config = FrpcConfig(
        server_addr=resolved_server_addr,
        base_domain=resolved_base_domain,
        token=resolved_token,
        server_port=server_port,
        region=resolved_region,
    )
    return config, _parse_services(services)


def _write_frpc_toml(config: FrpcConfig, services: dict[str, str]) -> None:
    FRPC_TOML_FILE.parent.mkdir(parents=True, exist_ok=True)
    FRPC_TOML_FILE.write_text(render_frpc_toml(config, services))
    FRPC_TOML_FILE.chmod(0o600)


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=True)


def _load_config() -> FrpcConfig:
    if not FRPC_CONFIG_FILE.exists():
        raise click.ClickException(
            "not configured; run `aws-reverse-tunnel frpc config` first"
        )
    return FrpcConfig.load(FRPC_CONFIG_FILE)


def _apply_services(cfg: FrpcConfig, services: dict[str, str]) -> None:
    save_services(services, SERVICES_FILE)
    _write_frpc_toml(cfg, services)
    _systemctl("restart", "frpc.service")


def _common_options(f: Callable[..., Any]) -> Callable[..., Any]:
    f = click.option(
        "--server-addr",
        default=None,
        help="Public address of the frps server (EC2 Elastic IP or hostname). "
        "Auto-detected from the deployed AWS stack if not given.",
    )(f)
    f = click.option(
        "--base-domain",
        default=None,
        help="Base domain the wildcard DNS record covers, e.g. dasbd72.com. "
        "Defaults to the domain set via `aws-reverse-tunnel infra config` if not given.",
    )(f)
    f = click.option(
        "--server-port", default=DEFAULT_SERVER_PORT, show_default=True, type=int
    )(f)
    f = click.option(
        "--token",
        default=None,
        help="The frp auth token. Auto-fetched from SSM (--token-param) if not given.",
    )(f)
    f = click.option("--token-param", default=DEFAULT_TOKEN_PARAM, show_default=True)(f)
    f = click.option(
        "--region",
        default=None,
        help="AWS region used to fetch the frp token and look up the deployed stack. "
        "Defaults to the region set via `aws-reverse-tunnel infra config` if not given.",
    )(f)
    f = click.option(
        "--service",
        "services",
        multiple=True,
        metavar="NAME=HOST:PORT",
        help="Expose a local HOST:PORT at NAME.<base-domain>. Repeatable.",
    )(f)
    return f


@click.group()
def frpc() -> None:
    """Manage the frpc side of your aws-reverse-tunnel."""


@frpc.command()
@_common_options
def render(
    server_addr: str | None,
    base_domain: str | None,
    server_port: int,
    token: str | None,
    token_param: str,
    region: str | None,
    services: tuple[str, ...],
) -> None:
    """Render an frpc.toml to stdout from flags only (no persistence)."""
    config, services_dict = _resolve_config(
        server_addr, base_domain, server_port, token, token_param, region, services
    )
    click.echo(render_frpc_toml(config, services_dict), nl=False)


@frpc.command()
@_common_options
@click.option(
    "--yes",
    is_flag=True,
    help="Overwrite an existing configuration without confirming.",
)
def config(
    server_addr: str | None,
    base_domain: str | None,
    server_port: int,
    token: str | None,
    token_param: str,
    region: str | None,
    services: tuple[str, ...],
    yes: bool,
) -> None:
    """Configure, persist, and start the local frpc tunnel client.

    An extension of `render`: resolves the same settings, then saves them
    and (re)starts the systemd service. If --service is not given, the
    previously registered services (managed via `add`/`del`) are kept as-is.
    """
    frpc_path = _require_frpc()

    new_config, service_overrides = _resolve_config(
        server_addr, base_domain, server_port, token, token_param, region, services
    )

    if FRPC_CONFIG_FILE.exists():
        confirm_overwrite(FrpcConfig.load(FRPC_CONFIG_FILE), new_config, yes=yes)

    resolved_services = service_overrides if services else load_services(SERVICES_FILE)
    if services:
        save_services(resolved_services, SERVICES_FILE)

    new_config.save(FRPC_CONFIG_FILE)
    _write_frpc_toml(new_config, resolved_services)

    SYSTEMD_UNIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SYSTEMD_UNIT_FILE.write_text(render_systemd_unit(frpc_path, str(FRPC_TOML_FILE)))
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", "frpc.service")
    _systemctl("restart", "frpc.service")
    click.echo(f"frpc configured and started ({SYSTEMD_UNIT_FILE})")


@frpc.command()
@click.argument("name")
@click.argument("target")
def add(name: str, target: str) -> None:
    """Expose a local TARGET (host:port) at NAME.<base-domain>."""
    try:
        parse_target(target)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="TARGET") from exc
    cfg = _load_config()
    services = load_services(SERVICES_FILE)
    services[name] = target
    _apply_services(cfg, services)
    click.echo(f"{name}.{cfg.base_domain} -> {target}")


@frpc.command(name="del")
@click.argument("name")
def del_(name: str) -> None:
    """Stop exposing NAME."""
    cfg = _load_config()
    services = load_services(SERVICES_FILE)
    if name not in services:
        raise click.ClickException(f"no such service: {name}")
    del services[name]
    _apply_services(cfg, services)
    click.echo(f"removed {name}")


@frpc.command(name="list")
def list_services() -> None:
    """List configured services."""
    cfg = _load_config()
    services = load_services(SERVICES_FILE)
    if not services:
        click.echo("no services configured")
        return
    for name, target in sorted(services.items()):
        click.echo(f"{name}.{cfg.base_domain} -> {target}")


@frpc.command()
def status() -> None:
    """Show frpc service status."""
    _systemctl("status", "frpc.service")

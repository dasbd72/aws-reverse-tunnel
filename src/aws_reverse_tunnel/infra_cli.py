from __future__ import annotations

import importlib
import shutil
import subprocess
import sys

import boto3
import click
from botocore.exceptions import ClientError

from .infra_config import STACK_NAME, InfraConfig, describe_stack
from .paths import INFRA_CACHE_DIR, INFRA_CONFIG_FILE
from .prompts import confirm_overwrite


def _require_cdk() -> str:
    cdk = shutil.which("cdk")
    if cdk is None:
        raise click.ClickException(
            "cdk not found on PATH. Install the infra extra — e.g. "
            "`uv tool install 'aws-reverse-tunnel[infra]'` — then re-run this command."
        )
    return cdk


def _require_aws_cdk_lib() -> None:
    try:
        importlib.import_module("aws_cdk")
    except ImportError as exc:
        raise click.ClickException(
            "aws-cdk-lib is not installed. Install the infra extra — e.g. "
            "`uv tool install 'aws-reverse-tunnel[infra]'` — then re-run this command."
        ) from exc


def _load_config() -> InfraConfig:
    if not INFRA_CONFIG_FILE.exists():
        raise click.ClickException(
            "not configured; run `aws-reverse-tunnel infra config` first"
        )
    return InfraConfig.load(INFRA_CONFIG_FILE)


def _default_region() -> str | None:
    return boto3.Session().region_name


def _discover_hosted_zone_id(domain: str, region: str) -> str:
    client = boto3.client("route53", region_name=region)
    response = client.list_hosted_zones_by_name(DNSName=domain, MaxItems="10")
    normalized = domain.rstrip(".") + "."
    matches = [
        zone
        for zone in response["HostedZones"]
        if zone["Name"] == normalized and not zone["Config"]["PrivateZone"]
    ]
    if not matches:
        raise click.ClickException(
            f"no public Route53 hosted zone found for {domain!r}; pass --hosted-zone-id explicitly"
        )
    if len(matches) > 1:
        ids = ", ".join(zone["Id"] for zone in matches)
        raise click.ClickException(
            f"multiple public hosted zones found for {domain!r} ({ids}); "
            "pass --hosted-zone-id explicitly"
        )
    zone_id: str = matches[0]["Id"]
    return zone_id.removeprefix("/hostedzone/")


def _run_cdk(subcommand: str, infra_config: InfraConfig) -> None:
    cdk = _require_cdk()
    _require_aws_cdk_lib()
    INFRA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    context_args = [
        "-c",
        f"domain={infra_config.domain}",
        "-c",
        f"hostedZoneId={infra_config.hosted_zone_id}",
        "-c",
        f"region={infra_config.region}",
    ]
    if infra_config.extra_port_range_start is not None:
        context_args += [
            "-c",
            f"extraPortRangeStart={infra_config.extra_port_range_start}",
            "-c",
            f"extraPortRangeEnd={infra_config.extra_port_range_end}",
        ]
    try:
        subprocess.run(
            [
                cdk,
                "-a",
                f"{sys.executable} -m aws_reverse_tunnel.infra.app",
                *context_args,
                subcommand,
            ],
            cwd=INFRA_CACHE_DIR,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"cdk {subcommand} failed (exit code {exc.returncode})"
        ) from exc


def _parse_port_range(value: str) -> tuple[int, int]:
    start_str, sep, end_str = value.partition("-")
    if not sep or not start_str.isdigit() or not end_str.isdigit():
        raise click.BadParameter(
            f"invalid --extra-port-range {value!r}; expected START-END",
            param_hint="--extra-port-range",
        )
    start, end = int(start_str), int(end_str)
    if not (1 <= start <= end <= 65535):
        raise click.BadParameter(
            f"invalid --extra-port-range {value!r}; expected 1 <= START <= END <= 65535",
            param_hint="--extra-port-range",
        )
    return start, end


@click.group()
def infra() -> None:
    """Manage the AWS infrastructure (EC2 + frps + Caddy) stack."""


@infra.command()
@click.option(
    "--domain",
    required=True,
    help="Base domain for the wildcard DNS record, e.g. dasbd72.com.",
)
@click.option(
    "--region",
    default=None,
    help="AWS region to deploy into. Defaults to your AWS CLI/config default region.",
)
@click.option(
    "--hosted-zone-id",
    default=None,
    help="Route53 hosted zone ID for --domain. Auto-discovered from --domain if not given.",
)
@click.option(
    "--extra-port-range",
    default=None,
    metavar="START-END",
    help="Open this TCP+UDP port range on the EC2 host and in frps, for "
    "non-HTTP tunnels added via `frpc add --proto tcp|udp --remote-port`. "
    "Not opened at all unless given.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Overwrite an existing configuration without confirming.",
)
def config(
    domain: str,
    region: str | None,
    hosted_zone_id: str | None,
    extra_port_range: str | None,
    yes: bool,
) -> None:
    """Configure the domain/zone/region that deploy/destroy/diff/status target."""
    region = region or _default_region()
    if not region:
        raise click.ClickException(
            "no region given and none configured; pass --region or set a default "
            "with `aws configure set region <region>`"
        )
    hosted_zone_id = hosted_zone_id or _discover_hosted_zone_id(domain, region)
    port_range = _parse_port_range(extra_port_range) if extra_port_range else None
    new_config = InfraConfig(
        domain=domain,
        hosted_zone_id=hosted_zone_id,
        region=region,
        extra_port_range_start=port_range[0] if port_range else None,
        extra_port_range_end=port_range[1] if port_range else None,
    )

    if INFRA_CONFIG_FILE.exists():
        confirm_overwrite(InfraConfig.load(INFRA_CONFIG_FILE), new_config, yes=yes)

    new_config.save(INFRA_CONFIG_FILE)
    click.echo(
        f"Configured: domain={domain} hosted-zone-id={hosted_zone_id} region={region}"
    )


@infra.command()
def deploy() -> None:
    """Deploy (or update) the AWS stack."""
    _run_cdk("deploy", _load_config())


@infra.command()
def destroy() -> None:
    """Destroy the AWS stack."""
    _run_cdk("destroy", _load_config())


@infra.command()
def diff() -> None:
    """Show pending changes to the AWS stack."""
    _run_cdk("diff", _load_config())


@infra.command()
def status() -> None:
    """Show the deployed stack's status and outputs."""
    infra_config = _load_config()
    try:
        stack = describe_stack(infra_config.region)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ValidationError":
            raise click.ClickException(
                f"stack {STACK_NAME!r} not found; run `aws-reverse-tunnel infra deploy` first"
            ) from exc
        raise click.ClickException(
            f"could not look up the deployed stack: {exc}"
        ) from exc
    click.echo(f"Status: {stack['StackStatus']}")
    for output in stack.get("Outputs", []):
        click.echo(f"{output['OutputKey']}: {output['OutputValue']}")

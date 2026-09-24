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
    try:
        subprocess.run(
            [
                cdk,
                "-a",
                f"{sys.executable} -m aws_reverse_tunnel.infra.app",
                "-c",
                f"domain={infra_config.domain}",
                "-c",
                f"hostedZoneId={infra_config.hosted_zone_id}",
                "-c",
                f"region={infra_config.region}",
                subcommand,
            ],
            cwd=INFRA_CACHE_DIR,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"cdk {subcommand} failed (exit code {exc.returncode})"
        ) from exc


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
    "--yes",
    is_flag=True,
    help="Overwrite an existing configuration without confirming.",
)
def config(
    domain: str, region: str | None, hosted_zone_id: str | None, yes: bool
) -> None:
    """Configure the domain/zone/region that deploy/destroy/diff/status target."""
    region = region or _default_region()
    if not region:
        raise click.ClickException(
            "no region given and none configured; pass --region or set a default "
            "with `aws configure set region <region>`"
        )
    hosted_zone_id = hosted_zone_id or _discover_hosted_zone_id(domain, region)
    new_config = InfraConfig(
        domain=domain, hosted_zone_id=hosted_zone_id, region=region
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

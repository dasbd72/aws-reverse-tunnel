from __future__ import annotations

import click

from .frpc_cli import frpc
from .infra_cli import infra


@click.group()
def main() -> None:
    """Manage your aws-reverse-tunnel: AWS infra and the frpc client."""


main.add_command(infra)
main.add_command(frpc)

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import boto3

STACK_NAME = "AwsReverseTunnelStack"


@dataclasses.dataclass
class InfraConfig:
    domain: str
    hosted_zone_id: str
    region: str

    @classmethod
    def load(cls, path: Path) -> InfraConfig:
        return cls(**json.loads(path.read_text()))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dataclasses.asdict(self), indent=2, sort_keys=True) + "\n"
        )


def describe_stack(region: str) -> dict[str, Any]:
    """Fetch the deployed stack's CloudFormation description.

    Raises botocore.exceptions.ClientError (e.g. Code "ValidationError") if
    the stack doesn't exist or the call otherwise fails.
    """
    client = boto3.client("cloudformation", region_name=region)
    stack: dict[str, Any] = client.describe_stacks(StackName=STACK_NAME)["Stacks"][0]
    return stack

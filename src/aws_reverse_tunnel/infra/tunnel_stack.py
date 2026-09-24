from __future__ import annotations

from pathlib import Path
from typing import Any

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_route53 as route53
from aws_cdk import custom_resources as cr
from constructs import Construct

_USER_DATA_TEMPLATE = Path(__file__).parent / "assets" / "user_data.sh"


class TunnelStack(Stack):
    """EC2 host running frps + Caddy, reachable at a wildcard subdomain of domain_name."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        hosted_zone_id: str,
        domain_name: str,
        frp_token: str,
        vpc: ec2.IVpc | None = None,
        hosted_zone: route53.IHostedZone | None = None,
        frp_bind_port: int = 7000,
        frp_vhost_http_port: int = 7080,
        token_param_name: str = "/reverse-tunnel/frp-token",
        frp_version: str = "0.71.0",
        caddy_version: str = "2.9.1",
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # `from_lookup`/`from_hosted_zone_attributes` both require a Stack
        # scope, so they're resolved here (self) rather than in the caller.
        # Tests inject fakes instead of exercising these live lookups.
        vpc = vpc or ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)
        hosted_zone = hosted_zone or route53.HostedZone.from_hosted_zone_attributes(
            self, "HostedZone", hosted_zone_id=hosted_zone_id, zone_name=domain_name
        )

        token_param_arn = self.format_arn(
            service="ssm",
            resource="parameter",
            resource_name=token_param_name.lstrip("/"),
        )
        # AWS::SSM::Parameter does not support Type: SecureString in
        # CloudFormation (String/StringList only), so this has to go
        # through a custom resource calling the SDK directly.
        put_parameter_call = cr.AwsSdkCall(
            service="SSM",
            action="PutParameter",
            parameters={
                "Name": token_param_name,
                "Type": "SecureString",
                "Value": frp_token,
                "Overwrite": True,
            },
            physical_resource_id=cr.PhysicalResourceId.of(token_param_name),
        )
        token_param = cr.AwsCustomResource(
            self,
            "FrpTokenParameter",
            on_create=put_parameter_call,
            on_update=put_parameter_call,
            on_delete=cr.AwsSdkCall(
                service="SSM",
                action="DeleteParameter",
                parameters={"Name": token_param_name},
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=[token_param_arn]
            ),
        )

        security_group = ec2.SecurityGroup(
            self,
            "TunnelSecurityGroup",
            vpc=vpc,
            description="aws-reverse-tunnel EC2 host",
            allow_all_outbound=True,
        )
        security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP (redirects to HTTPS)"
        )
        security_group.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS")
        security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(frp_bind_port), "frp control channel"
        )

        role = iam.Role(
            self,
            "InstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
            ],
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"], resources=[token_param_arn]
            )
        )
        # Permissions required by certbot-dns-route53's DNS-01 challenge:
        # ListHostedZones (to find the zone) and GetChange (to poll change
        # status) can't be scoped to a resource; ChangeResourceRecordSets is
        # scoped to this stack's zone only.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["route53:ChangeResourceRecordSets"],
                resources=[hosted_zone.hosted_zone_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["route53:ListHostedZones", "route53:GetChange"],
                resources=["*"],
            )
        )

        user_data = ec2.UserData.for_linux()
        script = _USER_DATA_TEMPLATE.read_text()
        for placeholder, value in {
            "__DOMAIN__": domain_name,
            "__TOKEN_PARAM__": token_param_name,
            "__BIND_PORT__": str(frp_bind_port),
            "__VHOST_HTTP_PORT__": str(frp_vhost_http_port),
            "__REGION__": self.region,
            "__FRP_VERSION__": frp_version,
            "__CADDY_VERSION__": caddy_version,
        }.items():
            script = script.replace(placeholder, value)
        user_data.add_commands(script)

        instance = ec2.Instance(
            self,
            "TunnelInstance",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T4G, ec2.InstanceSize.NANO
            ),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(
                cpu_type=ec2.AmazonLinuxCpuType.ARM_64
            ),
            security_group=security_group,
            role=role,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        8, volume_type=ec2.EbsDeviceVolumeType.GP3
                    ),
                )
            ],
        )
        # UserData changes don't force CloudFormation to replace an
        # AWS::EC2::Instance (it tries to stop/modify/start in place, which
        # also doesn't re-run cloud-init) — this was manually terminated
        # after an OOM'd boot script, so a fresh logical ID forces a real
        # replacement instead of updating an instance that no longer exists.
        instance.instance.override_logical_id("TunnelInstanceV2")
        # frps reads the token param at boot; make sure it exists first.
        instance.node.add_dependency(token_param)

        eip = ec2.CfnEIP(
            self, "TunnelEip", domain="vpc", instance_id=instance.instance_id
        )

        route53.CfnRecordSet(
            self,
            "WildcardRecord",
            hosted_zone_id=hosted_zone.hosted_zone_id,
            name=f"*.{domain_name}.",
            type="A",
            ttl="300",
            resource_records=[eip.ref],
        )

        CfnOutput(self, "ElasticIp", value=eip.ref)
        CfnOutput(self, "InstanceId", value=instance.instance_id)

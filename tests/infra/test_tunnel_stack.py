from typing import Any

from aws_cdk import App, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_route53 as route53
from aws_cdk.assertions import Match, Template

from aws_reverse_tunnel.infra.tunnel_stack import TunnelStack


def _build_template(**kwargs: Any) -> Template:
    app = App()
    fixtures = Stack(app, "Fixtures")
    vpc = ec2.Vpc.from_vpc_attributes(
        fixtures,
        "Vpc",
        vpc_id="vpc-12345",
        availability_zones=["ap-northeast-1a"],
        public_subnet_ids=["subnet-12345"],
    )
    hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
        fixtures,
        "Zone",
        hosted_zone_id="Z0FAKEZONE",
        zone_name="dasbd72.com",
    )

    stack = TunnelStack(
        app,
        "TunnelStack",
        vpc=vpc,
        hosted_zone=hosted_zone,
        hosted_zone_id="Z0FAKEZONE",
        domain_name="dasbd72.com",
        frp_token="test-token",
        **kwargs,
    )
    return Template.from_stack(stack)


def test_security_group_allows_http_https_and_frp_control_port() -> None:
    template = _build_template()

    for port in (80, 443, 7000):
        template.has_resource_properties(
            "AWS::EC2::SecurityGroup",
            {
                "SecurityGroupIngress": Match.array_with(
                    [
                        Match.object_like(
                            {"CidrIp": "0.0.0.0/0", "FromPort": port, "ToPort": port}
                        )
                    ]
                )
            },
        )


def test_instance_is_t4g_nano_arm() -> None:
    template = _build_template()

    template.has_resource_properties("AWS::EC2::Instance", {"InstanceType": "t4g.nano"})


def test_instance_role_grants_ssm_managed_instance_core() -> None:
    template = _build_template()

    template_json = template.to_json()
    role_resources = [
        resource
        for resource in template_json["Resources"].values()
        if resource["Type"] == "AWS::IAM::Role"
    ]
    assert any("AmazonSSMManagedInstanceCore" in str(role) for role in role_resources)


def test_instance_role_can_change_only_its_own_hosted_zone_records() -> None:
    template = _build_template()

    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": "route53:ChangeResourceRecordSets",
                                "Effect": "Allow",
                                "Resource": Match.not_("*"),
                            }
                        )
                    ]
                )
            }
        },
    )
    # The resource is an Fn::Join'd ARN; confirm it's actually scoped to our
    # zone. Search all IAM::Policy resources — the custom resource behind
    # the frp token parameter has one of its own too.
    policies = [
        resource
        for resource in template.to_json()["Resources"].values()
        if resource["Type"] == "AWS::IAM::Policy"
    ]
    assert any("Z0FAKEZONE" in str(policy) for policy in policies)


def test_instance_role_can_read_frp_token_parameter() -> None:
    template = _build_template()

    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": "ssm:GetParameter",
                                "Effect": "Allow",
                            }
                        )
                    ]
                )
            }
        },
    )


def test_frp_token_stored_as_secure_string_parameter() -> None:
    # AWS::SSM::Parameter doesn't support Type: SecureString in
    # CloudFormation, so this goes through a Custom::AWS resource that calls
    # ssm:PutParameter directly instead.
    template = _build_template()

    template.has_resource_properties(
        "Custom::AWS",
        {
            "Create": Match.serialized_json(
                Match.object_like(
                    {
                        "service": "SSM",
                        "action": "PutParameter",
                        "parameters": Match.object_like(
                            {
                                "Name": "/reverse-tunnel/frp-token",
                                "Type": "SecureString",
                                "Value": "test-token",
                            }
                        ),
                    }
                )
            )
        },
    )


def test_elastic_ip_is_allocated_and_associated_to_instance() -> None:
    template = _build_template()

    template.has_resource_properties(
        "AWS::EC2::EIP",
        {"Domain": "vpc", "InstanceId": Match.any_value()},
    )


def test_wildcard_dns_record_points_at_elastic_ip() -> None:
    template = _build_template()

    template.has_resource_properties(
        "AWS::Route53::RecordSet",
        {
            "Name": "*.dasbd72.com.",
            "Type": "A",
            "HostedZoneId": "Z0FAKEZONE",
        },
    )


def test_no_extra_port_range_opened_by_default() -> None:
    template = _build_template()

    template.has_resource_properties(
        "AWS::EC2::SecurityGroup",
        {
            "SecurityGroupIngress": Match.not_(
                Match.array_with([Match.object_like({"FromPort": 20000})])
            )
        },
    )


def test_security_group_opens_extra_tcp_and_udp_port_range_when_configured() -> None:
    template = _build_template(extra_port_range=(20000, 20100))

    for protocol in ("tcp", "udp"):
        template.has_resource_properties(
            "AWS::EC2::SecurityGroup",
            {
                "SecurityGroupIngress": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "CidrIp": "0.0.0.0/0",
                                "IpProtocol": protocol,
                                "FromPort": 20000,
                                "ToPort": 20100,
                            }
                        )
                    ]
                )
            },
        )

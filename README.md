# AWS Reverse Tunnel

Expose a home-network service at `<name>.<domain>` — no inbound ports opened at home.

```
Browser -> Route53 -> EC2 (Caddy + frps) -> tunnel -> home PC (frpc) -> your app
```

## Setup & Usage

**1. Install the CLI:**

```sh
# anywhere else — install frpc yourself first:
# https://github.com/fatedier/frp/releases
uv tool install 'aws-reverse-tunnel[infra]'
```

**2. Deploy the AWS side**

AWS stack: EC2 + frps/Caddy, Elastic IP, wildcard DNS, IAM role

```sh
aws-reverse-tunnel infra config --domain dasbd72.com   # zone ID + region auto-detected
aws-reverse-tunnel infra deploy
```

- `infra config`
  - `--domain` is the base domain for your tunnel subdomains (e.g. `dasbd72.com`).
  - `--hosted-zone-id` is the Route53 hosted zone ID for that domain (auto-detected).
  - `--region` is the AWS region to deploy the stack in (auto-detected from your AWS CLI config).
  - `--yes` skips confirmation prompts.

Note the `ElasticIp` output (or run `aws-reverse-tunnel infra status` anytime).

**3.1. Configure the tunnel client, then expose a service:**

```sh
aws-reverse-tunnel frpc config   # server address + domain + region + token auto-detected from step 2
aws-reverse-tunnel frpc add openwebui 192.168.50.10:8080
# -> https://openwebui.dasbd72.com
```

- `frpc config`
  - `--server-addr` is the public IP of the EC2 instance (auto-detected from step 2).
  - `--base-domain` is the base domain for your tunnel subdomains (auto-detected from step 2).
  - `--region` is the AWS region to deploy the stack in (auto-detected from your AWS CLI config).
  - `--token` is the frp auth token (fetched from SSM if this is the same machine that ran step 2).
- `frpc add <name> <local-ip>:<local-port>` adds a new tunnel for the given local service.
- `frpc list` lists all configured tunnels.
- `frpc del <name>` removes a configured tunnel.


**3.2. Run frpc client in other machines:**

```sh
# Get the frpc config
aws-reverse-tunnel frpc render
```

## Development

```sh
uv sync --all-extras
uv run pytest
uv run mypy src tests
uv run pre-commit run --all-files
```

**Layout:**
- `src/aws_reverse_tunnel/` — the CLI, including `infra/` (the CDK stack,
  bundled into the package so `infra deploy` works without cloning this repo)

**Known limitation:** the frp auth token is generated in CDK app code, so its
plaintext ends up in the synthesized CloudFormation template (stored as a
real SecureString at rest, but visible to anyone who can read the stack).
Fine for this personal, single-account setup.

**License:** MIT — see [LICENSE](LICENSE).

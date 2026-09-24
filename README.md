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
uv tool install 'aws-reverse-tunnel[infra]'   # drop [infra] if you won't manage the AWS stack
```

**2. Deploy the AWS side** (EC2 + frps/Caddy, Elastic IP, wildcard DNS, IAM role) —
needs the `[infra]` extra above, which also gives you the `cdk` CLI (via
`aws-cdk-cli`; no separate Node.js/npm install needed):

```sh
aws-reverse-tunnel infra config --domain dasbd72.com   # zone ID + region auto-detected
aws-reverse-tunnel infra deploy
```

`infra config` finds your Route53 hosted zone ID from `--domain` automatically,
and defaults `--region` to your AWS CLI's configured region — pass
`--hosted-zone-id`/`--region` explicitly only if auto-detection doesn't apply
(e.g. multiple zones for the same name). Re-running `infra config` with
different values asks before overwriting (skip with `--yes`).

Note the `ElasticIp` output (or run `aws-reverse-tunnel infra status` anytime).

**3. Configure the tunnel client, then expose a service:**

```sh
aws-reverse-tunnel frpc config   # server address + domain + region + token auto-detected from step 2
aws-reverse-tunnel frpc add openwebui 192.168.50.10:8080
# -> https://openwebui.dasbd72.com
```

`frpc config` auto-detects `--server-addr` (the deployed stack's Elastic IP),
`--base-domain`/`--region` (from `infra config`), and `--token` (fetched from
SSM) if this is the same machine that ran step 2 — pass them explicitly
otherwise.

Other commands: `frpc list`, `frpc del <name>`, `frpc status` (tunnel client);
`infra diff`, `infra destroy` (AWS stack).

```sh
aws-reverse-tunnel frpc render \
  --server-addr 1.2.3.4 --base-domain dasbd72.com --token "$TOKEN" \
  --service openwebui=192.168.50.10:8080 > frpc.toml
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

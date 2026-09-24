from aws_reverse_tunnel.systemd_unit import render


def test_embeds_frpc_and_config_paths() -> None:
    output = render(
        frpc_path="/opt/bin/frpc",
        config_path="/home/me/.config/aws-reverse-tunnel/frpc.toml",
    )

    assert (
        "ExecStart=/opt/bin/frpc -c /home/me/.config/aws-reverse-tunnel/frpc.toml"
        in output
    )


def test_restarts_on_failure() -> None:
    output = render(frpc_path="/opt/bin/frpc", config_path="/x/frpc.toml")

    assert "Restart=always" in output

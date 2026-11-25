import network_shaper


def test_plan_hostile_both_directions():
    params = {"delay_ms": 150, "loss_pct": 5.0, "mtu_bytes": 1500}
    _, _, required = network_shaper.build_plan(params, "eth0", "both")
    cmds = [" ".join(c) for c in required]
    assert "ip link set dev eth0 mtu 1500" in cmds
    assert any(c.startswith("tc qdisc add dev eth0 root netem delay 150ms loss 5.0%") for c in cmds)
    assert any(c.startswith("tc qdisc add dev ifb0 root netem loss 5.0%") for c in cmds)
    # El retardo solo se aplica a la salida: RTT añadido = delay_ms.
    assert not any("ifb0 root netem delay" in c for c in cmds)


def test_plan_without_loss_skips_ifb():
    params = {"delay_ms": 50, "loss_pct": 0.0, "mtu_bytes": 1280}
    _, _, required = network_shaper.build_plan(params, "eth0", "both")
    assert not any("ifb0" in " ".join(c) for c in required)
    assert ["ip", "link", "set", "dev", "eth0", "mtu", "1280"] in required


def test_parse_netem_json():
    opts = {"limit": 1000, "delay": {"delay": 0.05, "jitter": 0}, "loss-random": {"loss": 0.01}}
    assert network_shaper.parse_netem(opts) == (50.0, 1.0)
    assert network_shaper.parse_netem({"limit": 1000}) == (0.0, 0.0)

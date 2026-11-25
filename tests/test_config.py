import json

import pytest

import config


def test_matrix_matches_contract():
    cfg = config.load_scenarios_config()
    assert [e["experiment_id"] for e in cfg["experiments"]] == [f"EXP_0{i}" for i in range(1, 9)]
    exp = config.get_experiment(cfg, "EXP_08")
    assert (exp["scenario_id"], exp["protocol_mode"], exp["port"]) == ("SCN_04_MTU_RESTRICTED", "PQC_HYBRID", 4434)
    assert (exp["delay_ms"], exp["loss_pct"], exp["mtu_bytes"]) == (50, 0.0, 1280)
    assert exp["openssl_group"] == "X25519MLKEM768"


def test_unknown_experiment_fails_fast():
    with pytest.raises(config.ConfigError):
        config.get_experiment(config.load_scenarios_config(), "EXP_99")


def test_port_mismatch_is_rejected(tmp_path):
    cfg = json.loads(config.SCENARIOS_CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["experiments"][1]["port"] = 4433
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(cfg))
    with pytest.raises(config.ConfigError, match="EXP_02"):
        config.load_scenarios_config(path)

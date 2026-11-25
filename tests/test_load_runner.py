import csv
import json
import socket
import struct

import pytest

import config
import load_runner


def _row(status, ms, packets=9):
    return {"connection_status": status, "handshake_time_ms": ms, "tcp_packets_estimated": packets,
            "tcp_connect_ms": 1.0, "tls_bytes_sent": 300, "tls_bytes_received": 800,
            "tcp_retransmissions": 0, "error_detail": ""}


def test_percentile_matches_numpy_linear():
    np = pytest.importorskip("numpy")
    data = [3.1, 9.0, 1.2, 4.4, 7.7, 2.0, 5.5]
    for q in (50, 95, 99):
        assert load_runner.percentile(data, q) == pytest.approx(np.percentile(data, q))


def test_summary_uses_only_successful_handshakes():
    rows = [_row("HANDSHAKE_OK", v) for v in (10.0, 20.0, 30.0)]
    rows += [_row("TIMEOUT", 3000.0, 0), _row("TCP_RESET", 5.0, 0)]
    s = load_runner.summarize(rows)
    assert s["total_attempts"] == 5 and s["successful_handshakes"] == 3
    assert s["failure_rate_pct"] == 40.0
    assert s["status_counts"] == {"HANDSHAKE_OK": 3, "TIMEOUT": 1, "TCP_RESET": 1}
    assert s["metrics_ms"]["p50_median"] == 20.0 and s["metrics_ms"]["max"] == 30.0


def test_classification():
    assert load_runner.classify_exception(socket.timeout()) == "TIMEOUT"
    assert load_runner.classify_exception(ConnectionResetError()) == "TCP_RESET"


def test_parse_tcp_info_offsets():
    raw = bytearray(232)
    struct.pack_into("<I", raw, 100, 2)          # total_retrans
    struct.pack_into("<Q", raw, 128, 1875)       # bytes_received
    struct.pack_into("<II", raw, 136, 5, 4)      # segs_out, segs_in
    struct.pack_into("<QQ", raw, 200, 1500, 22)  # bytes_sent, bytes_retrans
    assert load_runner.parse_tcp_info(bytes(raw)) == {
        "tcp_packets": 9, "bytes_sent": 1478, "bytes_received": 1875, "retransmissions": 2}


def test_csv_header_follows_contract(tmp_path):
    row = {"experiment_id": "EXP_04", "iteration": 1, "worker_id": 1, "protocol_mode": "PQC_HYBRID",
           "scenario_id": "SCN_02_4G_TYPICAL", **_row("TIMEOUT", 3000, 0)}
    path = tmp_path / "EXP_04_raw.csv"
    load_runner.write_csv(path, [row])
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        first = next(reader)
    assert header[:8] == config.RAW_CONTRACT_COLUMNS
    assert first[6] == "3000.00"


def test_network_state_mismatch_is_invalid_env(tmp_path):
    exp = config.get_experiment(config.load_scenarios_config(), "EXP_05")
    (tmp_path / "network_state.json").write_text(json.dumps({
        "scenario_id": "SCN_02_4G_TYPICAL", "shaper_status": "READY",
        "applied_mtu": 1500, "applied_delay_ms": 50, "applied_loss_pct": 1.0}))
    with pytest.raises(load_runner.InvalidEnv, match="SCN_03_MOBILE_HOSTILE"):
        load_runner.check_network_state(exp, tmp_path)

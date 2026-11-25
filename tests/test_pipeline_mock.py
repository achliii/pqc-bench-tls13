import json

import pandas as pd
import pytest

pytest.importorskip("matplotlib")

import analytics_plotter  # noqa: E402
import mock_telemetry  # noqa: E402


def test_mock_to_figures(tmp_path):
    assert mock_telemetry.main(["--output-dir", str(tmp_path)]) == 0
    m = json.loads((tmp_path / "manifests" / "EXP_06_manifest.json").read_text())
    assert m["total_attempts"] == 500 and m["data_source"] == "MOCK"
    assert len(pd.read_csv(tmp_path / "raw" / "EXP_06_raw.csv")) == 500

    assert analytics_plotter.main(["--input-dir", str(tmp_path)]) == 0
    figs = tmp_path / "figures"
    for name in ("fig1_latency_comparison_bar.png", "fig2_mtu_fragmentation_impact.png",
                 "fig3_failure_rate_hostile.png", "summary_table_ieee.csv"):
        assert (figs / name).stat().st_size > 0
    table = pd.read_csv(figs / "summary_table_ieee.csv", encoding="utf-8-sig")
    assert len(table) == 4 and (table["ΔL p50 (%)"] > 0).all()


def test_invalid_runs_are_ignored(tmp_path):
    mock_telemetry.main(["--output-dir", str(tmp_path)])
    path = tmp_path / "manifests" / "EXP_01_manifest.json"
    m = json.loads(path.read_text())
    m["run_status"] = "INVALID_ENV"
    path.write_text(json.dumps(m))
    manifests, _, _ = analytics_plotter.load_data(tmp_path)
    assert "EXP_01" not in manifests and len(manifests) == 7

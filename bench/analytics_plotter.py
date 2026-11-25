#!/usr/bin/env python3
"""Analítica y graficación científica de PQC-Bench TLS 1.3 (Sección 7.3 de la línea base).

Consume output/manifests/EXP_XX_manifest.json (solo run_status = VALID_RUN) y
output/raw/EXP_XX_raw.csv, y exporta a output/figures/ (300 dpi, estilo IEEE):

    fig1_latency_comparison_bar.png   p50 y p95 Clásico vs Híbrido en los 4 escenarios
    fig2_mtu_fragmentation_impact.png segmentos TCP por handshake (MTU 1500 vs 1280) y
                                      distribución de latencia con MTU restringido
    fig3_failure_rate_hostile.png     tasa de fallos (TIMEOUT / TCP_RESET) por escenario
    summary_table_ieee.csv            tabla para el artículo (p50, p95, ΔL, R_éxito, volumen)

Uso:
    python bench/analytics_plotter.py
    python bench/analytics_plotter.py --input-dir /tmp/mock
"""

import argparse
import json
import sys
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402

DPI = 300
IEEE_DOUBLE_COL_IN = 7.16
IEEE_SINGLE_COL_IN = 3.5

PROTO_ORDER = ["CLASSIC", "PQC_HYBRID"]
PROTO_LABEL = {"CLASSIC": "Clásico (X25519)", "PQC_HYBRID": "Híbrido (X25519MLKEM768)"}
PROTO_COLOR = {"CLASSIC": "#2a78d6", "PQC_HYBRID": "#eb6834"}
# Textura a 45° para que la identidad no dependa solo del color (impresión en grises).
PROTO_HATCH = {"CLASSIC": "", "PQC_HYBRID": "////"}

SCENARIO_LABEL = {
    "SCN_01_LAN_IDEAL": "LAN ideal\n0 ms · 0 % · 1500 B",
    "SCN_02_4G_TYPICAL": "4G típico\n50 ms · 1 % · 1500 B",
    "SCN_03_MOBILE_HOSTILE": "Móvil hostil\n150 ms · 5 % · 1500 B",
    "SCN_04_MTU_RESTRICTED": "MTU restringido\n50 ms · 0 % · 1280 B",
}
SCENARIO_SHORT = {
    "SCN_01_LAN_IDEAL": "Escenario 1: LAN ideal",
    "SCN_02_4G_TYPICAL": "Escenario 2: 4G típico",
    "SCN_03_MOBILE_HOSTILE": "Escenario 3: Móvil hostil",
    "SCN_04_MTU_RESTRICTED": "Escenario 4: MTU restringido",
}

TEXT = "#0b0b0b"
TEXT_MUTED = "#52514e"
GRID = "#e4e3df"


def setup_style():
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"],
        "font.size": 8,
        "axes.titlesize": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "axes.edgecolor": TEXT_MUTED,
        "axes.labelcolor": TEXT,
        "xtick.color": TEXT_MUTED,
        "ytick.color": TEXT_MUTED,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "axes.axisbelow": True,
        "hatch.color": "white",
        "hatch.linewidth": 0.8,
        "savefig.dpi": DPI,
        "savefig.bbox": "tight",
        "savefig.facecolor": "white",
    })
    # Sin Times New Roman instalada matplotlib avisa y usa la siguiente serif.
    warnings.filterwarnings("ignore", message="findfont")


# --------------------------------------------------------------------------- #
# Carga de datos
# --------------------------------------------------------------------------- #

def load_data(input_dir=None):
    paths = config.output_paths(input_dir)
    manifests = {}
    skipped = []
    for path in sorted(paths["manifests"].glob("EXP_*_manifest.json")):
        m = json.loads(path.read_text(encoding="utf-8"))
        if m.get("run_status") != "VALID_RUN":
            skipped.append(f"{path.name} ({m.get('run_status')})")
            continue
        manifests[m["experiment_id"]] = m
    if skipped:
        print(f"[plotter] Ignorados (no VALID_RUN): {', '.join(skipped)}")
    if not manifests:
        raise SystemExit(f"[plotter] No hay manifiestos VALID_RUN en {paths['manifests']}")

    frames = []
    for exp_id in manifests:
        raw = paths["raw"] / f"{exp_id}_raw.csv"
        if raw.exists():
            frames.append(pd.read_csv(raw))
        else:
            print(f"[plotter] Aviso: falta {raw.name}")
    raw_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=config.RAW_COLUMNS)
    return manifests, raw_df, paths


def by_scenario(manifests):
    """{scenario_id: {protocol_mode: manifest}} en el orden oficial de escenarios."""
    table = {}
    for scn in config.SCENARIOS:
        row = {m["protocol_mode"]: m for m in manifests.values() if m["scenario_id"] == scn}
        if row:
            table[scn] = row
    return table


def delta_pct(hybrid, classic):
    """Penalización relativa ΔL (ecuación 1 del artículo)."""
    if hybrid is None or classic in (None, 0):
        return None
    return (hybrid - classic) / classic * 100.0


# --------------------------------------------------------------------------- #
# Figuras
# --------------------------------------------------------------------------- #

def _bar(ax, x, height, proto, width, **kw):
    return ax.bar(x, height, width, color=PROTO_COLOR[proto], hatch=PROTO_HATCH[proto],
                  edgecolor="white", linewidth=1.0, label=PROTO_LABEL[proto], zorder=2, **kw)


def _watermark(fig, is_mock):
    if is_mock:
        fig.text(0.5, 0.5, "DATOS SIMULADOS", ha="center", va="center", fontsize=28,
                 color="#c0392b", alpha=0.18, rotation=20, weight="bold")


def _legend(fig, ax):
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles))
    fig.legend(uniq.values(), uniq.keys(), loc="upper center", ncol=len(uniq),
               frameon=False, bbox_to_anchor=(0.5, 1.04))


def fig_latency(scn_table, out_path, is_mock):
    """Fig. 1: un panel por escenario (cada uno con su propia escala lineal desde cero)."""
    scenarios = list(scn_table)
    fig, axes = plt.subplots(1, len(scenarios), figsize=(IEEE_DOUBLE_COL_IN, 2.5), squeeze=False)
    width = 0.38
    for ax, scn in zip(axes[0], scenarios):
        ymax = 0.0
        for i, metric in enumerate(["p50_median", "p95"]):
            for j, proto in enumerate(PROTO_ORDER):
                m = scn_table[scn].get(proto)
                val = m["metrics_ms"][metric] if m else None
                if val is None:
                    continue
                x = i + (j - 0.5) * width
                _bar(ax, x, val, proto, width * 0.94)
                ax.annotate(f"{val:.1f}", (x, val), xytext=(0, 2), textcoords="offset points",
                            ha="center", va="bottom", fontsize=6, color=TEXT)
                ymax = max(ymax, val)
        ax.set_xticks([0, 1], ["p50", "p95"])
        ax.set_ylim(0, ymax * 1.22 if ymax else 1)
        classic, hybrid = scn_table[scn].get("CLASSIC"), scn_table[scn].get("PQC_HYBRID")
        title = SCENARIO_LABEL.get(scn, scn)
        if classic and hybrid:
            d50 = delta_pct(hybrid["metrics_ms"]["p50_median"], classic["metrics_ms"]["p50_median"])
            if d50 is not None:
                title += f"\nΔL(p50) = {d50:+.1f} %"
        ax.set_title(title, color=TEXT, linespacing=1.3)
    axes[0][0].set_ylabel("Latencia de handshake (ms)")
    _legend(fig, axes[0][0])
    fig.tight_layout(w_pad=1.2)
    _watermark(fig, is_mock)
    fig.savefig(out_path)
    plt.close(fig)


def fig_mtu(scn_table, raw_df, out_path, is_mock):
    """Fig. 2: (a) segmentos TCP por handshake con MTU 1500 vs 1280; (b) ECDF con MTU 1280."""
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(IEEE_DOUBLE_COL_IN, 2.5),
                                     gridspec_kw={"width_ratios": [1, 1.25]})
    pairs = [("SCN_01_LAN_IDEAL", "MTU 1500 B\n(Escenario 1)"), ("SCN_04_MTU_RESTRICTED", "MTU 1280 B\n(Escenario 4)")]
    width = 0.38
    ymax = 0.0
    for i, (scn, label) in enumerate(pairs):
        for j, proto in enumerate(PROTO_ORDER):
            m = scn_table.get(scn, {}).get(proto)
            val = m.get("handshake_volume", {}).get("tcp_packets_mean") if m else None
            if val is None:
                continue
            x = i + (j - 0.5) * width
            _bar(ax_a, x, val, proto, width * 0.94)
            ax_a.annotate(f"{val:.1f}", (x, val), xytext=(0, 2), textcoords="offset points",
                          ha="center", va="bottom", fontsize=6, color=TEXT)
            ymax = max(ymax, val)
    ax_a.set_xticks([0, 1], [p[1] for p in pairs])
    ax_a.set_ylim(0, ymax * 1.2 if ymax else 1)
    ax_a.set_ylabel("Segmentos TCP por handshake (media)")
    ax_a.set_title("(a) Fragmentación del handshake", color=TEXT)

    sub = raw_df[(raw_df["scenario_id"] == "SCN_04_MTU_RESTRICTED") & (raw_df["connection_status"] == "HANDSHAKE_OK")]
    for proto in PROTO_ORDER:
        vals = np.sort(sub.loc[sub["protocol_mode"] == proto, "handshake_time_ms"].astype(float).to_numpy())
        if vals.size == 0:
            continue
        y = np.arange(1, vals.size + 1) / vals.size
        ax_b.step(vals, y, where="post", color=PROTO_COLOR[proto], linewidth=2,
                  linestyle="-" if proto == "CLASSIC" else "--", label=PROTO_LABEL[proto])
        p50 = np.percentile(vals, 50)
        ax_b.annotate(f"p50 = {p50:.1f} ms", (p50, 0.5), xytext=(6, -12 if proto == "CLASSIC" else 6),
                      textcoords="offset points", fontsize=6, color=TEXT)
    ax_b.set_ylim(0, 1.02)
    ax_b.set_xlabel("Latencia de handshake (ms)")
    ax_b.set_ylabel("Proporción acumulada")
    ax_b.grid(axis="x", color=GRID, linewidth=0.6)
    ax_b.set_title("(b) Distribución con MTU 1280 B (Escenario 4)", color=TEXT)

    _legend(fig, ax_a)
    fig.tight_layout(w_pad=2.0)
    _watermark(fig, is_mock)
    fig.savefig(out_path)
    plt.close(fig)


def fig_failures(scn_table, out_path, is_mock):
    """Fig. 3: tasa de fallos por escenario; TIMEOUT en tono pleno, TCP_RESET apilado en tono claro."""
    scenarios = list(scn_table)
    fig, ax = plt.subplots(figsize=(IEEE_SINGLE_COL_IN * 1.4, 2.6))
    width = 0.38
    ymax = 0.0
    for i, scn in enumerate(scenarios):
        for j, proto in enumerate(PROTO_ORDER):
            m = scn_table[scn].get(proto)
            if not m:
                continue
            total = m["total_attempts"] or 1
            counts = m.get("status_counts", {})
            timeout = 100.0 * counts.get("TIMEOUT", m["failed_handshakes"]) / total
            reset = 100.0 * counts.get("TCP_RESET", 0) / total
            x = i + (j - 0.5) * width
            _bar(ax, x, timeout, proto, width * 0.94)
            if reset > 0:
                ax.bar(x, reset, width * 0.94, bottom=timeout, color=PROTO_COLOR[proto], alpha=0.4,
                       edgecolor="white", linewidth=1.0, label="TCP_RESET (tono claro)", zorder=2)
            ax.annotate(f"{m['failure_rate_pct']:.1f} %", (x, timeout + reset), xytext=(0, 2),
                        textcoords="offset points", ha="center", va="bottom", fontsize=6, color=TEXT)
            ymax = max(ymax, timeout + reset)
    ax.set_xticks(range(len(scenarios)), [SCENARIO_LABEL.get(s, s) for s in scenarios], fontsize=6)
    ax.set_ylim(0, max(ymax * 1.25, 1.0))
    ax.set_ylabel("Conexiones fallidas (% de 500)")
    ax.set_title("Tono pleno: TIMEOUT (> 3000 ms); tono claro apilado: TCP_RESET", color=TEXT_MUTED, fontsize=6.5)
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles))
    ax.legend(uniq.values(), uniq.keys(), frameon=False, loc="upper left")
    fig.tight_layout()
    _watermark(fig, is_mock)
    fig.savefig(out_path)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Tabla
# --------------------------------------------------------------------------- #

def summary_table(scn_table, cfg):
    rows = []
    for scn, protos in scn_table.items():
        c, h = protos.get("CLASSIC"), protos.get("PQC_HYBRID")

        def metric(m, key):
            return m["metrics_ms"][key] if m else None

        def vol(m, key):
            return m.get("handshake_volume", {}).get(key) if m else None

        def rnd(v, nd=2):
            return None if v is None else round(v, nd)

        net = cfg["scenarios"][scn]
        rows.append({
            "Escenario": SCENARIO_SHORT.get(scn, scn),
            "scenario_id": scn,
            "Experimentos": " / ".join(m["experiment_id"] for m in (c, h) if m),
            "Latencia (ms)": net["delay_ms"],
            "Pérdida (%)": net["loss_pct"],
            "MTU (B)": net["mtu_bytes"],
            "p50 Clásico (ms)": metric(c, "p50_median"),
            "p50 Híbrido (ms)": metric(h, "p50_median"),
            "ΔL p50 (%)": rnd(delta_pct(metric(h, "p50_median"), metric(c, "p50_median")), 1),
            "p95 Clásico (ms)": metric(c, "p95"),
            "p95 Híbrido (ms)": metric(h, "p95"),
            "ΔL p95 (%)": rnd(delta_pct(metric(h, "p95"), metric(c, "p95")), 1),
            "R_éxito Clásico (%)": rnd(100.0 - c["failure_rate_pct"]) if c else None,
            "R_éxito Híbrido (%)": rnd(100.0 - h["failure_rate_pct"]) if h else None,
            "Bytes handshake Clásico": vol(c, "tls_bytes_total_mean"),
            "Bytes handshake Híbrido": vol(h, "tls_bytes_total_mean"),
            "Segmentos TCP Clásico": vol(c, "tcp_packets_mean"),
            "Segmentos TCP Híbrido": vol(h, "tcp_packets_mean"),
        })
    return pd.DataFrame(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Genera figuras y tabla IEEE de PQC-Bench.")
    parser.add_argument("--input-dir", help="directorio con raw/ y manifests/ (por defecto ./output)")
    parser.add_argument("--output-dir", help="directorio de figuras (por defecto <input-dir>/figures)")
    args = parser.parse_args(argv)

    setup_style()
    cfg = config.load_scenarios_config()
    manifests, raw_df, paths = load_data(args.input_dir)
    fig_dir = config.output_paths(args.output_dir)["base"] if args.output_dir else paths["figures"]
    fig_dir.mkdir(parents=True, exist_ok=True)

    is_mock = any(m.get("data_source") == "MOCK" for m in manifests.values())
    if is_mock:
        print("[plotter] ATENCIÓN: hay manifiestos simulados (MOCK); las figuras llevarán marca de agua.")
    missing = [e["experiment_id"] for e in cfg["experiments"] if e["experiment_id"] not in manifests]
    if missing:
        print(f"[plotter] Aviso: faltan experimentos VALID_RUN: {', '.join(missing)}")

    scn_table = by_scenario(manifests)
    outputs = {
        "fig1": fig_dir / "fig1_latency_comparison_bar.png",
        "fig2": fig_dir / "fig2_mtu_fragmentation_impact.png",
        "fig3": fig_dir / "fig3_failure_rate_hostile.png",
        "table": fig_dir / "summary_table_ieee.csv",
    }
    fig_latency(scn_table, outputs["fig1"], is_mock)
    fig_mtu(scn_table, raw_df, outputs["fig2"], is_mock)
    fig_failures(scn_table, outputs["fig3"], is_mock)
    table = summary_table(scn_table, cfg)
    table.to_csv(outputs["table"], index=False, encoding="utf-8-sig")  # BOM: Excel/Word abre bien las tildes

    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(table.drop(columns=["scenario_id"]).to_string(index=False))
    for path in outputs.values():
        print(f"[plotter] {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

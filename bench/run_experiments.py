#!/usr/bin/env python3
"""Orquesta la campaña completa dentro de pqc-client-orchestrator.

Para cada experimento aplica el flujo obligatorio del contrato 01:

    network_shaper.py --exp EXP_XX  -> network_state.json READY
    load_runner.py    --exp EXP_XX  -> EXP_XX_raw.csv + EXP_XX_manifest.json

Si cualquier paso termina en INVALID_ENV la campaña se detiene (fail-fast).
Al final restaura la red y, con --plot, genera las figuras.

Uso:
    python bench/run_experiments.py                 # EXP_01..EXP_08
    python bench/run_experiments.py --exp EXP_05 EXP_06 --plot
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import config
import network_shaper

BENCH_DIR = Path(__file__).resolve().parent


def _chown_output(output_dir):
    """Devuelve ./output al usuario del host (el contenedor corre como root)."""
    uid, gid = int(os.environ.get("HOST_UID", "0")), int(os.environ.get("HOST_GID", "0"))
    if uid == 0 or os.geteuid() != 0:
        return
    base = config.output_paths(output_dir)["base"]
    for path in [base, *base.rglob("*")]:
        try:
            os.chown(path, uid, gid)
        except OSError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ejecuta la campaña de experimentos de PQC-Bench.")
    parser.add_argument("--exp", nargs="+", help="experimentos a ejecutar (por defecto todos)")
    parser.add_argument("--output-dir", help="directorio de salida (por defecto ./output)")
    parser.add_argument("--plot", action="store_true", help="ejecutar analytics_plotter.py al terminar")
    parser.add_argument("runner_args", nargs=argparse.REMAINDER,
                        help="tras `--`, argumentos extra para load_runner.py (p. ej. -- --concurrency 10)")
    args = parser.parse_args(argv)

    try:
        cfg = config.load_scenarios_config()
        exp_ids = args.exp or [e["experiment_id"] for e in cfg["experiments"]]
        for exp_id in exp_ids:
            config.get_experiment(cfg, exp_id)
    except config.ConfigError as exc:
        print(f"[campaña] INVALID_ENV: {exc}", file=sys.stderr)
        return 2

    extra = [a for a in args.runner_args if a != "--"]
    out_args = ["--output-dir", args.output_dir] if args.output_dir else []
    t0 = time.monotonic()
    status = 0
    try:
        for n, exp_id in enumerate(exp_ids, start=1):
            print(f"\n===== [{n}/{len(exp_ids)}] {exp_id} =====")
            exp = config.get_experiment(cfg, exp_id)
            try:
                state = network_shaper.apply_scenario(exp["scenario_id"], cfg, exp_id, args.output_dir)
            except network_shaper.ShaperError as exc:
                print(f"[shaper] INVALID_ENV: {exc}", file=sys.stderr)
                status = 2
                break
            print(f"[shaper] {exp['scenario_id']} READY (delay={state['applied_delay_ms']} ms, "
                  f"loss={state['applied_loss_pct']} %, mtu={state['applied_mtu']})")
            rc = subprocess.call([sys.executable, str(BENCH_DIR / "load_runner.py"), "--exp", exp_id,
                                  *out_args, *extra])
            if rc != 0:
                print(f"[campaña] {exp_id} terminó con código {rc}; se detiene la campaña.", file=sys.stderr)
                status = rc
                break
    except KeyboardInterrupt:
        print("\n[campaña] Interrumpida por el usuario.", file=sys.stderr)
        status = 130
    finally:
        network_shaper.reset(cfg, args.output_dir)
        print("[shaper] Red restaurada (sin netem, MTU 1500).")

    if status == 0 and args.plot:
        status = subprocess.call([sys.executable, str(BENCH_DIR / "analytics_plotter.py"),
                                  *(["--input-dir", args.output_dir] if args.output_dir else [])])
    _chown_output(args.output_dir)
    print(f"\n[campaña] Fin en {time.monotonic() - t0:.0f} s (código {status}).")
    return status


if __name__ == "__main__":
    sys.exit(main())

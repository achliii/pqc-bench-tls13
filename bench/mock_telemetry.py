#!/usr/bin/env python3
"""Generador de telemetría sintética (Sección 3 de 02_telemetry_output_contract.md).

Produce los 8 pares EXP_XX_raw.csv / EXP_XX_manifest.json con distribuciones
plausibles, para desarrollar y probar analytics_plotter.py sin Docker. Las
especificaciones son las del contrato; a diferencia del script original, aquí
también se generan los CSV crudos y el manifiesto se calcula a partir de ellos,
así ambos archivos son coherentes entre sí.

Los manifiestos se marcan con "data_source": "MOCK" y el graficador estampa
"DATOS SIMULADOS" en las figuras: nunca deben llegar al artículo.

Uso:
    python bench/mock_telemetry.py                 # escribe en ./output
    python bench/mock_telemetry.py --output-dir /tmp/mock --seed 7
"""

import argparse
import math
import random
import sys

import config
import load_runner

# (exp_id, scenario, protocolo, grupo, media_ms, desv_ms, fallos_%) — tomados del contrato.
MOCK_SPECS = [
    ("EXP_01", "SCN_01_LAN_IDEAL", "CLASSIC", "x25519", 2.1, 0.4, 0.0),
    ("EXP_02", "SCN_01_LAN_IDEAL", "PQC_HYBRID", "x25519_mlkem768", 3.8, 0.7, 0.0),
    ("EXP_03", "SCN_02_4G_TYPICAL", "CLASSIC", "x25519", 108.5, 18.0, 0.4),
    ("EXP_04", "SCN_02_4G_TYPICAL", "PQC_HYBRID", "x25519_mlkem768", 168.2, 35.0, 1.8),
    ("EXP_05", "SCN_03_MOBILE_HOSTILE", "CLASSIC", "x25519", 340.0, 95.0, 4.2),
    ("EXP_06", "SCN_03_MOBILE_HOSTILE", "PQC_HYBRID", "x25519_mlkem768", 620.5, 180.0, 14.6),
    ("EXP_07", "SCN_04_MTU_RESTRICTED", "CLASSIC", "x25519", 104.0, 5.0, 0.0),
    ("EXP_08", "SCN_04_MTU_RESTRICTED", "PQC_HYBRID", "x25519_mlkem768", 206.4, 12.0, 0.0),
]

# Volumen típico observado en el testbed (bytes TLS enviados/recibidos y segmentos).
_VOLUME = {
    ("CLASSIC", 1500): (302, 787, 7),
    ("PQC_HYBRID", 1500): (1478, 1875, 9),
    ("CLASSIC", 1280): (302, 787, 7),
    ("PQC_HYBRID", 1280): (1478, 1875, 11),
}


def _lognormal(rng, mean, std):
    sigma2 = math.log(1 + (std / mean) ** 2)
    return rng.lognormvariate(math.log(mean) - sigma2 / 2, math.sqrt(sigma2))


def generate_rows(spec, cfg, rng):
    exp_id, scn_id, proto, _, mean, std, fail_pct = spec
    mtu = cfg["scenarios"][scn_id]["mtu_bytes"]
    sent, recv, packets = _VOLUME[(proto, mtu)]
    total = config.ITERATIONS_PER_EXPERIMENT * config.CONCURRENCY_USERS
    failed = set(rng.sample(range(total), round(total * fail_pct / 100.0)))
    rows = []
    for i in range(total):
        it, worker = divmod(i, config.CONCURRENCY_USERS)
        row = {
            "experiment_id": exp_id, "iteration": it + 1, "worker_id": worker + 1,
            "protocol_mode": proto, "scenario_id": scn_id,
            "tcp_connect_ms": "", "tls_bytes_sent": "", "tls_bytes_received": "",
            "tcp_retransmissions": "", "error_detail": "",
        }
        if i in failed:
            status = "TIMEOUT" if rng.random() < 0.9 else "TCP_RESET"
            row.update(connection_status=status, tcp_packets_estimated=0,
                       handshake_time_ms=float(config.HANDSHAKE_TIMEOUT_MS) if status == "TIMEOUT"
                       else round(rng.uniform(50, 1500), 3),
                       error_detail="MOCK")
        else:
            retrans = 1 if rng.random() < fail_pct / 50.0 else 0
            row.update(connection_status="HANDSHAKE_OK",
                       handshake_time_ms=round(_lognormal(rng, mean, std), 3),
                       tcp_packets_estimated=packets + 2 * retrans,
                       tcp_connect_ms=round(cfg["scenarios"][scn_id]["delay_ms"] + rng.uniform(0.1, 2), 3),
                       tls_bytes_sent=sent, tls_bytes_received=recv + rng.randint(0, 2),
                       tcp_retransmissions=retrans)
        rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Genera telemetría simulada de los 8 experimentos.")
    parser.add_argument("--output-dir", help="directorio de salida (por defecto ./output)")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)

    cfg = config.load_scenarios_config()
    paths = config.output_paths(args.output_dir)
    rng = random.Random(args.seed)
    for spec in MOCK_SPECS:
        exp_id, scn_id, proto, cipher = spec[:4]
        rows = generate_rows(spec, cfg, rng)
        load_runner.write_csv(paths["raw"] / f"{exp_id}_raw.csv", rows)
        manifest = {
            "experiment_id": exp_id, "scenario_id": scn_id, "protocol_mode": proto,
            "cipher_group": cipher, "run_status": "VALID_RUN",
            "concurrency_level": config.CONCURRENCY_USERS,
            **load_runner.summarize(rows),
            "data_source": "MOCK",
        }
        load_runner.write_json(paths["manifests"] / f"{exp_id}_manifest.json", manifest)
    print(f"[mock] 8 experimentos simulados escritos en {paths['base']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

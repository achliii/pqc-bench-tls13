"""Parámetros globales de PQC-Bench TLS 1.3 (Sección 8 de la línea base).

La matriz de experimentos vive en config/scenarios_config.json; este módulo
la carga y valida que sea coherente con las constantes de aquí (fail-fast).
"""

import json
import os
from pathlib import Path

PROJECT_NAME = "PQC_BENCH_TLS13_V1"

# cipher_group es el identificador lógico del contrato; openssl_group es el
# nombre que entienden OpenSSL 3.5 (cliente) y oqs-provider (servidor).
PROTOCOLS = {
    "CLASSIC": {"cipher_group": "x25519", "openssl_group": "X25519", "port": 4433},
    "PQC_HYBRID": {"cipher_group": "x25519_mlkem768", "openssl_group": "X25519MLKEM768", "port": 4434},
}
CONCURRENCY_USERS = 100
ITERATIONS_PER_EXPERIMENT = 5
WARMUP_REQUESTS = 10
HANDSHAKE_TIMEOUT_MS = 3000

SCENARIOS = [
    "SCN_01_LAN_IDEAL",
    "SCN_02_4G_TYPICAL",
    "SCN_03_MOBILE_HOSTILE",
    "SCN_04_MTU_RESTRICTED",
]

RUN_STATUS = ["VALID_RUN", "INVALID_ENV"]
CONNECTION_STATUS = ["HANDSHAKE_OK", "TIMEOUT", "TCP_RESET"]

# Columnas del contrato 02_telemetry_output_contract.md (en este orden),
# seguidas de columnas de extensión que no alteran a quien lea por nombre.
RAW_CONTRACT_COLUMNS = [
    "experiment_id", "iteration", "worker_id", "protocol_mode", "scenario_id",
    "connection_status", "handshake_time_ms", "tcp_packets_estimated",
]
RAW_EXTRA_COLUMNS = [
    "tcp_connect_ms", "tls_bytes_sent", "tls_bytes_received", "tcp_retransmissions", "error_detail",
]
RAW_COLUMNS = RAW_CONTRACT_COLUMNS + RAW_EXTRA_COLUMNS

# Rutas (sobrescribibles por variables de entorno para pruebas).
REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_CONFIG_PATH = Path(os.environ.get("PQC_SCENARIOS_CONFIG", REPO_ROOT / "config" / "scenarios_config.json"))
OUTPUT_DIR = Path(os.environ.get("PQC_OUTPUT_DIR", REPO_ROOT / "output"))


class ConfigError(Exception):
    pass


def output_paths(output_dir=None):
    base = Path(output_dir) if output_dir else OUTPUT_DIR
    return {
        "base": base,
        "raw": base / "raw",
        "manifests": base / "manifests",
        "figures": base / "figures",
        "logs": base / "logs",
        "network_state": base / "network_state.json",
    }


def load_scenarios_config(path=None):
    """Carga scenarios_config.json y valida la matriz contra PROTOCOLS/SCENARIOS."""
    path = Path(path) if path else SCENARIOS_CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"No se pudo leer {path}: {exc}") from exc

    scenarios = cfg.get("scenarios", {})
    for scn_id in SCENARIOS:
        if scn_id not in scenarios:
            raise ConfigError(f"Falta el escenario {scn_id} en {path}")
        params = scenarios[scn_id]
        for key in ("delay_ms", "loss_pct", "mtu_bytes"):
            if not isinstance(params.get(key), (int, float)):
                raise ConfigError(f"{scn_id}.{key} debe ser numérico")
        if not 0 <= params["loss_pct"] <= 100 or params["delay_ms"] < 0 or params["mtu_bytes"] < 576:
            raise ConfigError(f"Parámetros de red fuera de rango en {scn_id}")

    if cfg.get("loss_direction", "both") not in ("both", "egress"):
        raise ConfigError("loss_direction debe ser 'both' o 'egress'")

    seen = set()
    for exp in cfg.get("experiments", []):
        exp_id = exp.get("experiment_id")
        if exp_id in seen:
            raise ConfigError(f"experiment_id duplicado: {exp_id}")
        seen.add(exp_id)
        proto = PROTOCOLS.get(exp.get("protocol_mode"))
        if proto is None:
            raise ConfigError(f"{exp_id}: protocol_mode desconocido {exp.get('protocol_mode')!r}")
        if exp.get("scenario_id") not in scenarios:
            raise ConfigError(f"{exp_id}: scenario_id desconocido {exp.get('scenario_id')!r}")
        if exp.get("cipher_group") != proto["cipher_group"] or exp.get("port") != proto["port"]:
            raise ConfigError(f"{exp_id}: cipher_group/puerto no coinciden con PROTOCOLS[{exp['protocol_mode']}]")
    if not seen:
        raise ConfigError(f"{path} no define experimentos")
    return cfg


def get_experiment(cfg, experiment_id):
    """Devuelve el experimento enriquecido con los parámetros de su escenario."""
    for exp in cfg["experiments"]:
        if exp["experiment_id"] == experiment_id:
            merged = dict(exp)
            merged.update(cfg["scenarios"][exp["scenario_id"]])
            merged["openssl_group"] = PROTOCOLS[exp["protocol_mode"]]["openssl_group"]
            return merged
    valid = ", ".join(e["experiment_id"] for e in cfg["experiments"])
    raise ConfigError(f"Experimento {experiment_id!r} no existe (válidos: {valid})")

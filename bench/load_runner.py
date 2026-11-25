#!/usr/bin/env python3
"""Generador de carga concurrente de PQC-Bench TLS 1.3 (Sección 7.2 de la línea base).

Flujo por experimento:

    validación previa (fail-fast) -> INVALID_ENV y salida 2 si algo falla
      1. network_state.json == READY y corresponde al escenario del experimento
      2. el servidor responde en el puerto del protocolo
      3. un handshake de prueba con el grupo del experimento tiene éxito
      4. un handshake contra el puerto del OTRO protocolo falla (prueba de que el
         cliente ofrece un único grupo y no hay negociación cruzada)
      5. openssl s_client confirma el grupo negociado y que no llegan Session Tickets
    -> warm-up de 10 conexiones descartables
    -> 5 iteraciones x 100 hilos lanzados a la vez con una barrera
    -> output/raw/EXP_XX_raw.csv + output/manifests/EXP_XX_manifest.json

Por qué el proceso se re-ejecuta a sí mismo: el módulo ssl de Python 3.13 no
permite elegir el grupo de intercambio de claves, así que se fija con un
archivo OPENSSL_CONF (Groups = X25519 o X25519MLKEM768). OpenSSL lee ese
archivo al inicializarse, antes de que se pueda cambiar desde Python.

Uso:
    python bench/load_runner.py --exp EXP_04
    python bench/load_runner.py --exp EXP_01 --host 127.0.0.1 --skip-network-check   # desarrollo local
"""

import argparse
import csv
import json
import os
import platform
import re
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import config

# ssl se importa después de fijar OPENSSL_CONF (ver ensure_group_config).
ssl = None

INTER_ITERATION_PAUSE_S = 1.0
PREFLIGHT_ATTEMPTS = 5
_GROUP_ENV = "PQC_BENCH_CLIENT_GROUP"


class InvalidEnv(Exception):
    pass


# --------------------------------------------------------------------------- #
# Configuración del grupo TLS del cliente
# --------------------------------------------------------------------------- #

def openssl_group_conf(group):
    return (
        "openssl_conf = openssl_init\n"
        "[openssl_init]\n"
        "ssl_conf = ssl_sect\n"
        "[ssl_sect]\n"
        "system_default = system_default_sect\n"
        "[system_default_sect]\n"
        "MinProtocol = TLSv1.3\n"
        f"Groups = {group}\n"
    )


def ensure_group_config(group):
    """Garantiza que este proceso arrancó con OPENSSL_CONF limitado a `group`."""
    global ssl
    if os.environ.get(_GROUP_ENV) != group:
        conf = Path(tempfile.gettempdir()) / f"pqc-bench-client-{group}.cnf"
        conf.write_text(openssl_group_conf(group), encoding="utf-8")
        os.environ["OPENSSL_CONF"] = str(conf)
        os.environ[_GROUP_ENV] = group
        os.execv(sys.executable, [sys.executable] + sys.argv)
    import ssl as _ssl
    ssl = _ssl


def make_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    # Certificado autofirmado del testbed: no se valida (no afecta al tamaño del handshake).
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Sin reanudación: el cliente no pide tickets y nunca reutiliza sesiones.
    ctx.options |= ssl.OP_NO_TICKET
    return ctx


# --------------------------------------------------------------------------- #
# Medición de una conexión
# --------------------------------------------------------------------------- #

# Desplazamientos de struct tcp_info (include/uapi/linux/tcp.h).
_TCPI_TOTAL_RETRANS = 100   # u32
_TCPI_BYTES_RECEIVED = 128  # u64
_TCPI_SEGS_OUT = 136        # u32
_TCPI_SEGS_IN = 140         # u32
_TCPI_BYTES_SENT = 200      # u64 (kernel >= 4.19)
_TCPI_BYTES_RETRANS = 208   # u64
_TCPI_MIN_LEN = 216


def read_tcp_info(sock):
    """Contadores TCP del kernel para la conexión (None si no hay TCP_INFO)."""
    if not hasattr(socket, "TCP_INFO"):
        return None
    try:
        raw = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 256)
    except OSError:
        return None
    if len(raw) < _TCPI_MIN_LEN:
        return None
    return parse_tcp_info(raw)


def parse_tcp_info(raw):
    segs_out, segs_in = struct.unpack_from("<II", raw, _TCPI_SEGS_OUT)
    bytes_sent, bytes_retrans = struct.unpack_from("<QQ", raw, _TCPI_BYTES_SENT)
    return {
        "tcp_packets": segs_out + segs_in,
        "bytes_sent": bytes_sent - bytes_retrans,
        "bytes_received": struct.unpack_from("<Q", raw, _TCPI_BYTES_RECEIVED)[0],
        "retransmissions": struct.unpack_from("<I", raw, _TCPI_TOTAL_RETRANS)[0],
    }


def classify_exception(exc):
    """Mapea una excepción de socket/TLS a connection_status."""
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "TIMEOUT"
    return "TCP_RESET"


def handshake_once(ctx, ip, port, timeout_ms):
    """Abre TCP, negocia TLS 1.3 y cierra. Devuelve un dict con la medición.

    handshake_time_ms mide solo la negociación TLS (ClientHello -> Finished);
    el tiempo del saludo TCP va en tcp_connect_ms. El presupuesto de timeout
    cubre ambos, de modo que un SYN perdido también cuenta contra los 3000 ms.
    """
    timeout_s = timeout_ms / 1000.0
    result = {
        "connection_status": "HANDSHAKE_OK", "handshake_time_ms": 0.0, "tcp_packets_estimated": 0,
        "tcp_connect_ms": "", "tls_bytes_sent": "", "tls_bytes_received": "",
        "tcp_retransmissions": "", "error_detail": "",
    }
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    t0 = time.perf_counter_ns()
    t1 = None
    try:
        sock.settimeout(timeout_s)
        sock.connect((ip, port))
        t1 = time.perf_counter_ns()
        result["tcp_connect_ms"] = round((t1 - t0) / 1e6, 3)
        remaining = timeout_s - (t1 - t0) / 1e9
        if remaining <= 0:
            raise TimeoutError("timeout agotado durante el saludo TCP")
        sock.settimeout(remaining)
        tls = ctx.wrap_socket(sock, do_handshake_on_connect=False)
        sock = tls
        tls.do_handshake()
        t2 = time.perf_counter_ns()
        result["handshake_time_ms"] = round((t2 - t1) / 1e6, 3)
        info = read_tcp_info(tls)
        if info:
            result.update({
                "tcp_packets_estimated": info["tcp_packets"],
                "tls_bytes_sent": info["bytes_sent"],
                "tls_bytes_received": info["bytes_received"],
                "tcp_retransmissions": info["retransmissions"],
            })
    except Exception as exc:  # noqa: BLE001 — toda falla de conexión es un dato del experimento
        status = classify_exception(exc)
        result["connection_status"] = status
        if status == "TIMEOUT":
            result["handshake_time_ms"] = float(timeout_ms)
        else:
            start = t1 if t1 is not None else t0
            result["handshake_time_ms"] = round((time.perf_counter_ns() - start) / 1e6, 3)
        result["error_detail"] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return result


# --------------------------------------------------------------------------- #
# Validación previa (fail-fast)
# --------------------------------------------------------------------------- #

def check_network_state(exp, output_dir):
    path = config.output_paths(output_dir)["network_state"]
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidEnv(f"No se pudo leer {path}: {exc}. Ejecute antes network_shaper.py --exp {exp['experiment_id']}")
    if state.get("shaper_status") != "READY":
        raise InvalidEnv(f"network_state.json tiene shaper_status={state.get('shaper_status')!r} (se esperaba READY)")
    if state.get("scenario_id") != exp["scenario_id"]:
        raise InvalidEnv(f"network_state.json es de {state.get('scenario_id')}, el experimento requiere {exp['scenario_id']}")
    expected = (exp["mtu_bytes"], float(exp["delay_ms"]), float(exp["loss_pct"]))
    applied = (state.get("applied_mtu"), float(state.get("applied_delay_ms", -1)), float(state.get("applied_loss_pct", -1)))
    if expected != applied:
        raise InvalidEnv(f"Parámetros aplicados {applied} != esperados {expected}")
    return state


def _retry_handshake(ctx, ip, port, timeout_ms):
    """Repite ante TIMEOUT (pérdida emulada); devuelve el primer resultado no-TIMEOUT."""
    res = None
    for _ in range(PREFLIGHT_ATTEMPTS):
        res = handshake_once(ctx, ip, port, timeout_ms)
        if res["connection_status"] != "TIMEOUT":
            return res
    return res


def s_client_check(host, port, group):
    """Usa openssl s_client para confirmar grupo negociado y ausencia de tickets."""
    if shutil.which("openssl") is None:
        return {"status": "SKIPPED", "detail": "openssl CLI no disponible"}
    cmd = ["openssl", "s_client", "-connect", f"{host}:{port}", "-groups", group, "-tls1_3", "-ign_eof"]
    out = ""
    for _ in range(PREFLIGHT_ATTEMPTS):
        try:
            proc = subprocess.run(cmd, input=b"GET / HTTP/1.0\r\n\r\n", capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            continue
        out = proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")
        if "PQC-Bench" in out:
            break
        if "SSL_CONF_cmd" in out or "Error with command" in out:
            return {"status": "SKIPPED", "detail": f"el openssl local no soporta el grupo {group}"}
    if "PQC-Bench" not in out:
        raise InvalidEnv(f"openssl s_client no completó una petición a {host}:{port}")
    match = re.search(r"Negotiated TLS1\.3 group: (\S+)|Peer Temp Key: ([^,\s]+)", out)
    negotiated = next((g for g in match.groups() if g), None) if match else None
    if negotiated is None or negotiated.lower() != group.lower():
        raise InvalidEnv(f"El servidor negoció el grupo {negotiated!r}, se esperaba {group}")
    if "New Session Ticket" in out:
        raise InvalidEnv("El servidor envía Session Tickets (debe estar desactivado)")
    return {"status": "OK", "negotiated_group": negotiated, "session_tickets": 0}


def preflight(exp, ip, host, output_dir, skip_network_check, timeout_ms):
    report = {}
    if skip_network_check:
        report["network_state"] = "SKIPPED"
    else:
        state = check_network_state(exp, output_dir)
        report["network_state"] = "READY"
        report["_state"] = state

    for attempt in range(PREFLIGHT_ATTEMPTS):
        try:
            socket.create_connection((ip, exp["port"]), timeout=timeout_ms / 1000.0).close()
            break
        except (socket.timeout, TimeoutError):
            if attempt == PREFLIGHT_ATTEMPTS - 1:
                raise InvalidEnv(f"El servidor {host}:{exp['port']} no responde")
        except OSError as exc:
            raise InvalidEnv(f"No se puede conectar a {host}:{exp['port']}: {exc}")
    report["tcp_port"] = "OK"

    ctx = make_context()
    res = _retry_handshake(ctx, ip, exp["port"], timeout_ms)
    if res["connection_status"] != "HANDSHAKE_OK":
        raise InvalidEnv(f"Handshake de prueba con {exp['openssl_group']} falló: {res['error_detail']}")
    report["probe_handshake"] = "OK"

    other_port = next(p["port"] for m, p in config.PROTOCOLS.items() if m != exp["protocol_mode"])
    cross = _retry_handshake(ctx, ip, other_port, timeout_ms)
    if cross["connection_status"] == "HANDSHAKE_OK":
        raise InvalidEnv(
            f"El cliente negoció con el puerto {other_port}: ofrece grupos además de {exp['openssl_group']}. "
            "Revise que OPENSSL_CONF se esté aplicando.")
    report["group_isolation"] = "OK"

    report["s_client"] = s_client_check(host, exp["port"], exp["openssl_group"])
    return report


# --------------------------------------------------------------------------- #
# Ejecución concurrente
# --------------------------------------------------------------------------- #

def run_iteration(ctx, ip, port, concurrency, timeout_ms):
    """Lanza `concurrency` hilos que inician su handshake a la vez."""
    barrier = threading.Barrier(concurrency)
    results = [None] * concurrency

    def worker(idx):
        barrier.wait()
        results[idx] = handshake_once(ctx, ip, port, timeout_ms)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if any(r is None for r in results):
        raise InvalidEnv("No se registraron todas las conexiones de la iteración")
    return results


# --------------------------------------------------------------------------- #
# Estadística y salidas
# --------------------------------------------------------------------------- #

def percentile(values, q):
    """Percentil con interpolación lineal (equivale a numpy.percentile por defecto)."""
    if not values:
        return None
    data = sorted(values)
    k = (len(data) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


def _r(value, nd=2):
    return None if value is None else round(value, nd)


def summarize(rows):
    """Estadísticos del manifiesto. La latencia usa solo HANDSHAKE_OK (Sección 3.4)."""
    ok = [r for r in rows if r["connection_status"] == "HANDSHAKE_OK"]
    lat = [float(r["handshake_time_ms"]) for r in ok]
    counts = {s: sum(1 for r in rows if r["connection_status"] == s) for s in config.CONNECTION_STATUS}
    total = len(rows)
    failed = total - len(ok)

    def mean(vals):
        return statistics.fmean(vals) if vals else None

    def numeric(key):
        return [float(r[key]) for r in ok if r.get(key) not in ("", None)]

    packets = numeric("tcp_packets_estimated")
    sent = numeric("tls_bytes_sent")
    recv = numeric("tls_bytes_received")
    connect = numeric("tcp_connect_ms")
    return {
        "total_attempts": total,
        "successful_handshakes": len(ok),
        "failed_handshakes": failed,
        "failure_rate_pct": _r(100.0 * failed / total if total else 0.0),
        "success_rate_pct": _r(100.0 * len(ok) / total if total else 0.0),
        "status_counts": counts,
        "metrics_ms": {
            "mean": _r(mean(lat)),
            "std_dev": _r(statistics.stdev(lat) if len(lat) > 1 else (0.0 if lat else None)),
            "p50_median": _r(percentile(lat, 50)),
            "p95": _r(percentile(lat, 95)),
            "p99": _r(percentile(lat, 99)),
            "min": _r(min(lat) if lat else None),
            "max": _r(max(lat) if lat else None),
        },
        "tcp_connect_ms": {"mean": _r(mean(connect)), "p50_median": _r(percentile(connect, 50))},
        "handshake_volume": {
            "tcp_packets_mean": _r(mean(packets)),
            "tcp_packets_p50": _r(percentile(packets, 50)),
            "tls_bytes_sent_mean": _r(mean(sent), 1),
            "tls_bytes_received_mean": _r(mean(recv), 1),
            "tls_bytes_total_mean": _r(mean([a + b for a, b in zip(sent, recv)]), 1),
            "tcp_retransmissions_total": int(sum(numeric("tcp_retransmissions"))),
        },
    }


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=config.RAW_COLUMNS)
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["handshake_time_ms"] = f"{float(row['handshake_time_ms']):.2f}"
            writer.writerow(out)
    tmp.replace(path)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_experiment(exp, args):
    paths = config.output_paths(args.output_dir)
    host = args.host or os.environ.get("PQC_SERVER_HOST") or args.cfg.get("server_host", "pqc-server")
    started = _now()
    t_start = time.monotonic()
    try:
        ip = socket.gethostbyname(host)
    except OSError as exc:
        raise InvalidEnv(f"No se pudo resolver {host}: {exc}")

    print(f"[runner] {exp['experiment_id']} {exp['scenario_id']} {exp['protocol_mode']} "
          f"({exp['openssl_group']}) -> {host}:{exp['port']}")
    report = preflight(exp, ip, host, args.output_dir, args.skip_network_check, args.timeout_ms)
    network_state = report.pop("_state", None)
    print(f"[runner] validación previa OK: {report}")

    ctx = make_context()
    for _ in range(args.warmup):
        handshake_once(ctx, ip, exp["port"], args.timeout_ms)
    print(f"[runner] warm-up de {args.warmup} conexiones completado")

    rows = []
    for it in range(1, args.iterations + 1):
        results = run_iteration(ctx, ip, exp["port"], args.concurrency, args.timeout_ms)
        for worker_id, res in enumerate(results, start=1):
            rows.append({
                "experiment_id": exp["experiment_id"], "iteration": it, "worker_id": worker_id,
                "protocol_mode": exp["protocol_mode"], "scenario_id": exp["scenario_id"], **res,
            })
        ok = sum(1 for r in results if r["connection_status"] == "HANDSHAKE_OK")
        print(f"[runner] iteración {it}/{args.iterations}: {ok}/{args.concurrency} HANDSHAKE_OK")
        if it < args.iterations:
            time.sleep(INTER_ITERATION_PAUSE_S)

    raw_path = paths["raw"] / f"{exp['experiment_id']}_raw.csv"
    write_csv(raw_path, rows)

    manifest = {
        "experiment_id": exp["experiment_id"],
        "scenario_id": exp["scenario_id"],
        "protocol_mode": exp["protocol_mode"],
        "cipher_group": exp["cipher_group"],
        "run_status": "VALID_RUN",
        "concurrency_level": args.concurrency,
        **summarize(rows),
        "parameters": {
            "iterations": args.iterations,
            "warmup_requests": args.warmup,
            "handshake_timeout_ms": args.timeout_ms,
            "server_host": host,
            "port": exp["port"],
            "openssl_group": exp["openssl_group"],
            "network": {k: exp[k] for k in ("delay_ms", "loss_pct", "mtu_bytes")},
        },
        "network_state": network_state,
        "preflight": report,
        "environment": {
            "python": platform.python_version(),
            "client_openssl": ssl.OPENSSL_VERSION,
            "platform": platform.platform(),
        },
        "data_source": "TESTBED",
        "started_at": started,
        "finished_at": _now(),
        "duration_s": round(time.monotonic() - t_start, 2),
    }
    manifest_path = paths["manifests"] / f"{exp['experiment_id']}_manifest.json"
    write_json(manifest_path, manifest)
    (paths["logs"] / f"{exp['experiment_id']}_invalid_env.json").unlink(missing_ok=True)
    m = manifest["metrics_ms"]
    print(f"[runner] VALID_RUN: éxito {manifest['success_rate_pct']}% | p50={m['p50_median']} ms "
          f"p95={m['p95']} ms | {raw_path.name}, {manifest_path.name}")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Carga concurrente TLS 1.3 de PQC-Bench.")
    parser.add_argument("--exp", required=True, help="experiment_id (EXP_01..EXP_08)")
    parser.add_argument("--host", help="servidor TLS (por defecto PQC_SERVER_HOST o server_host del config)")
    parser.add_argument("--config", help="ruta alternativa a scenarios_config.json")
    parser.add_argument("--output-dir", help="directorio de salida (por defecto ./output)")
    parser.add_argument("--skip-network-check", action="store_true",
                        help="no exige network_state.json (solo para desarrollo fuera de Docker)")
    parser.add_argument("--concurrency", type=int, default=config.CONCURRENCY_USERS)
    parser.add_argument("--iterations", type=int, default=config.ITERATIONS_PER_EXPERIMENT)
    parser.add_argument("--warmup", type=int, default=config.WARMUP_REQUESTS)
    parser.add_argument("--timeout-ms", type=int, default=config.HANDSHAKE_TIMEOUT_MS)
    args = parser.parse_args(argv)

    try:
        args.cfg = config.load_scenarios_config(args.config)
        exp = config.get_experiment(args.cfg, args.exp)
        ensure_group_config(exp["openssl_group"])
        run_experiment(exp, args)
    except (config.ConfigError, InvalidEnv) as exc:
        print(f"[runner] INVALID_ENV: {exc}", file=sys.stderr)
        log = {"experiment_id": args.exp, "run_status": "INVALID_ENV", "error": str(exc), "at": _now()}
        write_json(config.output_paths(args.output_dir)["logs"] / f"{args.exp}_invalid_env.json", log)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

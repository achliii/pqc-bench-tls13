#!/usr/bin/env python3
"""Módulo moldeador de red de PQC-Bench TLS 1.3 (Sección 7.1 de la línea base).

Aplica el escenario de red de un experimento sobre la interfaz del contenedor
cliente y deja constancia verificada en output/network_state.json:

    limpieza de reglas previas
    -> MTU            (ip link set dev eth0 mtu N)
    -> latencia/pérdida (tc qdisc add dev eth0 root netem delay Xms loss Y%)
    -> pérdida de bajada (ifb0 + netem, solo si loss_direction = "both")
    -> verificación leyendo tc/ip en JSON -> network_state.json (READY | INVALID_ENV)

La latencia se aplica solo a la salida, por lo que el RTT añadido es igual a
delay_ms (la latencia que reporta OSIPTEL es de ida y vuelta). La pérdida se
aplica en ambos sentidos para que también puedan perderse los segmentos del
servidor (ServerHello + certificado), que son los que más crecen con ML-KEM.

Uso:
    python bench/network_shaper.py --exp EXP_04
    python bench/network_shaper.py --scenario SCN_03_MOBILE_HOSTILE
    python bench/network_shaper.py --reset
    python bench/network_shaper.py --exp EXP_04 --dry-run
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import config

IFB_DEVICE = "ifb0"
NETEM_LIMIT = 100000      # cola amplia: la cola de netem no debe descartar paquetes por sí misma
DEFAULT_MTU = 1500


class ShaperError(Exception):
    pass


def _cleanup_commands(iface):
    # Pueden fallar si no hay nada que borrar; se ignoran sus errores.
    return [
        ["tc", "qdisc", "del", "dev", iface, "root"],
        ["tc", "qdisc", "del", "dev", iface, "ingress"],
        ["ip", "link", "del", IFB_DEVICE],
    ]


def build_plan(params, iface, loss_direction):
    """Devuelve (cleanup, best_effort, required) como listas de comandos."""
    delay_ms = params["delay_ms"]
    loss_pct = params["loss_pct"]
    mtu = params["mtu_bytes"]

    # Sin TSO/GSO cada segmento TCP es un paquete independiente para netem,
    # de modo que la pérdida se aplica por segmento y no por super-paquete.
    best_effort = [["ethtool", "-K", iface, "tso", "off", "gso", "off", "gro", "off"]]

    required = [
        ["ip", "link", "set", "dev", iface, "mtu", str(mtu)],
        ["tc", "qdisc", "add", "dev", iface, "root", "netem",
         "delay", f"{delay_ms}ms", "loss", f"{loss_pct}%", "limit", str(NETEM_LIMIT)],
    ]
    if loss_direction == "both" and loss_pct > 0:
        required += [
            ["ip", "link", "add", IFB_DEVICE, "type", "ifb"],
            ["ip", "link", "set", "dev", IFB_DEVICE, "up"],
            ["tc", "qdisc", "add", "dev", iface, "handle", "ffff:", "ingress"],
            ["tc", "filter", "add", "dev", iface, "parent", "ffff:", "protocol", "all",
             "matchall", "action", "mirred", "egress", "redirect", "dev", IFB_DEVICE],
            ["tc", "qdisc", "add", "dev", IFB_DEVICE, "root", "netem",
             "loss", f"{loss_pct}%", "limit", str(NETEM_LIMIT)],
        ]
    return _cleanup_commands(iface), best_effort, required


def _run(cmd, check=True):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        if "Operation not permitted" in detail:
            detail += " (¿el contenedor tiene cap_add: NET_ADMIN?)"
        raise ShaperError(f"Falló `{' '.join(cmd)}`: {detail}")
    return proc


def _run_json(cmd):
    return json.loads(_run(cmd).stdout or "[]")


def _netem_options(iface):
    for qdisc in _run_json(["tc", "-j", "qdisc", "show", "dev", iface]):
        if qdisc.get("kind") == "netem" and qdisc.get("root"):
            return qdisc.get("options", {})
    return None


def parse_netem(options):
    """Convierte las opciones JSON de netem a (delay_ms, loss_pct)."""
    delay_s = options.get("delay", {}).get("delay", 0.0)
    loss = options.get("loss-random", {}).get("loss", 0.0)
    return round(delay_s * 1000.0, 3), round(loss * 100.0, 4)


def verify(params, iface, loss_direction):
    """Lee el estado real del kernel y lo compara con lo pedido."""
    links = _run_json(["ip", "-j", "link", "show", "dev", iface])
    applied_mtu = links[0]["mtu"]

    egress = _netem_options(iface)
    if egress is None:
        raise ShaperError(f"No se encontró qdisc netem raíz en {iface}")
    applied_delay, applied_loss = parse_netem(egress)

    ingress_loss = 0.0
    if loss_direction == "both" and params["loss_pct"] > 0:
        ingress = _netem_options(IFB_DEVICE)
        if ingress is None:
            raise ShaperError(f"No se encontró qdisc netem en {IFB_DEVICE} (pérdida de bajada)")
        ingress_loss = parse_netem(ingress)[1]

    mismatches = []
    if applied_mtu != params["mtu_bytes"]:
        mismatches.append(f"mtu {applied_mtu} != {params['mtu_bytes']}")
    if abs(applied_delay - params["delay_ms"]) > 0.01:
        mismatches.append(f"delay {applied_delay} != {params['delay_ms']}")
    if abs(applied_loss - params["loss_pct"]) > 0.001:
        mismatches.append(f"loss {applied_loss} != {params['loss_pct']}")
    if loss_direction == "both" and abs(ingress_loss - params["loss_pct"]) > 0.001:
        mismatches.append(f"ingress loss {ingress_loss} != {params['loss_pct']}")
    if mismatches:
        raise ShaperError("El kernel no refleja el escenario: " + "; ".join(mismatches))
    return applied_mtu, applied_delay, applied_loss, ingress_loss


def _offloads(iface):
    proc = _run(["ethtool", "-k", iface], check=False)
    wanted = ("tcp-segmentation-offload", "generic-segmentation-offload", "generic-receive-offload")
    state = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.strip().partition(":")
        if key in wanted:
            state[key] = value.strip()
    return state


def write_state(state, output_dir=None):
    path = config.output_paths(output_dir)["network_state"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def apply_scenario(scenario_id, cfg, experiment_id=None, output_dir=None):
    """Aplica y verifica el escenario. Devuelve el dict escrito en network_state.json.

    Ante cualquier error de entorno escribe shaper_status = INVALID_ENV y lanza ShaperError.
    """
    iface = cfg.get("interface", "eth0")
    loss_direction = cfg.get("loss_direction", "both")
    params = cfg["scenarios"][scenario_id]
    state = {
        "scenario_id": scenario_id,
        "experiment_id": experiment_id,
        "interface": iface,
        "loss_direction": loss_direction,
        "requested": {k: params[k] for k in ("delay_ms", "loss_pct", "mtu_bytes")},
        "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        for tool in ("tc", "ip"):
            if shutil.which(tool) is None:
                raise ShaperError(f"No se encontró `{tool}` (instale iproute2)")
        if not _run(["ip", "link", "show", "dev", iface], check=False).returncode == 0:
            raise ShaperError(f"La interfaz {iface} no existe")

        cleanup, best_effort, required = build_plan(params, iface, loss_direction)
        for cmd in cleanup:
            _run(cmd, check=False)
        for cmd in best_effort:
            if shutil.which(cmd[0]):
                _run(cmd, check=False)
        for cmd in required:
            _run(cmd)

        mtu, delay, loss, ingress_loss = verify(params, iface, loss_direction)
        state.update({
            "applied_mtu": mtu,
            "applied_delay_ms": delay,
            "applied_loss_pct": loss,
            "applied_ingress_loss_pct": ingress_loss,
            "offloads": _offloads(iface),
            "shaper_status": "READY",
        })
    except ShaperError as exc:
        state.update({"shaper_status": "INVALID_ENV", "error": str(exc)})
        write_state(state, output_dir)
        raise
    write_state(state, output_dir)
    return state


def reset(cfg, output_dir=None):
    """Elimina todas las reglas y restaura MTU 1500."""
    iface = cfg.get("interface", "eth0")
    for cmd in _cleanup_commands(iface):
        _run(cmd, check=False)
    _run(["ip", "link", "set", "dev", iface, "mtu", str(DEFAULT_MTU)], check=False)
    state = {
        "scenario_id": None,
        "interface": iface,
        "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shaper_status": "RESET",
    }
    write_state(state, output_dir)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description="Aplica un escenario de red (tc netem + MTU) de PQC-Bench.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--exp", help="experiment_id (EXP_01..EXP_08); aplica su escenario")
    target.add_argument("--scenario", help="scenario_id a aplicar directamente")
    target.add_argument("--reset", action="store_true", help="elimina las reglas y restaura MTU 1500")
    parser.add_argument("--config", help="ruta alternativa a scenarios_config.json")
    parser.add_argument("--output-dir", help="directorio de salida (por defecto ./output)")
    parser.add_argument("--dry-run", action="store_true", help="solo imprime los comandos")
    args = parser.parse_args(argv)

    try:
        cfg = config.load_scenarios_config(args.config)
        if args.reset:
            if args.dry_run:
                print("\n".join(" ".join(c) for c in _cleanup_commands(cfg.get("interface", "eth0"))))
                return 0
            reset(cfg, args.output_dir)
            print("[shaper] Reglas eliminadas; MTU restaurado a 1500.")
            return 0

        exp_id = args.exp
        scenario_id = config.get_experiment(cfg, exp_id)["scenario_id"] if exp_id else args.scenario
        if scenario_id not in cfg["scenarios"]:
            raise config.ConfigError(f"Escenario desconocido: {scenario_id}")

        if args.dry_run:
            cleanup, best_effort, required = build_plan(
                cfg["scenarios"][scenario_id], cfg.get("interface", "eth0"), cfg.get("loss_direction", "both"))
            for cmd in cleanup + best_effort + required:
                print(" ".join(cmd))
            return 0

        state = apply_scenario(scenario_id, cfg, exp_id, args.output_dir)
    except (config.ConfigError, ShaperError) as exc:
        print(f"[shaper] INVALID_ENV: {exc}", file=sys.stderr)
        return 2

    print(f"[shaper] {scenario_id}: delay={state['applied_delay_ms']} ms, "
          f"loss={state['applied_loss_pct']}% (bajada {state['applied_ingress_loss_pct']}%), "
          f"mtu={state['applied_mtu']} -> READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# PQC-Bench TLS 1.3

Banco de pruebas en contenedores que mide la penalización de latencia de *handshake*, la fragmentación TCP y la tasa de éxito de **TLS 1.3 híbrido postcuántico (X25519MLKEM768)** frente a **TLS 1.3 clásico (X25519)**, con **100 usuarios concurrentes** y bajo 4 escenarios de red degradada (8 experimentos, `EXP_01`–`EXP_08`).

Implementa la Sección 3 (Metodología) del artículo *"Evaluación empírica del rendimiento y resiliencia del intercambio de claves híbrido postcuántico en TLS 1.3 bajo condiciones de carga concurrente y red degradada"* y los contratos `01_network_scenarios_contract.md` y `02_telemetry_output_contract.md`.

## Arquitectura

```
docker compose (red bridge pqc_bench_net, 172.28.0.0/16)
├── pqc-server (172.28.0.10)            receta OQS: nginx 1.28 + OpenSSL 3.5.9 + liboqs 0.16 + oqs-provider 0.12
│   ├── :4433  TLS 1.3 solo X25519
│   └── :4434  TLS 1.3 solo X25519MLKEM768
└── pqc-client-orchestrator (172.28.0.20, NET_ADMIN)   Python 3.13 + OpenSSL 3.5.9
    ├── bench/network_shaper.py    tc netem + MTU sobre eth0  -> output/network_state.json
    ├── bench/load_runner.py       100 hilos x 5 iteraciones  -> output/raw/, output/manifests/
    ├── bench/run_experiments.py   orquesta EXP_01..EXP_08 (shaper -> runner)
    └── bench/analytics_plotter.py figuras 300 dpi + tabla   -> output/figures/
```

| Archivo | Qué contiene |
|---|---|
| `config/scenarios_config.json` | Matriz oficial de los 8 experimentos y los parámetros de red de cada escenario |
| `bench/config.py` | Parámetros globales (100 usuarios, 5 iteraciones, warm-up 10, timeout 3000 ms) y validación de la matriz |
| `server/` | Imagen del servidor compilada desde código fuente (receta oficial de Open Quantum Safe con OpenSSL 3.5): `nginx.conf` (un grupo por puerto, sin reanudación), `openssl-bench.cnf`, certificado ECDSA P-256 |
| `client/` | Imagen del cliente/orquestador (iproute2, ethtool, openssl, pandas, matplotlib) |
| `bench/mock_telemetry.py` | Datos simulados para desarrollar el graficador sin Docker |
| `tests/` | Pruebas unitarias (pytest) |

## Requisitos

- Docker y Docker Compose v2 (Linux, o Docker Desktop con WSL2 en Windows).
- Conexión a Internet la primera vez: la imagen del servidor descarga y compila OpenSSL, liboqs, oqs-provider y nginx (unos 10–15 minutos; después queda en caché).
- Para correr las pruebas y los datos simulados en el host: Python ≥ 3.11 con `pytest`, `pandas`, `matplotlib`, `numpy` (o usar `make test-docker`).

## Cómo ejecutarlo

### 1. Levantar el entorno

```bash
make up
```

(equivale a `docker compose up -d --build`). Comprobación rápida de ambos puertos:

```bash
docker compose exec pqc-client-orchestrator sh -c 'printf "GET / HTTP/1.0\r\n\r\n" | openssl s_client -connect pqc-server:4434 -groups X25519MLKEM768 -ign_eof 2>&1 | grep -E "group|PQC-Bench"'
```

Debe mostrar `Negotiated TLS1.3 group: X25519MLKEM768`.

### 2. Campaña completa (8 experimentos + figuras)

```bash
make bench
```

Tarda ~2 minutos. Al terminar, la red del contenedor queda restaurada (sin netem, MTU 1500) y los archivos de `output/` pertenecen a tu usuario.

### 3. Un experimento suelto (flujo del contrato 01, paso a paso)

```bash
docker compose exec pqc-client-orchestrator python bench/network_shaper.py --exp EXP_04
```

```bash
docker compose exec pqc-client-orchestrator python bench/load_runner.py --exp EXP_04
```

`network_shaper.py --dry-run` imprime los comandos `tc`/`ip` sin ejecutarlos; `--reset` restaura la red. También existe `make exp EXP=EXP_04`.

### 4. Regenerar figuras y tabla

```bash
make plots
```

### 5. Pruebas sin Docker (datos simulados)

```bash
make mock
```

Escribe en `output_mock/` y genera las figuras con la marca de agua **DATOS SIMULADOS** (los números del mock vienen del contrato 02 y **no** deben usarse en el artículo).

```bash
make test
```

## Salidas

| Ruta | Contenido |
|---|---|
| `output/network_state.json` | Estado verificado del kernel (`shaper_status`: `READY`, `INVALID_ENV` o `RESET`) |
| `output/raw/EXP_XX_raw.csv` | 500 filas por experimento (5 iteraciones × 100 hilos) |
| `output/manifests/EXP_XX_manifest.json` | Estadísticos (media, desviación, p50, p95, p99), tasa de fallos, volumen del handshake, validación previa, versiones |
| `output/logs/EXP_XX_invalid_env.json` | Motivo del aborto cuando la corrida es `INVALID_ENV` |
| `output/figures/fig1_latency_comparison_bar.png` | p50/p95 clásico vs. híbrido por escenario, con ΔL(p50) |
| `output/figures/fig2_mtu_fragmentation_impact.png` | (a) segmentos TCP por handshake con MTU 1500 vs 1280; (b) distribución de latencia con MTU 1280 |
| `output/figures/fig3_failure_rate_hostile.png` | Tasa de fallos (TIMEOUT / TCP_RESET) por escenario |
| `output/figures/summary_table_ieee.csv` | Tabla para el artículo: p50, p95, ΔL, R_éxito, bytes y segmentos |

El CSV crudo conserva las 8 columnas del contrato en el mismo orden y añade al final `tcp_connect_ms`, `tls_bytes_sent`, `tls_bytes_received`, `tcp_retransmissions` y `error_detail`, que alimentan la métrica de volumen del handshake (Sección 3.4).

## Resultados explicados de forma sencilla

> Datos del 25/11/2026 con el servidor en OpenSSL 3.5.9, medidos en una laptop (8 núcleos, 7 GB de RAM). La campaña se corrió **dos veces seguidas** para comprobar que los resultados se repiten. La tabla muestra la primera, que es la que está en `output/`.

**La idea en una frase:** el cifrado postcuántico (híbrido) protege contra futuras computadoras cuánticas a cambio de mandar unas 3 veces más datos al conectarse. En la práctica la diferencia de tiempo es pequeña. Solo se nota con claridad en una red perfecta, donde lo único que pesa es el cálculo.

Cada vez que abres una página segura (con candado), tu dispositivo y el servidor primero se saludan y acuerdan una clave secreta. Ese saludo se llama *handshake*. Medimos cuánto tarda con el método actual (**clásico**) y con el nuevo (**híbrido**), con 100 personas conectándose al mismo tiempo y en cuatro tipos de red.

| Tipo de red | Clásico | Híbrido | ¿Cuánto más lento es el híbrido? | Al repetir la prueba | Conexiones que fallaron (de 500) |
|---|---|---|---|---|---|
| Red perfecta (como un cable en casa) | 39 ms | 64 ms | 63 % más (25 ms) | 31 % más | Ninguna |
| 4G normal en Perú | 98 ms | 100 ms | 2 % más (2 ms) | 17 % más | Ninguna |
| Red móvil mala (mucho retraso, se pierden paquetes) | 176 ms | 187 ms | 6 % más (11 ms) | 2 % más | 0 clásico, 1 híbrido (en la repetición: 3 y 2) |
| Red con VPN (paquetes más pequeños) | 102 ms | 108 ms | 6 % más (6 ms) | 17 % más | Ninguna |

*ms = milisegundos (milésimas de segundo). Los tiempos son los del usuario típico (la mediana, p50).*

**Qué significa cada fila:**

- **Red perfecta.** Es donde el híbrido sale más lento en porcentaje (entre 31 % y 63 % según la prueba), porque aquí no hay red que esperar y solo se nota el trabajo extra del procesador. Aun así son unas 12 a 25 milésimas de segundo, algo que una persona no percibe.
- **4G normal.** Cuando aparece el retraso normal de la red, ese retraso pesa más que el cálculo extra. La diferencia queda entre 2 % y 17 %, es decir, entre 2 y 15 milésimas de segundo.
- **Red móvil mala.** Esperábamos que el híbrido sufriera más aquí, porque manda más datos y hay más que perder. No pasó: ambos tardan casi lo mismo (entre 2 % y 6 % de diferencia). Los usuarios con peor suerte (el 5 % más lento, p95) esperan unos 0.67 segundos con cualquiera de los dos. Los pocos fallos aparecieron en ambos métodos por igual. Lo que retrasa la conexión es la mala red, no el tipo de cifrado.
- **Red con VPN.** Con paquetes más pequeños, el saludo híbrido se parte en más pedazos (11 en lugar de 8). Eso le cuesta entre 6 y 16 milésimas de segundo más, poco, porque los pedazos viajan juntos sin esperar turnos adicionales.

**¿Por qué cambian los números entre una prueba y otra?** Los 100 usuarios simulados y el servidor funcionan en la misma laptop, así que compiten por el mismo procesador. Esa espera interna es la razón de que un saludo en 4G tarde unos 100 ms aunque la red solo agregue 50 ms, y de que los porcentajes cambien entre repeticiones. Lo que **sí se repite siempre** es el patrón: la mayor diferencia aparece en la red perfecta, y en las redes malas ambos métodos se comportan casi igual.

**¿Por qué el híbrido es más "pesado"?** El saludo clásico intercambia unos **1 090 bytes** y el híbrido unos **3 350 bytes**, unas 3 veces más. Es como mandar una carta de 3 hojas en lugar de 1. En una red buena casi no se nota, pero ocupa más espacio.

**Conclusión práctica:** adoptar hoy el cifrado híbrido postcuántico tiene un costo pequeño para el usuario, incluso en redes móviles peruanas. El retraso y las pérdidas de la red pesan bastante más que el cambio de cifrado.

Las figuras de `output/figures/` muestran estos mismos números en gráficos: `fig1` compara los tiempos, `fig2` los pedazos con MTU normal y con VPN, y `fig3` las conexiones fallidas.

## Decisiones de implementación

- **Medición.** `handshake_time_ms` mide solo la negociación TLS (del ClientHello al Finished del servidor), con `perf_counter_ns`. El saludo TCP va aparte en `tcp_connect_ms`. El timeout de 3000 ms cubre ambos, así que un SYN perdido también cuenta. Los 100 hilos arrancan a la vez con una barrera. Los segmentos y bytes salen de `TCP_INFO` del kernel en cada conexión, no de estimaciones.
- **Latencia y pérdida.** El retardo se aplica solo a la salida del cliente, así que el RTT añadido es igual a `delay_ms`; OSIPTEL también reporta la latencia como ida y vuelta. La pérdida se aplica **en ambos sentidos** (`ifb0` para la bajada), porque los segmentos del servidor también deben poder perderse. Esto se configura con `loss_direction` (`"both"` o `"egress"`) en `scenarios_config.json`. TSO, GSO y GRO se desactivan para que netem descarte segmentos individuales.
- **Un grupo por proceso.** OpenSSL 3.5 envía por defecto key shares de X25519MLKEM768 **y** X25519, lo que inflaría el ClientHello clásico (1536 B en lugar de 302 B). El módulo `ssl` de Python 3.13 no permite elegir el grupo, así que `load_runner.py` se relanza con un `OPENSSL_CONF` que fija `Groups = X25519` o `Groups = X25519MLKEM768`.
- **Validación temprana (fail-fast).** Antes de lanzar los hilos, `load_runner.py` verifica:
  1. que `network_state.json` esté en `READY` y corresponda al escenario;
  2. que el puerto responda;
  3. que haya un handshake de prueba correcto;
  4. que un handshake contra el puerto del **otro** protocolo falle, lo que prueba que el cliente ofrece un solo grupo;
  5. con `openssl s_client`, cuál es el grupo negociado y que el servidor no envíe Session Tickets.

  Si algo falla, la corrida termina como `INVALID_ENV` con código de salida 2, sin escribir CSV ni manifiesto, y la campaña se detiene.
- **Sin reanudación.** Se usa `ssl_session_tickets off` y `ssl_session_cache off`. Además, `NumTickets = 0` en `server/openssl-bench.cnf`, porque nginx seguía enviando NewSessionTicket en TLS 1.3 aunque los tickets estuvieran en `off`. Del lado del cliente se usa `OP_NO_TICKET`.
- **Servidor con OpenSSL 3.5.** La imagen publicada `openquantumsafe/nginx` trae OpenSSL 3.4. Por eso `server/Dockerfile` reproduce la receta oficial de Open Quantum Safe (`oqs-demos/nginx`) compilando nginx 1.28.0 con **OpenSSL 3.5.9**, **liboqs 0.16.0** y **oqs-provider 0.12.0**. Así servidor y cliente usan la misma versión de OpenSSL.
  - Ambos proveedores (`default` y `oqsprovider`) están activos. Con `config_diagnostics = 1`, si oqs-provider no carga, el servidor no arranca.
  - oqs-provider 0.12 ya no incluye su propia versión de X25519MLKEM768 cuando corre sobre OpenSSL 3.5, así que el grupo híbrido evaluado lo implementa OpenSSL de forma nativa (FIPS 203). Se comprueba con `openssl list -kem-algorithms`, que muestra `X25519MLKEM768 @ default`. oqs-provider aporta los demás algoritmos de liboqs.
- **Autenticación constante.** El servidor usa un certificado ECDSA P-256 propio en ambos puertos. La receta OQS genera por defecto un certificado ML-DSA (~7.5 KB) que habría dominado el volumen del handshake.
- **Versiones fijadas** (imágenes base por digest y etiquetas exactas de OpenSSL, liboqs, oqs-provider y nginx) para que la campaña sea reproducible.

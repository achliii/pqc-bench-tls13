# Contenedor cliente/orquestador de PQC-Bench TLS 1.3 (pqc-client-orchestrator).
# Debian 13 (trixie) trae OpenSSL 3.5, que implementa X25519MLKEM768 de forma nativa.
FROM python:3.13-slim-trixie@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50

RUN apt-get update \
 && apt-get install -y --no-install-recommends iproute2 ethtool openssl fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["sleep", "infinity"]

# Servidor criptográfico de PQC-Bench TLS 1.3 (pqc-server).
#
# Etapa 1: genera un certificado ECDSA P-256 autofirmado. Se usa la misma
# autenticación clásica en ambos puertos para que cualquier diferencia medida
# sea atribuible únicamente al intercambio de claves (la imagen OQS trae por
# defecto un certificado postcuántico de ~7.5 KB que contaminaría la medición).
FROM alpine:3.22@sha256:49340b346798875b5592e5d420241f2f9cfba5f47e62ebb1446a1b34734e898f AS pki
RUN apk add --no-cache openssl \
 && mkdir /pki \
 && openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
      -keyout /pki/server.key -out /pki/server.crt -days 3650 \
      -subj "/CN=pqc-server" \
      -addext "subjectAltName=DNS:pqc-server,IP:172.28.0.10"

# Etapa 2: nginx 1.28 + OpenSSL 3.4 + oqs-provider (liboqs) del proyecto Open Quantum Safe.
FROM openquantumsafe/nginx@sha256:6ead8d74190e15581b6aa83c0158899231c74204f7ed55810b5bbf4a50bccfb3
COPY --from=pki --chown=oqs:oqs /pki/ /opt/nginx/pki-bench/
COPY --chown=oqs:oqs nginx.conf /opt/nginx/nginx-conf/nginx.conf
COPY openssl-bench.cnf /opt/nginx/openssl-bench.cnf
ENV OPENSSL_CONF=/opt/nginx/openssl-bench.cnf
EXPOSE 4433 4434

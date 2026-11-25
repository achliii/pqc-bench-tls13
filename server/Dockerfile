# Servidor criptográfico de PQC-Bench TLS 1.3 (pqc-server).
#
# Sigue la receta oficial de Open Quantum Safe para nginx
# (github.com/open-quantum-safe/oqs-demos, nginx/Dockerfile), pero compilada
# contra OpenSSL 3.5 en lugar de 3.4, con liboqs + oqs-provider recientes.
# Así servidor y cliente usan la misma versión de OpenSSL (3.5.9).

ARG ALPINE_IMAGE=alpine:3.22@sha256:49340b346798875b5592e5d420241f2f9cfba5f47e62ebb1446a1b34734e898f
ARG OPENSSL_TAG=openssl-3.5.9
ARG LIBOQS_TAG=0.16.0
ARG OQSPROVIDER_TAG=0.12.0
ARG NGINX_VERSION=1.28.0
ARG INSTALLDIR=/opt/nginx
ARG OSSLDIR=/opt/openssl/.openssl

# --------------------------------------------------------------------------- #
# Etapa 1: compila OpenSSL 3.5 (embebido en nginx), liboqs, nginx y oqs-provider
# --------------------------------------------------------------------------- #
FROM ${ALPINE_IMAGE} AS build
ARG OPENSSL_TAG
ARG LIBOQS_TAG
ARG OQSPROVIDER_TAG
ARG NGINX_VERSION
ARG INSTALLDIR
ARG OSSLDIR

RUN apk add --no-cache build-base linux-headers libtool automake autoconf make cmake ninja \
        perl git curl pcre-dev

WORKDIR /opt
RUN git clone --depth 1 --branch ${LIBOQS_TAG} https://github.com/open-quantum-safe/liboqs \
 && git clone --depth 1 --branch ${OQSPROVIDER_TAG} https://github.com/open-quantum-safe/oqs-provider.git \
 && git clone --depth 1 --branch ${OPENSSL_TAG} https://github.com/openssl/openssl.git \
 && curl -4 -fsSL --retry 5 -o nginx-${NGINX_VERSION}.tar.gz https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz \
 && tar -zxf nginx-${NGINX_VERSION}.tar.gz && rm nginx-${NGINX_VERSION}.tar.gz

# liboqs (estática, se enlaza dentro de oqsprovider.so; con sus propias primitivas
# simétricas para no depender de otro OpenSSL distinto del 3.5 de nginx)
WORKDIR /opt/liboqs/build
RUN cmake -G Ninja -DOQS_DIST_BUILD=ON -DBUILD_SHARED_LIBS=OFF -DOQS_USE_OPENSSL=OFF \
        -DCMAKE_INSTALL_PREFIX=${INSTALLDIR} .. \
 && ninja -j"$(nproc)" && ninja install

# nginx compila OpenSSL desde el código fuente y lo instala en ${OSSLDIR}
WORKDIR /opt/nginx-${NGINX_VERSION}
RUN ./configure --prefix=${INSTALLDIR} --with-http_ssl_module \
        --with-openssl=/opt/openssl --without-http_gzip_module \
 && make -j"$(nproc)" && make install

# oqs-provider contra ese mismo OpenSSL 3.5
WORKDIR /opt/oqs-provider
RUN ln -s ${INSTALLDIR}/include/oqs ${OSSLDIR}/include \
 && cmake -DCMAKE_BUILD_TYPE=Release -DOPENSSL_ROOT_DIR=${OSSLDIR} \
        -DCMAKE_PREFIX_PATH=${INSTALLDIR} -S . -B build \
 && cmake --build build -j"$(nproc)" \
 && MODULESDIR=$(find ${OSSLDIR} -type d -name ossl-modules | head -1) \
 && cp build/lib/oqsprovider.so "${MODULESDIR}/" \
 && strip ${INSTALLDIR}/sbin/nginx "${MODULESDIR}/oqsprovider.so" ${OSSLDIR}/bin/openssl \
 && rm -rf ${OSSLDIR}/lib/*.a ${OSSLDIR}/include ${INSTALLDIR}/include ${INSTALLDIR}/lib ${OSSLDIR}/share

# Certificado ECDSA P-256 autofirmado. Se usa la misma autenticación clásica en
# ambos puertos para que cualquier diferencia medida se deba solo al intercambio
# de claves (la receta OQS genera por defecto un certificado ML-DSA de ~7.5 KB).
RUN mkdir -p ${INSTALLDIR}/pki-bench \
 && ${OSSLDIR}/bin/openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -config /opt/openssl/apps/openssl.cnf \
      -keyout ${INSTALLDIR}/pki-bench/server.key -out ${INSTALLDIR}/pki-bench/server.crt \
      -days 3650 -subj "/CN=pqc-server" \
      -addext "subjectAltName=DNS:pqc-server,IP:172.28.0.10"

# --------------------------------------------------------------------------- #
# Etapa 2: imagen de ejecución
# --------------------------------------------------------------------------- #
FROM ${ALPINE_IMAGE}
ARG INSTALLDIR
ARG OSSLDIR

RUN apk add --no-cache pcre \
 && addgroup -S oqs && adduser -S -G oqs -H oqs

COPY --from=build --chown=oqs:oqs ${INSTALLDIR} ${INSTALLDIR}
COPY --from=build ${OSSLDIR} ${OSSLDIR}
COPY --chown=oqs:oqs nginx.conf ${INSTALLDIR}/nginx-conf/nginx.conf
COPY openssl-bench.cnf ${INSTALLDIR}/openssl-bench.cnf

# nginx compilado con --with-openssl solo lee OPENSSL_CONF si está definida.
ENV PATH="${INSTALLDIR}/sbin:${OSSLDIR}/bin:${PATH}" \
    OPENSSL_CONF=${INSTALLDIR}/openssl-bench.cnf \
    OPENSSL_MODULES=${OSSLDIR}/lib/ossl-modules

USER oqs
WORKDIR ${INSTALLDIR}
EXPOSE 4433 4434
CMD ["nginx", "-c", "nginx-conf/nginx.conf", "-g", "daemon off;"]

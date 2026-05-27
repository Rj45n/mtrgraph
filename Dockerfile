FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MTRGRAPH_DB=/data/mtrgraph.db

# mtr for L3/L4 probing. Without setcap, mtr will use UDP (which works for the
# k8s case where we mostly want http-daemon and TCP probes).
RUN apt-get update \
 && apt-get install -y --no-install-recommends mtr-tiny ca-certificates dnsutils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY mtrgraph ./mtrgraph

# Run as non-root by default. For ICMP/TCP probes you can grant cap_net_raw
# via the K8s pod securityContext (NET_RAW). HTTP probes don't need it.
RUN useradd -r -u 10001 -g 0 mtrgraph \
 && mkdir -p /data \
 && chown -R 10001:0 /data /app
USER 10001:0

VOLUME ["/data"]
EXPOSE 8765

ENTRYPOINT ["python", "-m", "mtrgraph.cli"]
CMD ["doctor", "--db", "/data/mtrgraph.db"]

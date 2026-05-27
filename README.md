# mtrgraph

[🇫🇷 Français](README.fr.md) · 🇬🇧 **English**

All-in-one network & S3 latency diagnostic toolkit — colored CLI MTR, HTTP probes, S3 operations (SigV4), concurrent benchmark, real-time web dashboard, schedules with webhook alerts. Self-contained with SQLite/WAL, Docker- and Kubernetes-ready.

## Quick start

```bash
sudo apt install mtr python3-venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m mtrgraph.cli doctor                       # health-check
python -m mtrgraph.cli run 1.1.1.1 -c 10 --label test
python -m mtrgraph.cli web                          # http://127.0.0.1:8765
```

Full walkthrough: [docs/getting-started.md](docs/getting-started.md) (in French; English translation is on the roadmap).

## Features

- **Colored CLI** (Rich): per-hop mtr table with color thresholds for loss / latency / jitter.
- **Multi-protocol**: ICMP, UDP, TCP SYN (with `--port`) — traverse firewalls or measure a specific port.
- **HTTP probe**: `mtrgraph http URL` measures DNS / TCP / TLS / TTFB / total — for S3 and HTTPS APIs.
- **HTTP daemon**: `mtrgraph http-daemon URL` monitors continuously with per-stage degradation alerts.
- **Authenticated S3**: LIST/HEAD/GET/PUT/DELETE via stdlib SigV4 (AWS, MinIO, Scaleway, OVH, Cellar, Free Pro, …) + interactive web UI at `/s3`.
- **Scheduler**: automated tests (MTR / HTTP / S3 / TCP) configurable via `/schedules`, fixed or random intervals, random target pools, auto baseline comparison.
- **S3 random_ops**: alternates LIST/HEAD/GET/PUT/DELETE with **safe delete** (only objects PUT by mtrgraph itself are ever deleted).
- **Unified dashboard** at `/dashboard`: MTR + S3 on the same timeline with KPIs (P50/P95/P99) and 7 charts (stages / RTT vs TTFB / server processing / throughput / status codes / error rate / TCP retransmits).
- **s3-bench**: concurrent throughput benchmark (`mtrgraph s3-bench get|put --concurrency N --count M`).
- **TCP retransmits**: `mtrgraph tcp-stats` parses `/proc/net/snmp` to confirm what mtr sees.
- **Webhooks**: Slack-compatible POST on detected degradation.
- **Auto retention**: configurable purge (`MTRGRAPH_RETENTION_DAYS`) + VACUUM, WAL mode for safe concurrency.
- **Kubernetes-ready**: Dockerfile + Kustomize manifests + Helm chart for in-cluster probing.
- **Local SQLite**: every run (mtr + http + s3 + tcp) is stored and queryable.
- **Comparison**: `compare A B` or `compare B --baseline` (median of last N runs, scoped by target+proto+port).
- **Built-in doctor**: `doctor` checks mtr, deps, DNS, HTTPS, TCP retrans, disk, DB size, web port.

## Deploy in Kubernetes

Two flavors:

**Kustomize** (recommended for simple cases):
```bash
kubectl apply -k k8s/
kubectl -n mtrgraph port-forward svc/mtrgraph-web 8765:80
```

**Helm**:
```bash
helm install mtrgraph charts/mtrgraph --create-namespace -n mtrgraph \
  --set image.repository=registry.example.com/infra/mtrgraph \
  --set image.tag=0.1.0
```

See [docs/kubernetes.md](docs/kubernetes.md) for the full guide.

## Documentation

Most documentation is in French. Translation to English is in progress.

| Document                                             | When to read                                     |
|------------------------------------------------------|--------------------------------------------------|
| [docs/installation.md](docs/installation.md)         | Detailed setup, permissions, upgrade             |
| [docs/getting-started.md](docs/getting-started.md)   | Step-by-step first usage                         |
| [docs/reading-mtr.md](docs/reading-mtr.md)           | **How to read an MTR result correctly**          |
| [docs/cookbook-s3.md](docs/cookbook-s3.md)           | **Diagnosing S3 / HTTPS API latency**            |
| [docs/s3-testing.md](docs/s3-testing.md)             | **Authenticated S3 testing** (LIST/HEAD/GET/PUT/DELETE) |
| [docs/schedules.md](docs/schedules.md)               | **Automated scheduled tests** (MTR/HTTP/S3/TCP, fixed or random) |
| [docs/dashboard.md](docs/dashboard.md)               | **Analysis dashboard** (KPIs + 7 unified charts) |
| [docs/s3-bench.md](docs/s3-bench.md)                 | **Concurrent S3 throughput benchmark**           |
| [docs/retention.md](docs/retention.md)               | **Long-term DB management**: WAL, auto-retention, sizing |
| [docs/commands.md](docs/commands.md)                 | Reference of all CLI sub-commands                |
| [docs/web.md](docs/web.md)                           | HTTP endpoints, templates, JSON API              |
| [docs/db-schema.md](docs/db-schema.md)               | SQLite schema + useful queries                   |
| [docs/architecture.md](docs/architecture.md)         | Module overview and request flows                |
| [docs/decisions.md](docs/decisions.md)               | Technical choices and rationale                  |
| [docs/deployment.md](docs/deployment.md)             | systemd, reverse proxy, backups                  |
| [docs/kubernetes.md](docs/kubernetes.md)             | **Kubernetes deployment** (Dockerfile + manifests + Helm) |
| [docs/troubleshooting.md](docs/troubleshooting.md)   | **Diagnostics & fixes**                          |
| [docs/faq.md](docs/faq.md)                           | Frequent questions                               |
| [docs/roadmap.md](docs/roadmap.md)                   | Improvement ideas                                |

## Something wrong?

1. **Environment issue** (mtr, deps, port, DB) → `python -m mtrgraph.cli doctor` highlights problems in color.
2. **Weird MTR result** (red intermediate hop, flapping loss…) → read [docs/reading-mtr.md](docs/reading-mtr.md) **before panicking**. 80% of "problems" are actually normal.
3. **Other symptom** → [docs/troubleshooting.md](docs/troubleshooting.md) (symptom → cause → fix).

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Bug reports, feature requests, and pull requests are welcome. See [docs/roadmap.md](docs/roadmap.md) for areas that need work.

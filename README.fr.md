# mtrgraph

🇫🇷 **Français** · [🇬🇧 English](README.md)

Boîte à outils complète de diagnostic de latence réseau & S3 — MTR coloré en CLI, probes HTTP, opérations S3 (SigV4), benchmark concurrent, dashboard web temps réel, schedules avec alertes webhooks, le tout en self-contained avec SQLite/WAL et déployable en Docker/Kubernetes.

## Démarrage express

```bash
sudo apt install mtr python3-venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m mtrgraph.cli doctor                       # vérifie l'environnement
python -m mtrgraph.cli run 1.1.1.1 -c 10 --label test
python -m mtrgraph.cli web                          # http://127.0.0.1:8765
```

Walkthrough complet : [docs/getting-started.md](docs/getting-started.md).

## Fonctionnalités

- **CLI coloré** (Rich) : table mtr hop par hop avec seuils de couleur (loss, latence, jitter).
- **Multi-protocole** : ICMP, UDP, TCP SYN (avec `--port`) pour traverser firewalls / mesurer un port précis.
- **Probe HTTP** : `mtrgraph http URL` mesure DNS / TCP / TLS / TTFB / total — pour diagnostiquer S3 et APIs HTTPS.
- **Daemon HTTP** : `mtrgraph http-daemon URL` surveille en continu avec alerte si dégradation par étape.
- **Tests S3 authentifiés** : LIST/HEAD/GET/PUT/DELETE en SigV4 stdlib (AWS, MinIO, Scaleway, OVH, etc.) + UI web interactive sur `/s3`.
- **Scheduler** : tests automatiques (MTR / HTTP / S3 / TCP) configurables via `/schedules`, intervalles fixes ou aléatoires, pools de cibles aléatoires, comparaison auto à la baseline.
- **S3 random_ops** : alterne LIST/HEAD/GET/PUT/DELETE avec **suppression sécurisée** (seuls les objets PUT par mtrgraph sont supprimés).
- **Dashboard** unifié `/dashboard` : MTR + S3 sur la même timeline avec KPIs (P50/P95/P99), 7 charts (étapes / RTT vs TTFB / server processing / throughput / status codes / taux d'erreurs / TCP retrans).
- **s3-bench** : benchmark de débit concurrent (`mtrgraph s3-bench get|put --concurrency N --count M`).
- **TCP retransmissions** : `mtrgraph tcp-stats` parse `/proc/net/snmp` pour confirmer ce que MTR voit.
- **Webhooks** : POST Slack-compatible sur dégradation détectée.
- **Retention auto** : purge configurable (`MTRGRAPH_RETENTION_DAYS`) + VACUUM, WAL mode pour concurrence safe.
- **Kubernetes-ready** : Dockerfile + manifests Kustomize + chart Helm pour probe depuis l'intérieur d'un cluster.
- **SQLite local** : tous les runs (mtr + http + s3 + tcp) sont enregistrés et requêtables.
- **Comparaison** : `compare A B` ou `compare B --baseline` (médiane des N derniers runs, scopée par target+proto+port).
- **Diagnostic intégré** : `doctor` vérifie mtr, deps, DNS, HTTPS, TCP retrans, disque, DB, port web.

## Documentation

| Document                                             | Quand le lire                                   |
|------------------------------------------------------|-------------------------------------------------|
| [docs/installation.md](docs/installation.md)         | Setup détaillé, permissions, mise à jour       |
| [docs/getting-started.md](docs/getting-started.md)   | Tutoriel pas-à-pas premier usage                |
| [docs/reading-mtr.md](docs/reading-mtr.md)           | **Lire un résultat MTR sans se faire avoir**    |
| [docs/cookbook-s3.md](docs/cookbook-s3.md)           | **Diagnostiquer une lenteur S3 / API HTTPS**    |
| [docs/s3-testing.md](docs/s3-testing.md)             | **Tester un S3 authentifié** (LIST/HEAD/GET/PUT/DELETE) |
| [docs/schedules.md](docs/schedules.md)               | **Tests automatiques planifiés** (MTR/HTTP/S3/TCP, fixe ou aléatoire) |
| [docs/dashboard.md](docs/dashboard.md)               | **Dashboard analyse** (KPIs + 7 charts unifiés) |
| [docs/s3-bench.md](docs/s3-bench.md)                 | **Benchmark débit S3** concurrent (`s3-bench`)  |
| [docs/retention.md](docs/retention.md)               | **Gestion DB long terme** : WAL, retention auto, sizing |
| [docs/commands.md](docs/commands.md)                 | Référence de toutes les sous-commandes          |
| [docs/web.md](docs/web.md)                           | Endpoints HTTP, templates, API JSON             |
| [docs/db-schema.md](docs/db-schema.md)               | Schéma SQLite + requêtes utiles                 |
| [docs/architecture.md](docs/architecture.md)         | Vue d'ensemble des modules et flux              |
| [docs/decisions.md](docs/decisions.md)               | Choix techniques et leurs raisons               |
| [docs/deployment.md](docs/deployment.md)             | systemd, reverse proxy, sauvegardes             |
| [docs/kubernetes.md](docs/kubernetes.md)             | **Déploiement Kubernetes** (Dockerfile + manifests + Helm) |
| [docs/troubleshooting.md](docs/troubleshooting.md)   | **Diagnostic & résolution de problèmes**        |
| [docs/faq.md](docs/faq.md)                           | Questions courantes                             |
| [docs/roadmap.md](docs/roadmap.md)                   | Pistes d'amélioration                           |

## Tu rencontres un problème ?

1. **Souci d'environnement** (mtr, deps, port, DB) → `python -m mtrgraph.cli doctor` met en lumière les soucis en couleur.
2. **Résultat MTR qui semble bizarre** (hop intermédiaire rouge, perte qui apparaît/disparaît…) → lis [docs/reading-mtr.md](docs/reading-mtr.md) **avant de paniquer**. 80% des "problèmes" sont en fait normaux.
3. **Autre symptôme** → [docs/troubleshooting.md](docs/troubleshooting.md) (symptôme → cause → fix).

## Licence

MIT — voir [LICENSE](LICENSE).

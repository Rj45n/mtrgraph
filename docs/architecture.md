# Architecture

Stack : **Python 3.12 · Rich · FastAPI · SQLite (WAL) · Chart.js**

## Structure des modules (post-refacto 3 phases)

```
mtrgraph/
├── __init__.py
├── cli.py                  ← entry argparse, sous-commandes
├── colors.py               ← seuils + couleurs (partagés CLI/web)
├── compare.py              ← dataclasses Diff + détection sévérité MTR
├── daemon.py               ← LEGACY: HTTP-daemon CLI (avant scheduler intégré)
├── doctor.py               ← health-checks (mtr, deps, DB size, TCP retrans, …)
├── http_probe.py           ← timing probe DNS/TCP/TLS/TTFB (stdlib)
├── probe.py                ← wrapper subprocess(mtr -j) + parseur JSON
├── retention.py            ← purge auto + RetentionTask thread
├── s3_bench.py             ← benchmark concurrent GET/PUT
├── s3_client.py            ← SigV4 stdlib + 5 opérations S3
├── tcp_stats.py            ← parse /proc/net/snmp + ss -s
├── tui.py                  ← rendu Rich coloré (tables, légende, diff)
│
├── db/                     ← SQLite layer (split par domaine)
│   ├── __init__.py         ← schema + session + _connect + ré-exports
│   ├── utils.py            ← helpers purs (proto_label)
│   ├── mtr.py              ← runs, hops, baseline_hops, latest_mtr_rtt_for_ip
│   ├── http_runs.py        ← http_runs, http_samples, http_baseline
│   ├── s3.py               ← s3_runs, s3_baseline
│   ├── tracked.py          ← s3_tracked_objects (PUT trackés safe-delete)
│   ├── tcp.py              ← tcp_samples
│   └── schedules.py        ← schedules CRUD
│
├── scheduler/              ← background scheduler (split par concern)
│   ├── __init__.py         ← Scheduler class + run_now + dispatcher
│   ├── _common.py          ← now_utc, iso, next_interval
│   ├── webhooks.py         ← is_degraded + post_webhook + maybe_notify
│   └── executors/
│       ├── s3.py           ← execute + random_ops + status_with_compare
│       ├── http.py         ← execute
│       ├── mtr.py          ← execute + trigger_auto_mtr (background)
│       └── tcp.py           ← execute (sample /proc/net/snmp)
│
├── web/                    ← FastAPI app (split par groupe de routes)
│   ├── __init__.py         ← create_app + serve + wire scheduler/retention
│   ├── routes_mtr.py       ← /, /run/{id}, /compare, /api/run/{id}, /api/target/{}/history
│   ├── routes_http.py      ← /http, /http/{id}, /api/http/*
│   ├── routes_s3.py        ← /s3, /api/s3/* (test, bench, history, by-ip, filters)
│   ├── routes_schedules.py ← /schedules, /api/schedules/* (CRUD + tracked + purge)
│   ├── routes_dashboard.py ← /dashboard, /api/dashboard/* (series, kpis, tcp, errors, closest-run)
│   └── routes_admin.py     ← /api/admin/* (db-stats, retention)
│
└── templates/              ← Jinja2 (base, index, run, compare, http, s3, schedules, dashboard)
```

## Flux d'un schedule random_ops S3

1. **Scheduler tick** (1 fois/s) lit les schedules dus dans `db.due_schedules`
2. Pour chaque due → `_run_schedule(row)` :
   - `executors.s3.execute(config, db_path, sid)` :
     - Si `random_ops` : lit `db.list_tracked_alive(sid)`, pioche un op pondéré
     - Pour PUT : génère key unique `mtrgraph-bench/probe-{ts}-{uuid8}`
     - Pour HEAD/GET/DELETE : pioche dans tracked alive
     - Appelle `s3_client.{list,head,get,put,delete}_object()` (SigV4)
     - Après PUT réussi → `db.track_s3_object(...)`
     - Après DELETE réussi → `db.mark_tracked_deleted(...)`
   - `db.insert_s3_run(result)` → stocke dans `s3_runs`
   - Si `auto_mtr` : `trigger_auto_mtr(resolved_ip)` (thread non-bloquant)
   - `executors.s3.status_with_compare(...)` : upgrade le status vers warning/critical si dégradation
   - `_finalize(...)` : update `schedules.last_status/last_run_at/next_run_at`
   - `webhooks.maybe_notify(...)` : POST Slack-compat si dégradé

## Flux dashboard

1. `/dashboard` → page statique
2. JS appelle `/api/dashboard/series?endpoint=X&start_time=…` :
   - Query `s3_runs` filtrée
   - Pour chaque run → `db.latest_mtr_rtt_for_ip(resolved_ip)` (auto-MTR alimente)
   - Calcule `server_processing_ms = ttfb_ms - rtt_ms` + `throughput_mbps`
3. JS appelle `/api/dashboard/kpis` (count, p50/p95/p99, err%, ops distribution)
4. JS appelle `/api/dashboard/tcp` (séries de retrans %)
5. JS appelle `/api/dashboard/errors` (runs en erreur du filtre)
6. Render des 7 charts + KPIs + panneaux

## Background tasks

Démarrés par `web.create_app()` au startup :
- `Scheduler` (tick 1s) — exécute schedules dus
- `RetentionTask` (tick configurable, défaut 24h) — purge + VACUUM

Arrêtés au shutdown via `app.on_event("shutdown")`.

## Dépendances externes

- `mtr` (binaire système) — sortie JSON via `-j`
- `rich` — TUI/CLI couleur
- `fastapi` + `uvicorn` — serveur web
- `jinja2` — templates HTML
- Chart.js via CDN (`base.html`) — pas de bundler

Pas de dépendance AWS SDK : SigV4 est implémenté en pure stdlib (`s3_client.py`).

## Données

Une seule base SQLite : `~/.local/share/mtrgraph/mtrgraph.db` par défaut (override `--db`).
Mode WAL activé, retention auto (cf [retention.md](retention.md)).

8 tables : `runs`, `hops`, `http_runs`, `http_samples`, `s3_runs`, `s3_tracked_objects`, `tcp_samples`, `schedules`. Voir [db-schema.md](db-schema.md).

## Pourquoi ce découpage ?

Avant refacto, 3 fichiers (`web.py`, `db.py`, `scheduler.py`) faisaient 45% du code à eux trois (2169/5010 lignes). Une modification touchait beaucoup de monde, le risque de régression était élevé.

Après refacto :
- **`web/`** : ajouter un endpoint = 1 fichier à toucher, les autres routers sont isolés
- **`db/`** : ajouter une table = 1 nouveau module + 1 import dans `__init__.py`
- **`scheduler/executors/`** : ajouter un kind de probe = 1 nouveau fichier + ajout au dispatcher

Les modules publics (DB helpers, `db.insert_run` etc.) restent à plat via les re-exports dans les `__init__.py`. Aucun code appelant n'a dû être modifié.

# Retention & taille de la base SQLite

mtrgraph collecte beaucoup de données en monitoring continu. Sans retention, la DB grossit sans limite. Voici comment c'est géré et comment l'ajuster.

## Volumes typiques

Pour une config standard (1 schedule S3 toutes les 30s + 1 MTR toutes les 60s + 1 TCP toutes les 60s + auto-MTR sur chaque S3) :

| Table | Lignes/jour | Taille/jour | Taille/mois |
|-------|-------------|-------------|-------------|
| `s3_runs` | ~3000 | ~1.5 MB | ~45 MB |
| `runs` + `hops` (mtr + auto-mtr) | ~6000 + 42000 | ~12 MB | ~360 MB |
| `tcp_samples` | ~1500 | ~0.5 MB | ~15 MB |
| `http_runs` + `http_samples` | variable | ~2 MB | ~60 MB |
| **Total** | | **~16 MB** | **~480 MB** |

## Mécanismes en place

### WAL mode + PRAGMAs
SQLite est configuré au boot avec :
- `journal_mode = WAL` → readers ne bloquent pas writers (scheduler + web peuvent partager la DB sans contention)
- `synchronous = NORMAL` → safe en WAL et ~10× plus rapide que FULL
- `busy_timeout = 5000` → retry automatique 5s si une transaction est verrouillée

### Retention automatique
Un thread `RetentionTask` tourne dans le processus web (`mtrgraph web`). Toutes les 24h par défaut, il :
1. Supprime les lignes plus vieilles que `MTRGRAPH_RETENTION_DAYS` jours (30 par défaut) dans :
   - `runs` (cascade sur `hops`)
   - `http_runs` (cascade sur `http_samples`)
   - `s3_runs`
   - `tcp_samples`
   - `s3_tracked_objects` (uniquement ceux déjà marqués `deleted_at`)
2. Lance un `VACUUM` pour récupérer l'espace libre sur disque

**Tables jamais purgées automatiquement** :
- `schedules` (vos configs)
- `s3_tracked_objects` vivants (représentent des objets distants encore présents — on attend que la purge S3 les nettoie d'abord)

## Configuration

### Variables d'environnement

| Variable | Défaut | Effet |
|----------|--------|-------|
| `MTRGRAPH_RETENTION_DAYS` | 30 | Âge max des lignes en jours |
| `MTRGRAPH_RETENTION_PERIOD_HOURS` | 24 | Intervalle entre 2 passages auto |

Exemple Docker :
```bash
docker run -e MTRGRAPH_RETENTION_DAYS=15 \
           -e MTRGRAPH_RETENTION_PERIOD_HOURS=12 \
           mtrgraph:0.1.0 web --host 0.0.0.0 --port 8765 --db /data/mtrgraph.db
```

Exemple K8s (ajouter dans `deployment-web.yaml`) :
```yaml
env:
  - name: MTRGRAPH_RETENTION_DAYS
    value: "30"
  - name: MTRGRAPH_RETENTION_PERIOD_HOURS
    value: "24"
```

### CLI manuel

```bash
# Stats DB sans rien purger
mtrgraph retention --dry-run

# Purger les vieilles données + VACUUM
mtrgraph retention --max-age-days 30

# Plus agressif (15 jours, sans VACUUM si tu veux préserver le disque)
mtrgraph retention --max-age-days 15 --no-vacuum
```

### API web

```bash
# Stats détaillées (lignes par table, oldest/newest)
curl http://localhost:8765/api/admin/db-stats | jq

# Trigger une purge manuelle
curl -X POST 'http://localhost:8765/api/admin/retention?max_age_days=30'
```

## Recommandations selon usage

### Mono-utilisateur / dev / test local
- Défauts (30 jours, purge toutes les 24h) → DB stable autour de 500 MB
- Pas de souci

### Prod monitoring 24/7 sur quelques targets
- Garde les 30 jours pour avoir une bonne baseline
- PVC K8s : provisionner **2 GB minimum**, **5 GB confortable**
- Surveiller via `doctor` : alerte au-delà de 500 MB

### Prod multi-targets ou intervalles agressifs (< 30s)
- Considère 7-14 jours de retention
- Ajoute un cron K8s en bonus pour `mtrgraph retention --max-age-days 7` quotidien
- À 5+ GB stable → envisage Postgres

## Quand basculer sur Postgres ?

SQLite reste OK jusqu'à ~3-5 GB ou ~10 M lignes / table. Au-delà :
- Les requêtes du dashboard ralentissent (full-scan sur les filtres temporels)
- VACUUM devient long (plusieurs minutes)
- Concurrent writer reste single (scheduler + web partagent une seule queue d'écriture)

Pour migrer : `db/__init__.py` expose `_connect()` qui pourrait être adapté à `psycopg2`. Le schéma est standard SQL, la migration est faisable. **Pas implémenté pour l'instant**, à faire le jour où ça pique.

## Monitoring

### Via `doctor`
```bash
mtrgraph doctor
```
Inclut un check `db size` qui passe en :
- ✓ OK si < 500 MB
- ! WARN si 500 MB - 2 GB
- ✗ FAIL si > 2 GB

### Via API
```bash
curl http://localhost:8765/api/admin/db-stats
```
Retourne :
```json
{
  "size_bytes": 405504,
  "rows_runs": 161,
  "rows_hops": 1288,
  "rows_http_runs": 7,
  "rows_s3_runs": 303,
  "rows_tcp_samples": 336,
  "rows_s3_tracked_objects": 106,
  "rows_schedules": 3,
  "oldest_runs": "2026-05-26T13:48:59+00:00",
  "newest_runs": "2026-05-27T08:42:34+00:00",
  ...
}
```

### Dans les logs du container
Au démarrage : `[retention] started — max_age=30d, every 24.0h`
À chaque tick : `[retention] runs=X http=Y s3=Z tcp=W tracked=N freed=A.BMB size=C.DMB in 0.05s`

## Limites & comportements

- **VACUUM bloque les écritures** pendant quelques secondes sur des DB de plusieurs GB. Le scheduler met simplement l'écriture en attente (busy_timeout 5s).
- **Suppression d'un schedule** ne purge pas automatiquement les `s3_runs` créés par lui (volontaire — on veut garder l'historique). Si tu veux nettoyer, fais-le via SQL.
- **WAL crée 2 fichiers** à côté de la DB : `mtrgraph.db-wal` et `mtrgraph.db-shm`. Normal. Pour les inclure dans une sauvegarde, copier les 3 en même temps ou fermer toutes les connexions avant copie.

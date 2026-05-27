# Schéma SQLite

Fichier par défaut : `~/.local/share/mtrgraph/mtrgraph.db` (auto-créé). Mode **WAL** activé pour permettre des lectures pendant les écritures du scheduler.

Voir aussi [retention.md](retention.md) pour la gestion de la croissance.

## Tables

### `runs`
| col          | type    | note                                                                  |
|--------------|---------|-----------------------------------------------------------------------|
| id           | INTEGER | PK auto-increment                                                     |
| target       | TEXT    | cible mtr (IP ou hostname)                                            |
| protocol     | TEXT    | `icmp`, `udp` ou `tcp` (défaut `icmp`)                                |
| dst_port     | INTEGER | port destination pour udp/tcp (NULL pour icmp)                        |
| label        | TEXT    | libre (ex. "wifi-bureau", "vpn-on")                                   |
| cycles       | INTEGER | nombre de cycles mtr                                                  |
| started_at   | TEXT    | ISO-8601 UTC (`2026-05-26T08:14:37+00:00`)                            |
| finished_at  | TEXT    | ISO-8601 UTC                                                          |
| src          | TEXT    | hostname source rapporté par mtr                                      |

Index : `(target, protocol, dst_port, started_at)` pour les requêtes d'historique scopées par protocole/port.

Le triplet `(target, protocol, dst_port)` constitue le **scope** d'une mesure : les baselines et comparaisons l'utilisent comme clé d'agrégation.

### `hops`
| col        | type    | note                              |
|------------|---------|-----------------------------------|
| run_id     | INTEGER | FK → runs.id, ON DELETE CASCADE   |
| hop_index  | INTEGER | 1-N (champ `count` de mtr)        |
| host       | TEXT    | hostname/IP                       |
| loss_pct   | REAL    | % de perte                        |
| sent       | INTEGER | paquets envoyés                   |
| last_ms    | REAL    | latence dernier paquet            |
| avg_ms     | REAL    |                                   |
| best_ms    | REAL    |                                   |
| worst_ms   | REAL    |                                   |
| stddev_ms  | REAL    | jitter                            |

PK composite `(run_id, hop_index)`.

### `http_runs`
Une ligne par exécution de `mtrgraph http URL`.

| col              | type    | note                                                          |
|------------------|---------|---------------------------------------------------------------|
| id               | INTEGER | PK                                                            |
| url              | TEXT    | URL complète (`https://s3.eu-west-3.amazonaws.com/`)          |
| method           | TEXT    | HEAD ou GET                                                   |
| label            | TEXT    | libre                                                         |
| samples          | INTEGER | nombre de samples mesurés                                     |
| started_at       | TEXT    | ISO-8601 UTC                                                  |
| finished_at      | TEXT    | ISO-8601 UTC                                                  |
| resolved_ip      | TEXT    | IP résolue lors du premier sample                             |
| status_summary   | TEXT    | ex. `200:28,503:2,err:1`                                      |
| errors           | INTEGER | nombre de samples en erreur                                   |

Index : `(url, started_at)`.

### `http_samples`
| col          | type    | note                                          |
|--------------|---------|-----------------------------------------------|
| run_id       | INTEGER | FK → http_runs.id, ON DELETE CASCADE          |
| sample_idx   | INTEGER | 1..N                                          |
| dns_ms       | REAL    | temps de résolution DNS                       |
| tcp_ms       | REAL    | temps de connect TCP                          |
| tls_ms       | REAL    | temps de handshake TLS (NULL si HTTP)         |
| ttfb_ms      | REAL    | time-to-first-byte (depuis envoi requête)     |
| total_ms     | REAL    | dns + tcp + tls + ttfb (≈ avant fermeture)    |
| status       | INTEGER | code HTTP renvoyé (NULL si erreur)            |
| error        | TEXT    | message d'erreur (NULL si OK)                 |

PK composite `(run_id, sample_idx)`.

### `s3_tracked_objects`
Sécurité : seuls les objets PUT par mtrgraph sont supprimables. Chaque PUT en mode `random_ops` est enregistré ici.

| col | type | note |
|-----|------|------|
| id | INTEGER | PK |
| schedule_id | INTEGER | id du schedule qui l'a créé (`0` pour `s3-bench`) |
| endpoint | TEXT | endpoint S3 |
| bucket | TEXT | bucket |
| key | TEXT | clé créée |
| size_bytes | INTEGER | taille uploadée |
| created_at | TEXT | ISO-8601 UTC |
| deleted_at | TEXT | NULL si encore présent sur le bucket distant |

Index : `(schedule_id, deleted_at)`.

### `tcp_samples`
Snapshots de `/proc/net/snmp` calculés sur fenêtre `duration_s`.

| col | type | note |
|-----|------|------|
| id | INTEGER | PK |
| started_at | TEXT | début du sample |
| duration_s | REAL | fenêtre de mesure |
| retrans_pct | REAL | RetransSegs / OutSegs × 100 |
| retrans_per_s | REAL | taux retrans |
| out_per_s | REAL | OutSegs/s |
| in_per_s | REAL | InSegs/s |
| in_errs_delta | INTEGER | nouveaux InErrs sur la fenêtre |
| estab_resets_delta | INTEGER | RST reçus en ESTAB |
| active_opens_delta | INTEGER | connect() nouveaux |
| label | TEXT | étiquette |

### `schedules`
Configurations des tests planifiés (MTR / HTTP / S3 / TCP).

| col | type | note |
|-----|------|------|
| id | INTEGER | PK |
| name | TEXT | nom affiché |
| kind | TEXT | `s3` / `http` / `mtr` / `tcp` |
| config | TEXT | JSON sérialisé (creds inclus en clair, cf [schedules.md](schedules.md)) |
| schedule_mode | TEXT | `fixed` ou `random` |
| interval_s | INTEGER | si fixed |
| min_interval_s / max_interval_s | INTEGER | si random |
| enabled | INTEGER | 0/1 |
| created_at | TEXT | |
| last_run_at | TEXT | |
| next_run_at | TEXT | NULL = ré-évalue immédiatement |
| last_run_id | INTEGER | id du dernier run dans la table cible |
| last_status | TEXT | ex. `ok:vs-baseline(10)` / `warning:ttfb 100→800ms` / `err:dns:…` |
| webhook_url | TEXT | POST JSON Slack-compatible sur dégradation |

## Requêtes utiles

```sql
-- Historique latence destination pour une cible + un scope précis
SELECT r.started_at, h.avg_ms, h.loss_pct
FROM runs r JOIN hops h ON h.run_id = r.id
WHERE r.target = '8.8.8.8' AND r.protocol = 'tcp' AND r.dst_port = 443
  AND h.hop_index = (SELECT MAX(hop_index) FROM hops WHERE run_id = r.id)
ORDER BY r.started_at;

-- Lister tous les scopes existants
SELECT target, protocol, dst_port, COUNT(*) AS n
FROM runs GROUP BY target, protocol, dst_port ORDER BY n DESC;

-- Top 5 runs avec le plus de perte cumulée
SELECT r.id, r.target, r.started_at, SUM(h.loss_pct) AS loss_sum
FROM runs r JOIN hops h ON h.run_id = r.id
GROUP BY r.id ORDER BY loss_sum DESC LIMIT 5;

-- Baseline = médiane par hop sur les 10 derniers runs
-- (calculée en Python dans db.baseline_hops, SQLite n'a pas de MEDIAN)

-- Purge des runs > 30 jours
DELETE FROM runs WHERE started_at < datetime('now', '-30 days');

-- HTTP : moyenne TTFB par URL sur les 30 derniers jours
SELECT hr.url, AVG(hs.ttfb_ms) AS avg_ttfb, COUNT(*) AS n
FROM http_runs hr JOIN http_samples hs ON hs.run_id = hr.id
WHERE hr.started_at >= datetime('now', '-30 days')
GROUP BY hr.url ORDER BY avg_ttfb DESC;

-- HTTP : taux d'erreur par URL
SELECT url, SUM(errors) AS errors, SUM(samples) AS total,
       100.0 * SUM(errors) / SUM(samples) AS err_pct
FROM http_runs GROUP BY url ORDER BY err_pct DESC;
```

## Migrations

Pas de système de migration : le schéma est en `CREATE IF NOT EXISTS`. Pour faire évoluer :
1. Ajouter la nouvelle colonne dans `db.SCHEMA`.
2. Ajouter un `ALTER TABLE` idempotent dans `init_db` (vérifier via `PRAGMA table_info`).
3. Ou supprimer la DB locale si dev.

# s3-bench — benchmark de débit S3 concurrent

Mesure le **débit agrégé** en lançant N opérations en parallèle. Révèle si le throttling/cap est par-connexion (et donc scale avec la concurrency) ou global (ne scale pas).

```bash
mtrgraph s3-bench put \
  --endpoint https://s3.fr-mar.freepro.com --region fr-mar \
  --bucket mon-bucket --access-key K --secret-key S \
  --concurrency 20 --count 500 --size-kb 1024
```

## CLI

| flag | défaut | rôle |
|------|--------|------|
| `operation` | (requis) | `get` ou `put` |
| `--bucket` | (requis) | bucket cible |
| `--key-or-prefix` | `mtrgraph-bench/` | GET : clé exacte à fetcher ; PUT : prefix sous lequel créer |
| `--concurrency` | 10 | nb de threads parallèles |
| `--count` | 100 | nb d'opérations total |
| `--size-kb` | 64 | taille des objets pour PUT |
| `--no-track` | off | par défaut, on track les PUT pour purge propre. `--no-track` désactive (à tes risques) |
| `--endpoint`, `--region`, `--access-key`, `--secret-key`, `--session-token`, `-T`, `--label` | — | mêmes que les autres commandes S3 |

## Sortie

Tableau Rich avec :
- Ops total / réussies / erreurs
- Bytes transférés
- Wall time (temps total écoulé)
- **Throughput** (MB/s) — la métrique clé
- Ops/sec
- Latence p50 / p95 / p99 / min / avg / max

Chaque opération est aussi insérée dans `s3_runs` avec le label `bench` (configurable), donc visible dans `/s3` et `/dashboard`.

## Sécurité — tracking des PUT

Par défaut, **chaque PUT est enregistré** dans `s3_tracked_objects` avec `schedule_id=0` (marqueur bench). Tu peux purger après coup avec :

```bash
# Voir ce qui a été créé
sqlite3 ~/.local/share/mtrgraph/mtrgraph.db \
  "SELECT count(*), bucket FROM s3_tracked_objects
   WHERE schedule_id=0 AND deleted_at IS NULL GROUP BY bucket"

# Cleanup script (à écrire selon le besoin — pas d'endpoint /purge pour schedule_id=0 actuellement)
# Workaround : passer par mc ou aws-cli pour DELETE le prefix
```

> Pour la purge auto, prochain ajout possible : endpoint `/api/bench/purge?prefix=mtrgraph-bench/` qui supprime les objets tracés du bench.

## API web

```bash
curl -X POST http://localhost:8765/api/s3/bench -H "content-type: application/json" -d '{
  "endpoint":"https://s3.fr-mar.freepro.com","region":"fr-mar",
  "access_key":"K","secret_key":"S","bucket":"mon-bucket",
  "operation":"put","key_or_prefix":"mtrgraph-bench/",
  "concurrency":20,"count":500,"size_kb":1024
}'
```

Retourne le même summary qu'en CLI, en JSON.

## Cas d'usage

### Détecter un cap par-connexion
Lance avec `--concurrency 1`, note le throughput. Relance avec `--concurrency 10`. Si le throughput est `10× plus grand` → c'est un cap par-flow. Si c'est le même → cap global.

```bash
for c in 1 5 10 20 50; do
  echo "=== concurrency=$c ==="
  mtrgraph s3-bench put --concurrency $c --count $((c * 20)) --size-kb 1024 ... | grep -E "Throughput|p95"
done
```

### Capturer un throttling temporel
```bash
while true; do
  mtrgraph s3-bench get --concurrency 20 --count 200 \
    --key-or-prefix existing-large-object.bin \
    --label "bench-$(date +%H%M)" ...
  sleep 60
done
```

Puis dans le `/dashboard` filtré sur cette URL, tu vois les pics.

### Comparer GET vs PUT
```bash
mtrgraph s3-bench get --concurrency 10 --count 100 --key-or-prefix big.bin
mtrgraph s3-bench put --concurrency 10 --count 100 --size-kb 1024
```

GET sera souvent plus rapide (cache CDN, lectures parallèles côté backend). Si PUT est anormalement lent → souvent le bottleneck est en écriture.

## Limites

- **Mono-process** : tous les threads sont dans le même Python. GIL = pas un souci ici (I/O-bound), mais ne tire pas parti de plusieurs cores. Pour > 100 concurrence réelle, lancer plusieurs `s3-bench` en parallèle.
- **Pas de multipart** : chaque PUT est single-shot. Pour des objets > 5 MiB en environnement AWS S3 réel, utiliser un SDK avec multipart.
- **Le débit indiqué** = bytes / wall_time. Inclut TLS handshake + ouverture de connexion à chaque op (pas de keep-alive). C'est représentatif des clients courts (Lambda cold start), pas d'un client persistant.

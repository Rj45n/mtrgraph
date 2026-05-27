# Tester un S3 authentifié

`mtrgraph` parle S3 nativement (SigV4 stdlib, pas de dep boto3). Compatible avec **tous** les fournisseurs S3-API :

| Fournisseur            | Endpoint exemple                                 |
|------------------------|--------------------------------------------------|
| AWS S3                 | `https://s3.eu-west-3.amazonaws.com`             |
| MinIO                  | `https://minio.example.com` ou `http://localhost:9000` |
| Scaleway               | `https://s3.fr-par.scw.cloud`                    |
| OVH Cloud Object       | `https://s3.gra.io.cloud.ovh.net`                |
| Clever Cloud Cellar    | `https://cellar-c2.services.clever-cloud.com`    |
| Free Pro               | `https://s3.fr-mar.freepro.com`                  |
| Wasabi, Backblaze, …   | endpoint fourni par le provider                  |

5 opérations supportées : **LIST**, **HEAD**, **GET**, **PUT**, **DELETE**.

## Interface web — recommandée pour le diagnostic interactif

```bash
mtrgraph web   # puis ouvre http://127.0.0.1:8765/s3
```

La page `/s3` propose :
- formulaire avec endpoint, credentials (access/secret/STS), bucket, key ;
- sélecteur d'opération en onglets (LIST/HEAD/GET/PUT/DELETE) ;
- résultats live : timings DNS/TCP/TLS/TTFB/Total en cartes colorées, statut HTTP en pastille, résumé de la réponse, erreur si échec ;
- **graphe historique** filtrable par endpoint/bucket/opération, 3 vues : toutes étapes empilées, TTFB par IP résolue, Total par IP résolue — pour spotter qu'une IP du round-robin DNS est plus lente ;
- **table par IP résolue** : count, % erreurs, avg DNS/TCP/TLS/TTFB/Total. Code couleur vert (≤ médiane), orange (≥ 1.5×), rouge (≥ 2× ou > 10% erreurs) ;
- historique des runs avec sévérité ;
- option "mémoriser dans ce navigateur" (localStorage, jamais persisté côté serveur).

**Cas typique pour spotter une lenteur intermittente** : laisse un schedule S3 LIST tourner toutes les minutes pendant quelques heures, puis ouvre `/s3`, sélectionne ton endpoint et la vue "TTFB par IP résolue". Si la courbe d'une IP spécifique se détache (latence ou erreurs), tu as ton coupable.

**Sécurité importante** : les credentials saisis dans le formulaire sont utilisés pour la requête puis oubliés côté serveur (jamais stockés en DB). Mais ce web n'a **aucune authentification** par défaut — protège-le derrière un reverse proxy + auth si tu es en multi-utilisateurs ([deployment.md](deployment.md)).

## CLI

Credentials prioritaires : flags `--access-key/--secret-key/--session-token`, sinon `AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN`.

```bash
export AWS_ACCESS_KEY_ID=AKIA…
export AWS_SECRET_ACCESS_KEY=…
ENDPOINT=https://s3.eu-west-3.amazonaws.com
REGION=eu-west-3

# LIST — souvent la cause #1 de lenteur S3
mtrgraph s3-list   --endpoint $ENDPOINT --region $REGION --bucket mon-bucket --prefix "logs/2026/"

# HEAD un objet précis (check d'existence)
mtrgraph s3-head   --endpoint $ENDPOINT --region $REGION --bucket mon-bucket --key path/to/file.log

# GET (mesure le TTFB + débit)
mtrgraph s3-get    --endpoint $ENDPOINT --region $REGION --bucket mon-bucket --key path/to/file.log

# PUT (10 Kio random pour tester la latence d'upload, sans toucher au disque)
mtrgraph s3-put    --endpoint $ENDPOINT --region $REGION --bucket mon-bucket --key test/probe-$(date +%s) --size-kb 10

# PUT à partir d'un fichier local
mtrgraph s3-put    --endpoint $ENDPOINT --region $REGION --bucket mon-bucket --key uploads/data.bin --file ./data.bin

# DELETE
mtrgraph s3-delete --endpoint $ENDPOINT --region $REGION --bucket mon-bucket --key test/probe-1234567890

# Historique
mtrgraph s3-runs --operation list --limit 20
```

## Cas d'usage S3 typiques

### Diagnostic "S3 rame intermittent"

Workflow recommandé :
1. **Web `/s3`** : tester manuellement une LIST sur le bucket suspect avec des credentials réels, voir si la lenteur se reproduit.
2. Si oui, regarder **quelle étape** est anormale dans le panneau résultat (TLS ? TTFB ?).
3. Si TTFB > 500 ms sur LIST → le bucket est probablement trop gros (lente énumération côté backend). Ajouter un `prefix` réduit le scope.
4. Si TTFB > 1000 ms sur HEAD/GET → c'est le backend qui rame ou throttle (vérifier `status_summary` pour `503 SlowDown`).
5. Si erreurs `tls:` ou `tcp:` → c'est le réseau ou le firewall TLS, voir [cookbook-s3.md](cookbook-s3.md).

### Mesurer l'impact d'un prefix

```bash
# Sans prefix (full bucket)
mtrgraph s3-list --bucket mon-bucket
# avec prefix réduit
mtrgraph s3-list --bucket mon-bucket --prefix "logs/2026/01/"
# Comparer les deux runs dans /s3 historique
```

Si la version sans prefix prend 5× plus de temps, tu as ta cause.

### Vérifier qu'un object PUT n'a pas de pic anormal

```bash
for i in {1..20}; do
  mtrgraph s3-put --endpoint $ENDPOINT --region $REGION \
    --bucket mon-bucket --key bench/run-$i --size-kb 100 \
    --label "bench-100k"
done
mtrgraph s3-runs --operation put --limit 20
```

Tu vois immédiatement si certains uploads sont >> aux autres (queue côté backend, multipart, etc.).

### Tester multipart implicite

S3 attend du multipart au-delà de ~5 MiB. mtrgraph fait du **PUT simple** (single-shot), donc :
- Pour des objets > 5 MiB, le serveur peut refuser (RequestEntityTooLarge) ou accepter lentement.
- Pour mesurer du *vrai* multipart, utiliser un SDK côté client. Sur la roadmap : ajouter un mode `--multipart` qui fait l'upload en plusieurs `UploadPart`.

## Patterns de réponses S3

mtrgraph parse les erreurs XML S3 et les expose dans `response_summary` :

| Code              | Cause typique                                            |
|-------------------|----------------------------------------------------------|
| `200`             | OK                                                       |
| `204`             | OK pour DELETE                                           |
| `403 InvalidAccessKeyId` | Access key inconnue côté serveur                 |
| `403 SignatureDoesNotMatch` | Secret key ou région incorrects               |
| `403 AccessDenied` | Policy IAM refuse l'opération                          |
| `404 NoSuchBucket / NoSuchKey` | Cible inexistante                          |
| `405 MethodNotAllowed` | HEAD sur racine bucket (normal sur AWS S3)         |
| `503 SlowDown`    | Throttling côté backend — vraie cause de lenteur souvent |
| `500 InternalError` | Backend KO temporairement                              |

Erreurs réseau (avant que le serveur réponde) :
- `dns: …` → résolution DNS KO
- `tcp: …` → connexion TCP impossible
- `tls: …` → handshake TLS échoué (souvent firewall TLS-inspection)
- `http: …` → timeout ou RST pendant la requête

## Sécurité des credentials

- **Web** : les credentials sont POSTés en JSON dans le body de `/api/s3/test`. Le serveur les utilise pour signer la requête S3 puis les jette (pas de DB, pas de log). Le navigateur peut les mémoriser en `localStorage` si tu coches la case.
- **CLI** : préférer les variables d'env (`AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY`) plutôt que les flags (l'historique shell garde les flags).
- **K8s** : monter les credentials via `Secret` + `env` (voir [kubernetes.md](kubernetes.md)).
- Ne **jamais** exposer le web sur Internet sans auth devant. Le formulaire `/s3` permet à n'importe qui de signer des requêtes vers ton S3.

## Limites actuelles

- Pas de **multipart upload** explicite (workaround : SDK ou outil dédié).
- Pas de **virtual-hosted style** (`bucket.s3.amazonaws.com`) — uniquement path-style (`s3.amazonaws.com/bucket`). Tous les S3-compat acceptent path-style.
- Pas de **chunked SigV4 streaming** — corps signé en une fois (peut peser en RAM sur des PUT énormes).
- Pas de support natif **IAM role / STS / SSO** — utiliser un sidecar qui rafraîchit les credentials (ou boto3 plus tard via `extras_require`, sur la roadmap).
- Le `s3_runs` n'a pas de baseline auto (à la différence de `http-daemon`). Si besoin de monitoring continu d'une opération S3, lancer un cron qui rejoue `mtrgraph s3-list …`.

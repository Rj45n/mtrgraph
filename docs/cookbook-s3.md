# Cookbook : diagnostiquer une latence S3 / API HTTPS

Recette concrète pour quand une API externe devient lente. Combine `mtrgraph run` (couche réseau) et `mtrgraph http` (couche application).

> **mtrgraph fonctionne avec n'importe quel S3** (AWS, MinIO, Scaleway, OVH Object Storage, Clever Cloud Cellar, Free Pro, etc.) et plus largement n'importe quelle URL HTTPS. Les exemples utilisent indifféremment l'un ou l'autre — remplace par ton endpoint.

## TL;DR

```bash
# 1. Mesure couche réseau (TCP:443 vers l'IP S3)
S3_HOST=s3.eu-west-3.amazonaws.com
S3_IP=$(dig +short "$S3_HOST" | head -1)

python -m mtrgraph.cli run "$S3_IP" --proto tcp --port 443 -c 30 --label s3-net

# 2. Mesure couche application (DNS + TCP + TLS + TTFB)
python -m mtrgraph.cli http "https://$S3_HOST/" -c 30 --label s3-app

# 3. Si tout est OK et le problème persiste : c'est S3 lui-même ou ton code applicatif
```

## Étape 1 : le réseau est-il en cause ?

Le but est d'**éliminer** le réseau. Si la latence réseau est bonne, on sait que le problème est plus haut.

```bash
S3_HOST=s3.eu-west-3.amazonaws.com           # adapte la région
S3_IP=$(dig +short "$S3_HOST" | head -1)
echo "Testing $S3_HOST → $S3_IP"

# Baseline réseau
python -m mtrgraph.cli run "$S3_IP" --proto tcp --port 443 -c 60 --label s3-tcp-base
```

**Lecture** (voir [reading-mtr.md](reading-mtr.md)) :
- **Hop final** : latence et perte réelles vers S3.
- Si Avg < 30 ms et Loss = 0% → le réseau est **innocent**.
- Si l'Avg final monte à 100+ ms ou la perte > 1% → vrai souci réseau à investiguer.

Pour comparer ICMP vs TCP (savoir si seul TCP est dégradé) :
```bash
python -m mtrgraph.cli run "$S3_IP" --proto icmp -c 60 --label s3-icmp
python -m mtrgraph.cli compare <id_icmp> <id_tcp>
```

## Étape 2 : la couche HTTPS est-elle en cause ?

Si l'étape 1 est verte, le problème est dans la pile au-dessus. C'est là que `mtrgraph http` rentre en jeu.

```bash
python -m mtrgraph.cli http "https://$S3_HOST/" -c 30 --label s3-http-base
```

Sortie :
```
HTTP #N — https://s3.eu-west-3.amazonaws.com/
Étape   Avg ms   Best   Worst   StDev   n
DNS      8.0      0.8    27.8   12.6    30
TCP      6.1      4.8     7.5    1.1    30
TLS     33.0     23.7    47.1   10.1    30
TTFB   128.3    122.5   138.7    7.4    30
TOTAL  206.6    186.4   219.8   14.5    30
```

**Interprétation par étape** :

| Étape    | Si élevé, c'est…                                                  |
|----------|-------------------------------------------------------------------|
| **DNS**  | Résolveur lent, cache froid, mauvais DNS configuré sur le système |
| **TCP**  | Réseau lent (devrait être ≈ ce que dit le hop final de mtr)       |
| **TLS**  | Certif OCSP, négociation TLS 1.2 vs 1.3, ALPN mal config, BoringSSL vs OpenSSL |
| **TTFB** | S3/serveur lent à répondre — throttling, bucket en cold start, requête lourde |
| **Total**| Somme des précédents + transfert (HEAD → quasi 0 transfert)       |

## Patterns typiques de problèmes S3

### Cas 1 : DNS très élevé (>100 ms)
```
DNS 250 ms · TCP 5 ms · TLS 30 ms · TTFB 100 ms
```
- Résolveur DNS surchargé ou éloigné. Tester avec un autre :
  ```bash
  echo "nameserver 1.1.1.1" | sudo tee /etc/resolv.conf  # temporaire, attention
  python -m mtrgraph.cli http "https://s3..." -c 30
  ```
- Si le résolveur est OK ailleurs, problème côté config locale.

### Cas 2 : TLS très élevé (>300 ms)
```
DNS 5 ms · TCP 8 ms · TLS 450 ms · TTFB 150 ms
```
- Vérification OCSP lente (le client interroge l'autorité de certif).
- TLS 1.2 forcé (négociation en 2 RTT au lieu d'1 en TLS 1.3).
- Cipher suite obsolète.
- Tester :
  ```bash
  curl -v -w "tls=%{time_appconnect}\n" --tls-max 1.3 "https://$S3_HOST/" -o /dev/null -s 2>&1 | grep -E "TLSv|tls="
  ```

### Cas 3 : TTFB explose (>1000 ms)
```
DNS 5 ms · TCP 8 ms · TLS 50 ms · TTFB 1800 ms
```
- **C'est S3 qui rame**, pas ta machine ni le réseau.
- Causes possibles :
  - Throttling (HTTP 503 SlowDown) — vérifier `status_summary` dans le résultat.
  - Bucket dans une mauvaise région (latence aller-retour vers une autre zone géo).
  - Requête trop lourde (LIST sur un bucket énorme).
  - Cold start côté S3 sur des objets rarement accédés.
- Vérifier les status codes :
  ```bash
  python -m mtrgraph.cli http-show <id>
  ```
  Si tu vois `503:5` dans le résumé, c'est du throttling.

### Cas 4 : Tout est rapide mais ton app rame
- C'est dans **ton code** : pool de connexions, retry agressif, gestion d'erreur qui bloque.
- mtrgraph t'a permis d'éliminer toutes les couches en-dessous.

## Mode monitoring continu

Pour surveiller un endpoint S3 en continu et capturer les pics :

```bash
# Daemon réseau (TCP:443) — alerte si dégradation vs baseline
mtrgraph daemon "$S3_IP" --proto tcp --port 443 --every 300 -c 10 --label s3-net &

# Daemon HTTP — alerte par étape (DNS/TCP/TLS/TTFB/Total) ou sur taux d'erreurs
mtrgraph http-daemon "https://$S3_HOST/" --every 60 -c 5 --label s3-http &
```

Le daemon HTTP affiche en continu :
```
[14:38:12] OK http_run #2 total=58 ms  status=200:5
[14:40:14] DEGRADATION http_run #4 → TTFB warning 9→145 ms  status=200:5
[14:42:15] DEGRADATION http_run #5 → ERRORS 40% (2/5) · TTFB critical 9→850 ms  status=503:2,200:3
```

Dans le web (`/http`) :
- Sélectionner l'URL → graphe historique multi-étapes (DNS/TCP/TLS/TTFB/Total).
- Voir d'un coup d'œil quelle étape dérive avec le temps.

## Faire tourner ça depuis Kubernetes

Si ton problème S3 vient de tes pods (et pas de ton poste), il faut probe **depuis l'intérieur du cluster**. Voir [docs/kubernetes.md](kubernetes.md) — il y a un Dockerfile et des manifests prêts à l'emploi.

Résumé :
```bash
docker build -t registry.example.com/infra/mtrgraph:0.1.0 .
docker push registry.example.com/infra/mtrgraph:0.1.0
# adapter k8s/configmap.yaml (TARGET_URL) et k8s/kustomization.yaml (image)
kubectl apply -k k8s/
kubectl -n mtrgraph logs -l role=http-daemon -f
```

Tu auras alors un Deployment qui probe S3 toutes les minutes depuis le cluster, avec alerte vs baseline, et une UI web accessible via `kubectl port-forward`.

## Aller plus loin avec des credentials

`mtrgraph http` ne fait que des requêtes anonymes (HEAD/GET sur l'endpoint racine). Pour mesurer les **vraies** opérations qui rament (LIST, GET d'un gros objet, PUT), il faut signer en SigV4 avec des credentials. C'est exactement ce que fait `mtrgraph s3-*` :

```bash
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
mtrgraph s3-list   --endpoint https://$S3_HOST --region eu-west-3 --bucket mon-bucket --prefix "logs/"
mtrgraph s3-head   --endpoint https://$S3_HOST --region eu-west-3 --bucket mon-bucket --key path/to/obj
mtrgraph s3-get    --endpoint https://$S3_HOST --region eu-west-3 --bucket mon-bucket --key path/to/obj
mtrgraph s3-put    --endpoint https://$S3_HOST --region eu-west-3 --bucket mon-bucket --key probe-$(date +%s) --size-kb 100
mtrgraph s3-delete --endpoint https://$S3_HOST --region eu-west-3 --bucket mon-bucket --key probe-1234567890
```

Ou via l'**interface web** dédiée (recommandée pour le diag interactif) :
```bash
mtrgraph web   # puis http://127.0.0.1:8765/s3
```

Doc dédiée : [s3-testing.md](s3-testing.md).

## Limites de `mtrgraph http`

- **HEAD par défaut** — pour mesurer un GET réel, `--method GET`. Attention, GET d'un gros objet inclut le transfert dans `total_ms`.
- **HTTP/1.1 forcé** (stdlib) — pas d'HTTP/2. Pour S3 ça reste représentatif des SDK aws-sdk-python en mode classique.
- **Pas de keep-alive** — chaque sample paie DNS+TCP+TLS. C'est volontaire pour mesurer le coût d'un nouveau client (Lambda cold start, microservice qui ouvre/ferme à chaque appel). Si ton vrai code utilise un pool persistent, retire mentalement TCP+TLS de chaque mesure.
- **Pas de signature AWS** — donc on tape `s3.eu-west-3.amazonaws.com/` ou un objet public. Pour une requête authentifiée, mesure côté SDK avec hooks/middleware.

## Workflow complet recommandé

1. **Doctor** : `mtrgraph doctor` — environnement OK ?
2. **Réseau** : `mtrgraph run $S3_IP --proto tcp --port 443 -c 30`
3. **App** : `mtrgraph http https://$S3_HOST/ -c 30`
4. **Compare** au baseline si tu en as un.
5. **Décision** :
   - Hop final mtr > 50 ms → ouvrir un ticket réseau (FAI, transit).
   - TLS > 300 ms → debug TLS local (OpenSSL, certif, config).
   - TTFB > 1000 ms → ouvrir un ticket AWS, vérifier la région du bucket.
   - Tout OK → c'est le code applicatif.

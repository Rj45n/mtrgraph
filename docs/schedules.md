# Schedules — tests automatiques en mode serveur

Le scheduler intégré au web (`mtrgraph web`) exécute des tests périodiques en arrière-plan. Tu configures tout via l'UI `/schedules`, les résultats vont dans les mêmes tables que les tests manuels (donc visibles dans `/`, `/http`, `/s3`).

3 types de tests planifiables :
- **MTR** (chemin réseau) — avec comparaison auto à la baseline
- **HTTP** (DNS / TCP / TLS / TTFB sur une URL)
- **S3** (LIST / HEAD / GET / PUT / DELETE en SigV4)

2 modes d'intervalle :
- **Fixe** : toutes les N secondes
- **Aléatoire** : intervalle tiré uniformément entre min et max (utile pour ne pas synchroniser plusieurs sondes, ou pour simuler une charge non-corrélée)

## Accéder à la page

```bash
mtrgraph web
# → http://127.0.0.1:8765/schedules
```

Le scheduler démarre automatiquement avec le serveur (thread daemon Python, tick chaque seconde).

## Créer un schedule

Le formulaire de droite contient 3 fieldsets selon le type choisi.

### MTR

| Champ                       | Rôle                                                                  |
|-----------------------------|-----------------------------------------------------------------------|
| Cible                       | IP ou hostname. Ignoré si pool aléatoire est rempli.                  |
| Pool de cibles aléatoires   | Une cible par ligne. Une au hasard est tirée à chaque run.            |
| Protocole                   | `icmp` (CAP_NET_RAW), `udp` (sans privilège), `tcp` (CAP_NET_RAW).    |
| Port                        | Pour UDP/TCP — caché si ICMP.                                         |
| Durée du run (s)            | ≈ nb de cycles mtr (1 cycle/seconde). 30 s = bon compromis.           |
| Comparaison auto            | Si coché, compare à la baseline (médiane par hop sur les N derniers). |
| Taille baseline (N)         | 10 par défaut. Min 2.                                                 |

**Statuts produits** :
- `ok:dst_avg=Xms` (pas de comparaison) ou `ok:vs-baseline`
- `warning:hop3 avg 50→120ms` (baseline dépassée non critique)
- `critical:hop2 avg 5→500ms` (dégradation critique)
- `err:<msg>` (exception)

Exemple typique : monitoring d'une route VPN avec **pool de cibles** (datacenter, backup, fallback) → chaque run pique une cible au hasard, et chacune a sa propre baseline. Tu vois directement laquelle dérive.

### HTTP

| Champ            | Rôle                                          |
|------------------|-----------------------------------------------|
| URL              | URL complète https://…                        |
| Méthode          | HEAD ou GET                                   |
| Samples par run  | Nombre d'échantillons (chaque run = N probes) |
| IP forcée        | Optionnel : DNS bypass, garde SNI/Host        |

Statuts : `ok:200:5` (5 samples 200 OK), `err:2/5` (2 erreurs sur 5), etc.

### S3 — mode random_ops (🎲 opérations aléatoires)

Coche la case **"Opérations aléatoires"** pour que le scheduler alterne LIST / HEAD / GET / PUT / DELETE à chaque tick. Trois champs apparaissent :

| Champ | Rôle | Défaut |
|-------|------|--------|
| **Prefix** (obligatoire) | Toutes les opérations sont scopées sous ce prefix | `mtrgraph-bench/` |
| **Taille objets PUT** | En Kio, pour les uploads | 10 |
| **Pool min / max** | En dessous de min → PUT prioritaire. Au-dessus de max → DELETE prioritaire | 5 / 100 |

**Sécurité — garanti** :
- Toutes les clés PUT sont enregistrées dans la table `s3_tracked_objects` au moment du PUT.
- DELETE ne pioche **que** dans `s3_tracked_objects` — donc on ne supprime **jamais** un objet qu'on n'a pas créé.
- HEAD/GET pioche aussi uniquement parmi nos objets tracés.
- LIST est scopé au prefix → ne liste pas les données utilisateur du bucket.

**Bouton 🧹 Purger** sur chaque carte de schedule random_ops : supprime tous les objets `s3_tracked_objects` encore vivants pour ce schedule, sur le bucket distant. Le schedule continue de tourner après.

**À la suppression du schedule** : si des objets sont encore tracés, l'UI demande "Supprimer aussi les N objets restants du bucket ?". Tu choisis.

### S3 — mode opération unique

| Champ                       | Rôle                                                     |
|-----------------------------|----------------------------------------------------------|
| Endpoint URL                | https://endpoint sans le bucket                          |
| Région, Access/Secret Key   | Credentials AWS SigV4                                    |
| Bucket                      | Nom du bucket                                            |
| Opération (onglet)          | LIST / HEAD / GET / PUT / DELETE                         |
| Key                         | Path de l'objet. Pour PUT, `{ts}` est remplacé par timestamp |
| Pool de keys aléatoires     | HEAD/GET/DELETE : pioche une key au hasard à chaque run  |
| Prefix, Max keys            | LIST uniquement                                          |
| Body (Kio)                  | PUT : taille de données aléatoires à uploader            |
| Comparaison auto baseline   | Compare aux N derniers runs (même endpoint+op+bucket) et alerte si dégradation |
| Taille baseline             | N par défaut 10                                          |

Statuts produits :
- `ok:200`, `ok:vs-baseline(N)`
- `warning:ttfb 100→800ms` (étape dégradée, ratio ≥ 1.5)
- `critical:total 200→2500ms` (ratio ≥ 3)
- `http:403`, `http:503` (erreur côté serveur — alerte aussi via webhook)
- `err:dns:...`, `err:tls:...`, etc.

Seuils minimum d'écart (pour éviter le bruit sur valeurs faibles) :
DNS/TCP ≥ 20 ms, TLS ≥ 30 ms, TTFB ≥ 50 ms, Total ≥ 100 ms.

## Planification

### Mode fixe
Intervalle en secondes. Min 5 s. Au-delà de quelques jours, préférer un cron externe.

### Mode aléatoire
Min/Max en secondes. Le prochain délai est tiré uniformément après chaque run. Utile pour :
- Désynchroniser plusieurs schedules (éviter les pics de charge corrélés)
- Tester en condition "non périodique" (simuler du trafic utilisateur)
- Capturer des problèmes qui se produisent sous load variable

## Webhook (alerte externe)

Chaque schedule peut avoir un `webhook_url`. Quand un run produit un statut **non-OK** (`warning:`, `critical:`, `err:*`, `http:4xx/5xx`), le scheduler POSTe un JSON :

```json
{
  "text": "🔴 *mtrgraph* schedule [`mon-schedule`] (#3, kind=s3) → `critical:ttfb 100→800ms` (run_id=42)",
  "schedule_id": 3,
  "schedule_name": "mon-schedule",
  "kind": "s3",
  "status": "critical:ttfb 100→800ms",
  "severity": "critical",
  "run_id": 42,
  "timestamp": "2026-05-26T15:34:21+00:00"
}
```

Le champ `text` est directement compatible **Slack** (formatage markdown). Pour Teams ou autre, le payload générique reste utilisable côté receveur.

- L'appel a un timeout de 5 s.
- Une erreur webhook est loguée mais ne fait jamais échouer le run lui-même.
- Test rapide : `nc -l 9999` puis configurer `http://localhost:9999/` comme webhook → tu vois le POST en direct.

## Actions sur un schedule

Chaque carte expose 4 boutons :
- **▶ Run now** — exécute immédiatement, hors du tick normal.
- **⏸ Désactiver / ▶ Activer** — toggle. Désactivé = exclu du scheduler mais conservé en DB.
- **✎ Éditer** — re-remplit le formulaire pour modifier.
- **🗑 Supprimer** — supprime le schedule (les runs déjà produits restent).

Couleurs des cartes : vert (dernier run OK), rouge (erreur ou status HTTP 5xx), gris (désactivé).

## Sécurité

⚠ Les schedules stockent leurs credentials **en clair** dans la DB (nécessaire pour l'exécution automatique sans interaction). Conséquences :

- Ne pas exposer le serveur web sans authentification devant.
- Utiliser des credentials dédiés au monitoring (read-only quand possible), avec scope limité.
- Pour MinIO/S3-compat, créer un user dédié `monitoring` avec une policy stricte (read-only ou un seul bucket).
- En K8s, monter le PVC en `mode 0600`, et restreindre l'accès au pod.

## Cas d'usage typiques

### Monitoring continu d'un S3 (LIST scheduling)
```
Nom: prod-list-toutes-les-2min
Type: S3 - LIST
Endpoint, bucket, creds : ceux de ta prod
Mode: fixe, 120 s
```
→ détecte les dégradations LIST persistantes ou les 503 SlowDown intermittents.

### Test S3 aléatoire sur un pool d'objets
```
Nom: random-get-pool
Type: S3 - GET
Key: (vide)
Pool de keys: 50 keys représentatives de la prod
Mode: aléatoire 30-90 s
```
→ chaque run pioche une key, alterne les caches CDN, révèle des cold-paths.

### Surveillance multi-cibles MTR (load-balanced)
```
Nom: vpn-multi-pop
Type: MTR
Pool de cibles: vpn-pop1.example.com, vpn-pop2.example.com, vpn-pop3.example.com
Proto: TCP, port 443
Durée: 30 s
Comparaison auto: oui, baseline 10
Mode: aléatoire 60-180 s
```
→ surveille 3 POPs avec une baseline par POP. Si l'un dérive, alerte spécifique.

### Test HTTP intensif avec IP forcée
```
Nom: backend-pod-A
Type: HTTP
URL: https://api.internal/health
IP forcée: 10.0.1.42  ← un pod précis
Mode: fixe 30 s
```
→ teste un pod par-derrière le service, à comparer avec d'autres schedules sur d'autres pods.

## API REST

```bash
# Lister
curl http://localhost:8765/api/schedules

# Créer
curl -X POST http://localhost:8765/api/schedules \
  -H "content-type: application/json" \
  -d '{
    "name":"mtr-vpn",
    "kind":"mtr",
    "config":{"target":"vpn.example.com","proto":"tcp","port":443,"cycles":30,"auto_compare":true,"baseline_n":10},
    "schedule_mode":"fixed",
    "interval_s":120,
    "enabled":true
  }'

# Toggle
curl -X POST http://localhost:8765/api/schedules/1/toggle

# Run now (one-shot, hors du tick)
curl -X POST http://localhost:8765/api/schedules/1/run-now

# Éditer
curl -X PUT http://localhost:8765/api/schedules/1 -H "content-type: application/json" -d '{...}'

# Supprimer
curl -X DELETE http://localhost:8765/api/schedules/1
```

## Déploiement K8s

Le scheduler tourne dans le pod `mtrgraph-web`. Pour persister les schedules entre redéploiements, la PVC est partagée — déjà OK dans le manifest fourni ([k8s/deployment-web.yaml](../k8s/deployment-web.yaml)).

Sans configuration spéciale dans `kustomization.yaml`, un déploiement K8s te donne **immédiatement** un scheduler accessible via `/schedules` après `kubectl port-forward svc/mtrgraph-web 8765:80`.

## Limites

- **Pas de retry automatique** en cas d'erreur — le prochain tick refera l'essai.
- **SQLite mono-writer** : le scheduler et le web sharent la DB. Sous très forte charge (> 100 schedules avec intervalles < 5 s), envisager Postgres (roadmap).
- **Pas d'alerte externe** (webhook, email) — les statuts dégradés sont visibles dans l'UI uniquement. À cron-ner un grep sur les logs si besoin (`docker logs mtrgraph | grep -E "warning|critical|err:"`).
- **Pas de versioning des configs** — éditer écrase l'ancienne config. Faire un snapshot via l'API si tu veux garder l'historique.

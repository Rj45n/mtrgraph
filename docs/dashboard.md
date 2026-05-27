# Dashboard — vision unifiée par endpoint

Page `/dashboard` qui réunit MTR + HTTP/S3 sur une seule timeline pour analyser un endpoint en un coup d'œil.

```bash
mtrgraph web    # http://127.0.0.1:8765/dashboard
```

## Filtres

- **Endpoint** (obligatoire) — choisis dans la liste des endpoints déjà testés
- **Bucket** (optionnel)
- **Opération** (optionnel — LIST / HEAD / GET / PUT / DELETE)
- **Limite runs** — combien de derniers runs analyser (défaut 200)

Auto-refresh toutes les 15 s.

## KPI tuiles

| Tuile | Sens |
|-------|------|
| Runs | Nombre de runs S3 retenus pour le filtre |
| Erreurs % | Taux d'erreur (HTTP 4xx/5xx ou erreur réseau). Vert < 1, jaune 1-10, rouge ≥ 10 |
| Avg Total | Latence moyenne end-to-end |
| P50 / P95 / P99 Total | Percentiles — P95 ≥ 800 ms → jaune, ≥ 2500 ms → rouge |
| Avg / P95 TTFB | TTFB seul (sans DNS/TCP/TLS) |

## Charts

### 1. Étapes HTTP/S3 empilées
DNS + TCP + TLS + TTFB sur la même courbe stack-area. Si l'une dérive, on voit instantanément laquelle.

### 2. Réseau (RTT MTR) vs TTFB applicatif
- **TTFB** (jaune) = ce que voit le client S3
- **RTT** (cyan tireté) = ce que mesure le dernier hop MTR vers la **même IP**, dans la dernière heure

Si TTFB suit la même tendance que RTT → c'est le réseau. Si TTFB monte mais RTT reste stable → c'est le serveur S3.

### 3. Server processing time ≈ TTFB − RTT
Différence entre les deux ci-dessus. C'est le temps passé **côté serveur S3**, hors latence réseau. Permet d'isoler "S3 rame" vs "le réseau rame".

→ Requiert qu'il y ait au moins **un schedule MTR actif vers la même IP** dans l'heure précédente. Sinon `null`. Conseil : crée un schedule MTR sur l'IP du frontal S3, intervalle 60 s.

### 4. Throughput (MB/s)
Pour les ops GET et PUT uniquement (LIST/HEAD/DELETE ont peu de données utiles). Calculé comme `bytes_transferred / duration_ms`. Permet de voir si le débit s'effondre lors d'un pic.

→ Pour des mesures de débit **réel** (concurrent), utilise `mtrgraph s3-bench` (voir [s3-bench.md](s3-bench.md)).

### 5. Code HTTP / erreurs
Barres colorées par status :
- Vert = 2xx OK
- Cyan = 3xx redirect
- Jaune = 4xx client (signature, perms, etc.)
- Rouge = 5xx server ou erreur réseau

Les pics rouges sautent aux yeux.

## Workflow recommandé pour diagnostic

1. **Crée un schedule MTR** sur l'IP de ton endpoint S3 (proto TCP, port 443, durée 30 s, auto_compare on, intervalle 60 s).
2. **Crée un schedule S3 random_ops** sur ton bucket (intervalle 30-90 s aléatoire).
3. Laisse tourner 30 min à plusieurs heures.
4. Ouvre `/dashboard`, sélectionne ton endpoint.
5. Lis dans l'ordre :
   - **KPI** : tendance générale (erreurs, P95)
   - **Chart 1** : quelle étape dérive ?
   - **Chart 2** : est-ce le réseau ?
   - **Chart 3** : quel temps purement côté serveur ?
   - **Chart 4** : le débit suit ?
   - **Chart 5** : des codes anormaux ?

Conclusion type : "À 14h32, hausse de P95 → Chart 2 montre RTT stable mais TTFB qui double → Chart 3 confirme +500 ms côté serveur → Chart 5 montre 3 codes 503 → c'est du throttling S3."

## Limites

- Le **server processing** est une **estimation**, pas une mesure exacte. La RTT MTR est une moyenne d'aller-retour qui peut inclure des hops control-plane lents (voir [reading-mtr.md](reading-mtr.md)). Utiliser pour les tendances, pas pour des chiffres précis.
- Le **throughput** sur la base de runs unitaires n'est pas un vrai débit. Pour mesurer le débit réel concurrent, voir [s3-bench.md](s3-bench.md).
- Pas de **time range explicite** pour l'instant — utilise `Limite runs` (chaque run a son horodatage). À ajouter : sélecteur "last 1h / 6h / 24h".

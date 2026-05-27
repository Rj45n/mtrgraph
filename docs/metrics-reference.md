# Référence des métriques

Toutes les métriques produites par mtrgraph, leur source, leur signification, et comment les interpréter pour le diagnostic.

📑 [MTR (réseau)](#mtr--métriques-réseau) · [HTTP (probes)](#http--métriques-applicatives) · [S3 (opérations authentifiées)](#s3--métriques-doperations) · [TCP (kernel)](#tcp--métriques-kernel) · [KPIs dashboard (dérivés)](#kpis-dashboard--métriques-dérivées) · [Sévérités & alertes](#sévérités--seuils-dalerte) · [Couleurs](#couleurs)

---

## MTR — métriques réseau

Source : binaire `mtr -j` (subprocess). Chaque run produit 1 ligne dans `runs` + N lignes dans `hops` (1 par hop).

### Au niveau du run (`runs`)
| Métrique | Sens | Comment l'interpréter |
|----------|------|----------------------|
| `target` | IP ou hostname cible | Identifie le chemin mesuré |
| `protocol` | `icmp` / `udp` / `tcp` | Protocole utilisé pour les probes. ICMP est "classique ping", UDP traverse moins de firewalls, TCP SYN simule un vrai client TCP |
| `dst_port` | Port destination (UDP/TCP) | NULL pour ICMP. 443 pour TCP HTTPS, 33434+ par défaut UDP |
| `cycles` | Nombre de cycles mtr | Plus de cycles = stats plus stables mais run plus long |
| `started_at` / `finished_at` | Horodatages ISO-8601 UTC | Pour aligner avec d'autres événements |
| `src` | Hostname de la machine qui a lancé mtr | Utile en multi-source pour distinguer |

### Par hop (`hops`, 1 ligne par hop_index)
| Métrique | Sens | Comment l'interpréter |
|----------|------|----------------------|
| `hop_index` | Position du hop (1 = premier routeur, N = destination) | Avec TTL=hop_index, mtr remonte un saut à la fois |
| `host` | Hostname ou IP du routeur | `???` quand le routeur ne répond pas aux TTL-exceeded (cf [reading-mtr.md](reading-mtr.md)) |
| `loss_pct` | % de paquets perdus pour ce hop | **⚠ piège** : élevé ne veut PAS forcément dire perte sur le chemin. Cf rate-limiting control-plane dans [reading-mtr.md](reading-mtr.md) |
| `sent` | Nombre de paquets envoyés | Égal à `cycles` du run |
| `last_ms` | Dernier RTT mesuré | Plus volatile que `avg` |
| `avg_ms` | RTT moyen | La métrique de référence pour comparer les hops dans le temps |
| `best_ms` / `worst_ms` | Min/max RTT | `worst_ms` >> `avg_ms` = jitter important |
| `stddev_ms` | Écart-type du RTT | **Jitter**. Plus c'est haut, plus l'instabilité est forte (VoIP, gaming, en pâtissent) |

### Métriques dérivées (computed)

| Métrique | Calcul | Où c'est exposé |
|----------|--------|----------------|
| `baseline_hops` | Médiane de `avg_ms` et `loss_pct` par hop sur les N derniers runs (même target+proto+port) | `compare --baseline`, scheduler `auto_compare` |
| `latest_mtr_rtt_for_ip(ip)` | RTT au dernier hop du dernier MTR vers `ip` dans la dernière heure | Alimente `network_rtt_ms` du dashboard |
| `hops_count` (par run) | Compte des hops ayant répondu (loss < 100%) | Page MTR target + route stability |
| `total_hop_count_changes` | Nombre de fois où `hops_count` a changé entre runs successifs | `/api/dashboard/route-stability`, indique du route flapping |

---

## HTTP — métriques applicatives

Source : module `http_probe.py` (pur stdlib `socket` + `ssl`). 1 ligne dans `http_runs` par exécution, N lignes dans `http_samples`.

### Par sample (`http_samples`)
Chaque sample = une connexion fraîche (pas de keep-alive — mesure le cold-start, représentatif d'un client court type Lambda).

| Métrique | Sens | Comment l'interpréter |
|----------|------|----------------------|
| `dns_ms` | Temps de résolution DNS (`getaddrinfo`) | **<20 ms** OK, **>100 ms** résolveur lent ou cold cache, **>300 ms** vraiment cassé |
| `tcp_ms` | Temps de `connect()` TCP | ≈ RTT vers l'IP. **<20 ms** local/datacenter, **20-100 ms** internet OK, **>200 ms** suspect |
| `tls_ms` | Temps de handshake TLS (`wrap_socket`) | **<50 ms** TLS 1.3 nominal, **50-200 ms** TLS 1.2 ou cold cipher, **>500 ms** OCSP lent ou problème certif |
| `ttfb_ms` | Time-To-First-Byte (envoi de la requête → premier octet reçu) | **<100 ms** rapide, **>1 s** serveur lent (CPU saturé, requête lourde, throttling) |
| `total_ms` | Durée totale de l'échantillon (DNS + TCP + TLS + TTFB + close) | C'est ce que voit ton vrai client |
| `status` | Code HTTP retourné | 2xx OK, 3xx redirect, 4xx client, 5xx serveur |
| `error` | Message d'erreur si l'échantillon a échoué | Préfixé par `dns:` / `tcp:` / `tls:` / `http:` pour catégoriser |
| `resolved_ip` | IP retournée par DNS | Utile pour spotter les round-robins ou les IPs lentes individuellement |

### Par run (`http_runs`)
| Métrique | Sens |
|----------|------|
| `url` / `method` | Cible et verbe HTTP (HEAD ou GET) |
| `samples` | Nombre d'échantillons réalisés |
| `status_summary` | Compteur des codes HTTP : `"200:28,503:2"` — permet de spotter du throttling intermittent |
| `errors` | Nombre d'échantillons en erreur |
| `resolved_ip` | IP du premier échantillon réussi (pour corrélation MTR) |

### Métriques dérivées
| Métrique | Calcul |
|----------|--------|
| `http_baseline` | Médiane par étape (dns/tcp/tls/ttfb/total) sur les N derniers runs réussis de la même URL |
| Agrégat par run (CLI/UI) | avg / best / worst / stddev par étape calculés depuis les samples |

---

## S3 — métriques d'opérations

Source : module `s3_client.py` (SigV4 stdlib). 1 ligne dans `s3_runs` par opération.

| Métrique | Sens | Notes |
|----------|------|-------|
| `endpoint` | URL S3 (sans bucket) | AWS / MinIO / Scaleway / OVH / Cellar / Free Pro / etc. |
| `bucket` / `key` | Cible | Pour LIST : `key` est NULL, `bucket` est le scope |
| `operation` | `list` / `head` / `get` / `put` / `delete` | |
| `http_status` | Code HTTP retourné par l'endpoint | 200 OK, 204 OK (DELETE), 403 InvalidAccessKey/SignatureDoesNotMatch, 503 SlowDown (throttling) |
| `duration_ms` | Temps total (DNS + TCP + TLS + TTFB + body transfer + close) | C'est ce que ton SDK S3 mesurerait |
| `dns_ms` / `tcp_ms` / `tls_ms` / `ttfb_ms` | Idem HTTP, mêmes seuils | |
| `bytes_transferred` | Octets transférés (response body pour GET, request body pour PUT) | Utilisé pour calculer le throughput |
| `resolved_ip` | IP utilisée | Permet la vue par-IP (round-robin S3 → pour spotter qu'une IP rame) |
| `response_summary` | Résumé textuel parsé de la réponse | LIST : `"42 keys (truncated) — key1, key2, …"`, HEAD : `"size=1024 · etag=abc"`, erreur : `"SlowDown: Please reduce your request rate"` |
| `error` | Erreur réseau/SigV4 si la requête n'a même pas abouti | Préfixé `dns:` / `tcp:` / `tls:` / `http:` / `sign:` / `endpoint:` / `config:` |

### Métriques dérivées
| Métrique | Calcul |
|----------|--------|
| `throughput_mbps` | `bytes_transferred / (duration_ms/1000) / (1024*1024)` (pour GET/PUT avec bytes>0) |
| `server_processing_ms` | `ttfb_ms - latest_mtr_rtt_for_ip(resolved_ip)` — estimation du temps purement passé côté S3, hors latence réseau |
| `transfer_ms` (GET only) | `duration_ms - ttfb_ms` — temps de download du body après le TTFB |
| `s3_baseline` | Médiane par étape sur les N derniers runs réussis (même endpoint+op+bucket) |
| Per-IP stats | count, err_pct, avg dns/tcp/tls/ttfb/total agrégés par `resolved_ip` |

---

## TCP — métriques kernel

Source : `tcp_stats.py` parse `/proc/net/snmp` (compteurs TCP MIB). 1 ligne dans `tcp_samples` par échantillon.

> ⚠ En container sans `hostNetwork`, mesure le namespace réseau **du pod**, pas l'host.

### Snapshot brut (depuis le boot)
Stocké en interne, jamais affiché directement.

| Compteur kernel | Sens |
|-----------------|------|
| `OutSegs` | Nombre total de segments TCP émis depuis le boot |
| `InSegs` | Nombre total de segments TCP reçus |
| `RetransSegs` | Nombre de segments TCP retransmis (perte détectée) |
| `InErrs` | Segments rejetés (bad checksum, etc.) |
| `ActiveOpens` | Appels `connect()` qui ont réussi |
| `EstabResets` | Connexions reset alors qu'elles étaient ESTABLISHED (RST reçu) |

### Delta par échantillon (`tcp_samples`)
Calculé entre 2 snapshots espacés de `duration_s` secondes.

| Métrique | Sens | Interprétation |
|----------|------|----------------|
| `duration_s` | Fenêtre du sample (typiquement 5s) | |
| `out_per_s` | **OutSegs/s** — segments émis par seconde | Volume du trafic TCP sortant. Pas alarmant en soi |
| `in_per_s` | InSegs/s — segments reçus par seconde | Volume entrant |
| `retrans_per_s` | RetransSegs/s — retransmissions par seconde | À mettre en proportion avec `out_per_s` |
| **`retrans_pct`** | `100 × retrans_segs_delta / out_segs_delta` | **La métrique clé.** Pourcentage de paquets qui ont dû être retransmis. **<0.1% nominal**, **0.1-1% suspect**, **>1% problème actif** |
| `in_errs_delta` | Nouveaux InErrs sur la fenêtre | Bad checksum, etc. Normalement 0 |
| `estab_resets_delta` | RST sur connexions établies | Connexions cassées brutalement (firewall agressif, peer crash, etc.) |
| `active_opens_delta` | Nouveaux `connect()` réussis | Mesure le taux de nouvelles connexions |

### Pourquoi afficher OutSegs/s à côté de retrans%

Réponse à la question fréquente :
- Si `retrans%` grimpe à 1% **mais que `out/s` a doublé** → la perte absolue est proportionnelle au trafic, **pas alarmant**
- Si `retrans%` grimpe à 1% **à `out/s` constant** → **vraie dégradation**, à investiguer
- Si `out/s` s'effondre → ton trafic réseau a chuté. Normal (heures creuses) ou anormal (quelque chose ne sort plus) ?

---

## KPIs dashboard — métriques dérivées

Source : module `kpis.py` (pures fonctions sur les rows). Calculées à la volée à chaque hit de `/api/dashboard/kpis`.

| KPI | Calcul | Interprétation |
|-----|--------|----------------|
| `count` | Nombre de runs dans la fenêtre filtrée | |
| `err_pct` | `100 × err_count / count` | <1% nominal, 1-10% suspect, ≥10% rouge |
| `avg_total_ms` | Moyenne arithmétique de `duration_ms` | Lissée par les outliers |
| `p50_total_ms` | Médiane | "Run médian" |
| `p95_total_ms` | 95<sup>e</sup> percentile | "5% des runs sont au-dessus". **La métrique SRE de référence pour l'UX** |
| `p99_total_ms` | 99<sup>e</sup> percentile | Pire 1% |
| `avg_ttfb_ms` / `p95_ttfb_ms` | Idem mais pour `ttfb_ms` uniquement (sans transfert) | Plus représentatif de la latence serveur |
| **`apdex`** | `(satisfied + tolerating/2) / total` avec `satisfied: ≤T`, `tolerating: T..4T`, défaut T=500ms | Score unique 0-1 de l'expérience utilisateur. ≥0.94 excellent · 0.85-0.93 bon · 0.70-0.84 moyen · <0.70 mauvais |
| **`jitter_ttfb_ms`** | Stddev de population sur `ttfb_ms` (σ) | Plus c'est haut, plus la variabilité est forte. <50 ms OK, >200 ms gênant pour l'UX |
| `jitter_total_ms` | Idem pour `duration_ms` | |
| **`failure_modes`** | `{ok: N, http_4xx: N, http_5xx: N, dns_error: N, tcp_error: N, tls_error: N, http_error: N, other_error: N}` | Distribution par catégorie d'échec. Sépare réseau (dns/tcp/tls) vs application (4xx) vs serveur (5xx) |
| **`trend_24h`** | `{current, past, delta_pct, direction}` comparant la fenêtre courante à la même fenêtre 24h plus tôt | `up` (>+5%), `down` (<-5%), `flat`, `unknown` (pas assez de data) |
| `trend_7d` | Idem mais vs J-7 | Tendance hebdomadaire |
| **`burst`** | `{count, first_at, last_at, window_s}` du pire burst ≥5 erreurs en ≤60s | Détecte les rafales d'erreurs. NULL si jamais de burst |
| **`mttr`** | Mean Time To Recovery : moyenne du temps écoulé entre un passage en dégradation (err_rate ≥20%) et le retour (err_rate ≤5%), sur fenêtre glissante 10 runs | `events`: nb de dégradations, `avg_recovery_s`, `max_recovery_s`, `ongoing` (true si actuellement dégradé) |
| `ips` | Liste des IPs distinctes vues dans la fenêtre | Round-robin DNS visible |
| `ops_distribution` | `{op: count}` | Sur S3, montre quelle proportion de chaque op (LIST/GET/PUT/…) |

### Heatmap jour × heure (`/api/dashboard/heatmap`)
Matrice 7×24 : moyenne de `duration_ms` (ou `ttfb_ms`) par jour de semaine × heure. Permet de spotter "ça rame le lundi à 14h" instantanément.

### Route stability (`/api/dashboard/route-stability`)
Liste des changements de `hops_count` entre runs successifs d'un même target. Révèle le route flapping (chemin instable côté opérateur, BGP).

---

## Sévérités & seuils d'alerte

Utilisés par le scheduler pour passer un statut en `warning` / `critical` et déclencher un webhook.

### MTR
| Sévérité | Critère (vs baseline) |
|----------|----------------------|
| `warning` | `d_loss >= 3 pt` OU (`d_avg ≥ +50%` ET `d_avg ≥ 20ms`) |
| `critical` | `d_loss ≥ 10 pt` |

### S3 (auto_compare)
Par étape, avec ratio + delta minimum (anti-bruit) :
| Étape | warn ratio | crit ratio | min delta |
|-------|-----------|-----------|-----------|
| DNS / TCP | 1.5× | 3× | 20 ms |
| TLS | 1.5× | 3× | 30 ms |
| TTFB | 1.5× | 3× | 50 ms |
| Total | 1.5× | 3× | 100 ms |

### HTTP daemon
Idem S3 + alerte si `errors / samples × 100 ≥ error_threshold_pct` (défaut 10%).

### TCP
| Sévérité | Critère |
|----------|---------|
| `warning` | `retrans_pct ≥ 0.1%` |
| `critical` | `retrans_pct ≥ 1.0%` |

### Webhook payload
```json
{
  "text": "🔴 *mtrgraph* schedule [`name`] (#3, kind=s3) → `critical:ttfb 100→800ms` (run_id=42)",
  "schedule_id": 3, "schedule_name": "name", "kind": "s3",
  "status": "critical:ttfb 100→800ms",
  "severity": "critical",
  "run_id": 42,
  "timestamp": "2026-05-27T15:34:21+00:00"
}
```

---

## Couleurs

Seuils unifiés CLI (Rich) ↔ web (CSS). Source : [mtrgraph/colors.py](../mtrgraph/colors.py).

### Loss %
| <1% | 1-5% | 5-10% | ≥10% |
|-----|------|-------|------|
| vert | jaune | rouge | rouge gras |

### Latence MTR (avg_ms)
| <50 ms | 50-100 | 100-200 | ≥200 |
|--------|--------|---------|------|
| vert | jaune | rouge | rouge gras |

### Jitter MTR (stddev_ms)
| <5 ms | 5-20 | 20-50 | ≥50 |
|-------|------|-------|-----|
| vert | jaune | rouge | rouge gras |

### Étapes HTTP/S3 (ms)
| Étape | vert | jaune | rouge | rouge gras |
|-------|------|-------|-------|-----------|
| DNS | <20 | 20-100 | 100-300 | ≥300 |
| TCP | <20 | 20-100 | 100-300 | ≥300 |
| TLS | <50 | 50-200 | 200-500 | ≥500 |
| TTFB | <100 | 100-500 | 500-1500 | ≥1500 |
| Total | <200 | 200-800 | 800-2500 | ≥2500 |

### Status HTTP
| 2xx | 3xx | 4xx | 5xx / err |
|-----|-----|-----|----------|
| vert | cyan | jaune | rouge gras |

---

## Pour aller plus loin

- [reading-mtr.md](reading-mtr.md) — comment lire un résultat MTR sans se faire avoir (pièges courants : faux positifs sur les hops intermédiaires)
- [cookbook-s3.md](cookbook-s3.md) — workflow de diagnostic S3 (recommandations selon quelle étape rame)
- [dashboard.md](dashboard.md) — comment utiliser le dashboard unifié
- [troubleshooting.md](troubleshooting.md) — symptôme → cause → fix

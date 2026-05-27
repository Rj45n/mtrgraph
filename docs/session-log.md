# Journal de session

Notes laissées pour reprendre le projet plus tard. Une entrée par session de travail.

---

## 2026-05-26 — création initiale

**Décision utilisateur** : stack Python (Rich/Textual + FastAPI), usage mixte daemon + ad-hoc.

**Livré** :
- Structure de paquet `mtrgraph/` (cli, probe, db, tui, colors, compare, daemon, web, templates).
- Schéma SQLite `runs` + `hops` dans `~/.local/share/mtrgraph/mtrgraph.db`.
- Sous-commandes CLI : `run`, `list`, `show`, `compare` (avec `--baseline`), `daemon`, `web`, `delete`.
- Web FastAPI : `/`, `/run/{id}`, `/compare`, API `/api/target/.../history`, `/api/run/{id}`.
- Templates Jinja2 dark + Chart.js via CDN.
- Couleurs partagées CLI/web via `colors.py` (seuils Loss / Latence / Jitter).
- Docs `docs/architecture.md`, `db-schema.md`, `commands.md`, `decisions.md`, `web.md`, `roadmap.md`.

**Choix par défaut posés (à challenger plus tard si besoin)** :
- Rich seul (pas Textual) pour le MVP — voir [decisions.md](decisions.md).
- Baseline = médiane sur les N derniers runs (N=10) — robuste aux outliers.
- Seuils en dur dans `colors.py` / `compare.py`.
- DB unique locale, pas d'auth web (écoute 127.0.0.1).

**Smoke-tests à faire après `pip install -r requirements.txt`** :
```bash
python3 -m mtrgraph.cli run 1.1.1.1 -c 5 --label smoke
python3 -m mtrgraph.cli list
python3 -m mtrgraph.cli show 1
python3 -m mtrgraph.cli run 1.1.1.1 -c 5 --label smoke2
python3 -m mtrgraph.cli compare 1 2
python3 -m mtrgraph.cli web   # vérifier http://127.0.0.1:8765
```

**Points d'attention pour la suite** :
- Pas de migrations DB : si on ajoute des colonnes, prévoir un `ALTER TABLE` idempotent dans `init_db`.
- Le daemon écrit sur stdout en boucle → systemd unit + journald si on veut le faire tourner en arrière-plan propre.
- Si `mtr -j` ne sort rien sur certaines distros : vérifier la version (`mtr --version` ≥ 0.86).
- **Starlette ≥ 1.0** exige `TemplateResponse(request, name, ctx)` (pas `(name, {"request": request, ...})`). Bug rencontré et corrigé. Si on régresse vers une vieille version, refaire l'ancienne signature.
- Bug Rich : ne pas mettre `[...]` dans les titres `Table(title=...)` car interprété comme markup → utiliser des séparateurs sans crochets.

**Smoke-tests effectués (OK)** :
- `run 1.1.1.1 -c 3` → table colorée + entrée DB.
- `list`, `show 1` → OK.
- `compare 1 2`, `compare 2 --baseline` → OK.
- Web : `/`, `/run/1`, `/compare?a=1&b=2`, `/api/target/1.1.1.1/history`, `/api/run/1` → 200.

---

---

## 2026-05-26 — doc complète + commande `doctor`

**Contexte** : ajout demandé d'une doc plus complète et d'un moyen visuel de diagnostiquer les soucis.

**Modifications** :
- Nouveau module `mtrgraph/doctor.py` : 8 health-checks (deps, mtr binary, mtr -j, capabilities, DNS, disk, db, port) avec rendu Rich coloré + suggestions de fix.
- Nouvelle sous-commande `mtrgraph doctor` (exit code 1 si FAIL).
- Docs ajoutées : `installation.md`, `getting-started.md`, `deployment.md`, `troubleshooting.md`, `faq.md`.
- README refait : tableau des docs + section "Tu rencontres un problème ?".
- `commands.md` : section `doctor` ajoutée.

**Décisions** :
- `importlib.metadata.version()` pour lire les versions des deps (Rich n'expose pas `__version__`).
- Seuils par défaut : disque < 100 MB = FAIL, capabilities absentes = WARN (UDP fonctionne sans), port occupé = WARN (pas un blocage en soi).
- Pas d'auto-fix : doctor *signale* et *suggère* mais ne touche pas le système (principe : un diagnostic ne modifie rien).

**À faire ensuite** :
- Quand on ajoutera un fichier de config TOML, doctor pourra aussi vérifier sa validité.
- Si on ajoute le mode TUI Textual : doctor pourra checker que le terminal supporte 256 couleurs / unicode.

---

---

## 2026-05-26 — support TCP/UDP avec port + baseline scopée

**Contexte** : besoin de pouvoir faire du mtr sur un port TCP précis (HTTPS, VPN, etc.) pour traverser les firewalls et mesurer ce qu'un vrai client verrait.

**Modifications** :
- `runs` étendu avec `protocol TEXT NOT NULL DEFAULT 'icmp'` et `dst_port INTEGER` ; nouvel index `(target, protocol, dst_port, started_at)`. DB existante droppée (juste smoke).
- `probe.run_mtr` accepte `protocol={icmp,udp,tcp}` et `port=N`. Mapping : `udp → -u -P N` (default 33434), `tcp → -T -P N` (default 80). ICMP reste le défaut.
- CLI : `--proto` et `--port` ajoutés à `run` et `daemon`. `list` affiche le scope en colonne magenta. `compare` vérifie la cohérence des scopes et avertit en jaune si mismatch.
- Daemon : baseline scopée `(target, proto, dst_port)`. Header coloré indique le scope.
- TUI : titres `Run #N — host [tcp:443]` etc.
- Web : pill protocole/port dans la liste, sélecteur scope sur la page d'accueil, bandeau rouge "scope différent" sur diff incohérent, API history accepte `?proto=&port=`.
- Doctor : nouveau check `mtr -T (TCP)` qui WARN avec suggestion `setcap` si TCP indisponible.

**Décisions** :
- Scope = triplet `(target, protocol, dst_port)` partout (baseline, history, comparaison).
- Ports par défaut : udp=33434 (style traceroute), tcp=80 (HTTP). Surchargeable avec `--port`.
- Pas de migration auto : DB droppée car pas encore de données métier.
- ICMP reste le défaut pour éviter de surprendre les utilisateurs qui font juste `mtrgraph run X`.
- `compare` n'interdit pas un diff cross-scope, il **avertit** seulement — laisser à l'utilisateur le choix.

**À faire ensuite** :
- Migration idempotente si on touche encore le schéma (commencer à coder `ALTER TABLE` dans `init_db`).
- Possibilité d'un mode auto-baseline qui essaie ICMP puis tombe sur TCP:443 si ICMP filtré.

---

---

## 2026-05-26 — guide d'interprétation des résultats MTR

**Contexte** : utilisateur déstabilisé par un hop intermédiaire à 354 ms (Avg) / 2034 ms (Wrst) sur un mtr vers `www.google.fr` TCP:443, alors que les hops suivants et le hop final étaient à <15 ms. Classique faux positif de rate-limiting control-plane côté routeur Google.

**Modifications** :
- Nouveau doc [docs/reading-mtr.md](reading-mtr.md) avec :
  - règle d'or (regarder le dernier hop, chercher des dégradations qui persistent) ;
  - cas d'école commenté du run `www.google.fr` TCP:443 ;
  - explication forwarding plane vs control plane ;
  - vrais signaux vs faux signaux (tableau de référence) ;
  - workflow de diagnostic ;
  - commandes de creusement (whois, AS lookup, compare ICMP vs TCP).
- `troubleshooting.md` : nouveau symptôme "hop intermédiaire 300 ms / hop suivant 5 ms" qui renvoie vers reading-mtr.md, + bannière en tête du doc.
- `faq.md` : Q/R "pourquoi hop 5 rouge mais hop 8 vert".
- `README.md` : reading-mtr listé en gras dans le tableau des docs et dans la section "Tu rencontres un problème ?".

**Décisions** :
- Reading-mtr.md est volontairement en français informel et pédagogique, pas une référence sèche. Cible : un humain qui découvre mtr.
- Inclure le cas d'école exact que l'utilisateur a vécu — plus parlant qu'un exemple théorique.
- Pas de schémas (ASCII art seulement) pour rester rendable partout.

**À faire ensuite** :
- Idée : ajouter dans le `compare` un message contextuel "la dégradation au hop X est probablement un faux positif car les hops suivants sont OK" — détection automatique.
- Possibilité d'un mode `mtrgraph explain <run_id>` qui génère ce diagnostic auto en texte.

---

---

## 2026-05-26 — probe HTTP (DNS/TCP/TLS/TTFB) pour diagnostiquer S3

**Contexte** : utilisateur a des lenteurs d'accès à un S3 externe et `mtrgraph` (couche réseau seule) ne suffit pas à identifier si c'est le réseau, le TLS, ou S3 lui-même qui rame.

**Modifications** :
- Nouveau module [mtrgraph/http_probe.py](../mtrgraph/http_probe.py) en pure stdlib (`socket`+`ssl`) — pas de dep ajoutée. Mesure DNS, TCP connect, TLS handshake, TTFB, total, status code, erreur par sample. Connexion fermée à chaque sample (cold-start representative).
- DB étendue : tables `http_runs` + `http_samples` avec helpers `insert_http_run`, `insert_http_samples`, `list_http_runs`, `get_http_run`, `get_http_samples`, `delete_http_run`.
- Colors étendus : `HTTP_THRESHOLDS` par étape (DNS/TCP/TLS/TTFB/Total) avec `http_color`/`http_hex`/`http_status_color`.
- TUI : `http_samples_table`, `http_summary_table`, `http_legend`.
- CLI : nouvelles sous-commandes `http`, `http-list`, `http-show`.
- Web : nouvelles routes `/http`, `/http/{id}`, API `/api/http/{id}`, `/api/http/url/history?url=...`. Nav header avec liens "MTR runs" / "HTTP runs".
- Templates : `http_index.html` (listing + graphe historique multi-étapes), `http_run.html` (détail avec bar chart empilé + courbe TTFB).
- Doctor : nouveau check `HTTPS probe` qui appelle `probe_once("https://www.cloudflare.com/")` et affiche les timings dns/tcp/tls/ttfb.
- Nouveau doc [docs/cookbook-s3.md](cookbook-s3.md) : recette complète pour diagnostiquer S3 (4 cas typiques : DNS lent, TLS lent, TTFB lent, tout-OK-mais-app-rame).

**Décisions** :
- Pas de dep externe : stdlib uniquement (socket+ssl). Évite `httpx` qui forcerait HTTP/2 (pas représentatif des SDK AWS classiques).
- Connexion fermée à chaque sample (pas de keep-alive). Mesure du "cold start" — représentatif des clients Lambda, microservices courts, etc. Documenté dans cookbook-s3.md.
- Stockage par sample (table `http_samples`) plutôt qu'agrégats dans `http_runs` — symétrique avec mtr `runs`/`hops`, permet de re-calculer les stats à la demande.
- Pas de baseline scopée HTTP pour l'instant — `compare` HTTP n'existe pas encore. Si besoin, ajouter `mtrgraph http-compare A B` plus tard.
- Pas de `daemon http` natif — utiliser cron en attendant (mentionné dans cookbook-s3.md). Possibilité d'ajouter plus tard si demandé.

**À faire ensuite** :
- `mtrgraph http-compare A B` : diff par étape entre deux runs HTTP.
- `mtrgraph http-daemon URL` : équivalent du daemon mtr.
- Pour les requêtes S3 authentifiées : trop spécifique, laisser au SDK avec hooks. Possibilité d'un mode `--header "X: Y"` pour des cas simples.

---

---

## 2026-05-26 — http-daemon + déploiement Kubernetes

**Contexte** : test sur s3.fr-mar.freepro.com (3 IPs en round-robin 88.212.152.{164,165,166}) avec `--ip` montre que les 3 frontaux sont identiques (Total ~50 ms, 0 erreur). Mais l'utilisateur a des lenteurs intermittentes **depuis ses pods K8s**, donc il faut un monitoring continu qui tourne **dans le cluster** pour capturer le problème quand il se produit.

**Modifications** :
- `db.http_baseline(url, last_n=10)` : médiane par étape (DNS/TCP/TLS/TTFB/Total) sur les N derniers runs d'une URL. Robuste aux outliers.
- `daemon.run_http_daemon()` : boucle de probe + comparaison à la baseline + log coloré. Seuils par étape : warning ≥ 1.5× baseline (avec delta min absolu), critical ≥ 3×. Alerte aussi sur `errors / samples × 100 >= error_threshold` (défaut 10%).
- CLI : nouvelle sous-commande `http-daemon URL` (mêmes flags que `http` + `--every`, `--baseline-n`, `--error-threshold`).
- Flag `--ip` ajouté au probe HTTP (déjà fait dans cette session) : permet de bypass DNS pour tester une IP précise du round-robin, en gardant SNI/Host = hostname original.

**Kubernetes** :
- [Dockerfile](../Dockerfile) : `python:3.12-slim` + `mtr-tiny` + deps, non-root (uid 10001), volume `/data`, port 8765. ~150 MB.
- [k8s/](../k8s/) : namespace, PVC RWO 1Gi, ConfigMap (TARGET_URL etc.), Deployment http-daemon, Deployment web (Service ClusterIP), Deployment mtr-daemon optionnel (avec CAP_NET_RAW), CronJob optionnel, kustomization.yaml.
- Stratégie : `Recreate` partout (SQLite mono-writer), PVC partagée daemon/web, livenessProbe basée sur la mtime du fichier DB pour le daemon, HTTP probe pour le web.

**Nouvelle doc [kubernetes.md](kubernetes.md)** : 11 sections — build image, config, déploiement, accès web (port-forward + ingress), monitoring multi-URL, diag depuis l'intérieur du pod, sauvegarde, limites SQLite en K8s, alertes via Loki, désinstall, troubleshooting K8s (table de 6 symptômes typiques).

**Docs mises à jour** :
- `commands.md` : section `http-daemon` ajoutée avec exemples (S3 simple, IP forcée, error-threshold).
- `cookbook-s3.md` : section "monitoring continu" remplace le cron par `http-daemon` natif + nouvelle section "Faire tourner ça depuis Kubernetes".
- `deployment.md` : pointe vers `kubernetes.md` pour le cas K8s.
- `README.md` : trois nouvelles features dans la liste, `kubernetes.md` dans le tableau des docs.

**Décisions** :
- HTTP baseline par URL uniquement (pas par (url, ip)) — simple. Si besoin par-IP : utiliser `--label` distinct OU lancer plusieurs daemons.
- Seuils HTTP : 1.5× = warning, 3× = critical, avec delta minimum absolu pour éviter le bruit sur des valeurs faibles. Documentés dans `daemon.HTTP_DEGRADATION`.
- Image Docker : mtr-tiny (pas mtr complet) pour réduire la taille, suffisant pour `mtr -j`. Non-root par défaut, pas de capabilities — il faut les ajouter explicitement dans le pod pour ICMP/TCP SYN.
- K8s : `strategy: Recreate` mandatoire car SQLite. Si l'utilisateur passe à Postgres plus tard, on pourra faire du RollingUpdate.

**Smoke-test http-daemon** : OK sur Free Pro S3, baseline puis OK runs successifs avec total ~50-78 ms.

**À faire ensuite** :
- Tester le build Docker (en cours, à confirmer).
- Quand l'utilisateur déploie en K8s : récupérer les logs pour voir si l'intermittence est capturée.
- Possibilité d'ajouter `/metrics` Prometheus dans le web pour intégration Alertmanager (plus propre que grep DEGRADATION dans Loki).
- `http-compare A B` (diff entre deux http_runs) — pas encore implémenté.

---

---

## 2026-05-26 — S3 SigV4 + UI web interactive

**Contexte** : `http-daemon` permet le monitoring continu mais ne mesure que des opérations non authentifiées (HEAD `/`). Pour vraiment diagnostiquer la lenteur S3 il faut signer les requêtes et tester les opérations qui rament en prod (LIST sur gros bucket, GET d'objet précis, PUT, etc.). L'utilisateur veut aussi une interface web moderne pour tester interactivement avec login/mdp.

**Modifications** :
- Nouveau module [mtrgraph/s3_client.py](../mtrgraph/s3_client.py) (~360 lignes pure stdlib) : SigV4 complet (hash canonical request, signing key derivation), client HTTP/HTTPS bas niveau avec timings DNS/TCP/TLS/TTFB, 5 opérations (`list_bucket`, `head_object`, `get_object`, `put_object`, `delete_object`), parsing XML d'erreurs S3.
- DB : nouvelle table `s3_runs` avec colonnes par étape + opération + bucket/key + status + bytes + summary + erreur. Helpers `insert_s3_run`, `list_s3_runs`, `get_s3_run`, `delete_s3_run`.
- CLI : `s3-list`, `s3-head`, `s3-get`, `s3-put`, `s3-delete`, `s3-runs`. Credentials via `--access-key/--secret-key/--session-token` ou env `AWS_*`. Helper `_resolve_creds` + `_print_s3_result` (table Rich colorée avec status, timings, summary, erreur).
- Web : nouvelle route `GET /s3` avec UI moderne (formulaire + onglets opérations + cartes de timings colorées + historique). API `POST /api/s3/test` (body Pydantic), `GET /api/s3/runs`, `DELETE /api/s3/runs/{id}`.
- Template `s3.html` : ~300 lignes HTML/CSS/JS vanilla. Tabs LIST/HEAD/GET/PUT/DELETE, autocomplete sur endpoints/buckets via datalist, localStorage opt-in pour mémoriser les creds côté navigateur (jamais persisté serveur), résultat live avec stages colorés selon seuils, historique avec suppression. Couleurs par étape réplique celles du Python (HTTP_THRESHOLDS).
- Nav header : ajout de l'onglet "S3 testing".
- K8s : ConfigMap rendu générique (CHANGE-ME au lieu de Free Pro), exemple de Secret AWS_* dans le même fichier, deployment-web.yaml a un placeholder commenté pour envFrom.

**Smoke-test SigV4** : MinIO en container Docker (testkey/testsecret123), bucket de test avec 2 objets. Toutes les 5 opérations passent en 200/204 :
- LIST → 200, 2 keys parsées
- HEAD → 200, etag retourné
- GET → 200, 13 bytes
- PUT 10 KiB → 200, etag retourné
- DELETE → 204

**Test Free Pro avec creds bidons** : signature acceptée, Free Pro répond `403 InvalidAccessKeyId` proprement → signature SigV4 conforme.

**Décisions** :
- Pas de boto3 même en optional (extras_require) pour cette itération : SigV4 stdlib couvre 95% des cas (access/secret/session-token), boto3 ajouterait 50 MB pour IAM/STS/SSO qu'on n'utilise pas. Documenté comme roadmap.
- Credentials jamais stockés en DB ni en log. localStorage navigateur opt-in. Warning visible en haut de la page /s3.
- Path-style only (`endpoint/bucket/key`), pas de virtual-hosted (`bucket.endpoint`). Tous les S3-compat acceptent path-style.
- PUT single-shot (pas multipart). Documenté comme limite.
- Free Pro retiré comme défaut partout, replacé par CHANGE-ME ou liste de fournisseurs.

**À faire ensuite** :
- Multipart upload (UploadPart) si besoin d'objets > 5 MiB.
- `s3-daemon` (monitoring continu d'une opération S3, équivalent http-daemon).
- Charts de timings par opération dans `/s3` (Chart.js).
- Support virtual-hosted style si demandé.
- Tests automatisés contre MinIO (CI).

---

---

## 2026-05-26 — scheduler en mode serveur (tests automatiques MTR/HTTP/S3)

**Contexte** : l'utilisateur veut configurer des tests récurrents depuis l'UI web sans lancer manuellement à chaque fois — et avec une option de planification aléatoire pour ne pas synchroniser les sondes et capturer des problèmes intermittents.

**Modifications** :
- DB : nouvelle table `schedules` (id, name, kind, config JSON, schedule_mode fixed|random, interval_s OU min/max_interval_s, enabled, created_at, last_run_at, next_run_at, last_run_id, last_status). Helpers `list_schedules`, `get_schedule`, `insert_schedule`, `update_schedule`, `delete_schedule`, `due_schedules`.
- Nouveau module [mtrgraph/scheduler.py](../mtrgraph/scheduler.py) : thread daemon qui tick chaque seconde, sélectionne les schedules dus (next_run_at ≤ now ou null), exécute selon le kind. Mode random recalcule un délai random(min, max) après chaque run.
- 3 kinds supportés : `s3` (les 5 ops via SigV4), `http` (probe DNS/TCP/TLS/TTFB), `mtr` (run_mtr complet + parse + insert dans `runs`/`hops`).
- **MTR avec auto_compare** : après chaque run mtr, si `auto_compare: true`, calcule baseline_hops sur les N derniers runs du même `(target, proto, dst_port)` et détecte la sévérité (`ok`, `warning:hopX avg A→B ms`, `critical:...`). Le statut est stocké dans `last_status` de la schedule.
- **MTR avec targets_pool** : si pool renseigné, pioche une cible aléatoire à chaque tick. Baseline est scopée par cible, donc reste cohérente.
- **S3 avec keys_pool** : pour head/get/delete, pioche une key aléatoire (utile pour tester des objets variés).
- **S3 PUT** avec `{ts}` dans la key remplacé par le timestamp à chaque run (évite collisions).
- Web : route `GET /schedules` (page), API CRUD `POST/PUT/DELETE /api/schedules[/{id}]`, `POST /api/schedules/{id}/toggle`, `POST /api/schedules/{id}/run-now`. Scheduler démarré via `@app.on_event("startup")`, arrêté à `shutdown`.
- Template [schedules.html](../mtrgraph/templates/schedules.html) : UI moderne 2 colonnes (liste des schedules à gauche, formulaire à droite). Tabs S3/HTTP/MTR selon le kind, sous-tabs des opérations S3, mode fixe vs random avec affichage adaptatif des champs. Auto-refresh toutes les 5 s.
- Nav header : ajout onglet "Schedules".
- Nouveau doc [docs/schedules.md](schedules.md) : guide complet (kinds, modes, sécurité, cas d'usage, API REST, K8s).

**Décisions** :
- Scheduler en **thread** (pas process séparé) : simple, partage la DB, mort en même temps que le web. Pour scaler, basculer en process via une CronJob K8s qui exécute juste les schedules.
- Credentials stockés en **clair** dans le `config` JSON. Documenté en warning rouge dans l'UI et dans schedules.md. Pour la prod, recommandation : creds dédiés monitoring + auth proxy devant le web.
- `interval_s` minimum = 5 s (validation). Au-dessous, le tick overhead devient significatif et les LIST S3 peuvent se chevaucher.
- `auto_compare` par défaut activé pour MTR (le seul cas où c'est immédiatement utile). HTTP n'a pas d'auto_compare dans le scheduler (utiliser `http-daemon` CLI qui a déjà cette logique).
- Pas de retry automatique. Si un tick échoue, le prochain se contente de re-tenter au prochain intervalle. Documenté.
- Pas d'alerte externe pour l'instant (webhook/Slack) — ajouter plus tard si besoin.

**Smoke-test à faire** (todo restant) :
- Rebuild Docker image
- Créer un schedule via l'UI, vérifier qu'il s'exécute, qu'il apparaît dans /s3 ou /http
- Tester run-now, toggle, edit, delete
- Tester mode random
- Tester MTR avec pool de cibles + auto_compare

**À faire ensuite** :
- Webhook/Slack sur statut dégradé (boucle dans le scheduler après chaque run).
- Endpoint `/metrics` Prometheus.
- Comparaison HTTP auto comme MTR.
- Export/import des schedules en YAML pour versionner.

---

---

## 2026-05-26 — chart S3, vue par IP, auto-compare S3, webhooks

**Contexte** : avec les schedules + S3 SigV4 + scheduler en place, on pouvait collecter mais pas vraiment **localiser** une latence intermittente. 4 manques identifiés : chart historique sur /s3, vue par IP résolue (critique pour les S3 en round-robin DNS comme Free Pro), auto-compare S3 (équivalent du `auto_compare` MTR), et alerte externe (webhook).

**Modifications** :
- DB : `schedules.webhook_url` (TEXT, nullable). Migration idempotente via `_migrate_add_column` dans `init_db` — `ALTER TABLE ADD COLUMN` si absent. Pour les DBs existantes, la première connexion ajoute la colonne sans rien casser. Nouveau `db.s3_baseline(endpoint, operation, bucket, last_n)` qui calcule la médiane par étape sur les N derniers runs réussis (HTTP 2xx/3xx).
- Scheduler : `S3_DEGRADATION` (mêmes seuils que HTTP : 1.5×/3× + min_delta absolu par étape). Nouvelle fonction `_s3_status_with_compare` appelée après chaque run S3 réussi — si `auto_compare: true` dans le config, compare aux N derniers runs et upgrade le statut en `warning:stage A→B ms` ou `critical:...`. Insertion du run **avant** la lecture de la baseline (sinon le current run ne contribuerait pas à la fenêtre — on accepte un léger lissage).
- Scheduler : `_post_webhook(url, payload)` stdlib `urllib.request` (timeout 5s, erreur loggée mais non-propagée). `_maybe_notify(row, status, run_id)` appelé après chaque `_run_schedule` (s3, mtr, http) — POST si le statut commence par `warning:`, `critical:`, `err:`, `http:4`, `http:5`, ou `unknown`. Payload Slack-compatible (`{text: "..."}` + champs structurés).
- Web : `ScheduleIn.webhook_url` ajouté. `insert_schedule` et `update_schedule` propagent. 3 nouvelles routes API :
  - `GET /api/s3/history?endpoint=X&bucket=Y&operation=Z&limit=200` — time-series ordonné ancien→récent
  - `GET /api/s3/by-ip?...` — agrégation par `resolved_ip` avec count, err_pct, avg DNS/TCP/TLS/TTFB/Total. Triée par avg_total_ms desc
  - `GET /api/s3/filters` — endpoints/buckets/operations distincts pour peupler les dropdowns
- Template `schedules.html` : checkbox `s3-auto-compare` + champ `s3-baseline-n` dans le fieldset S3, champ `sched-webhook` à la fin du formulaire avec help text Slack-compatible. JS `buildPayload`/`fillForm`/`clearForm` mis à jour.
- Template `s3.html` réécrit en grande partie : nouvelle section "Historique par étape (filtré)" avec barre de filtres (endpoint/bucket/operation/view) + canvas Chart.js. 3 vues sélectionnables : `stages` (DNS/TCP/TLS/TTFB/Total empilées), `per-ip` (TTFB par IP, 1 ligne par IP), `total-per-ip`. Nouvelle section "Statistiques par IP résolue" : table colorée vert/orange/rouge selon ratio vs médiane et taux d'erreur. Colonne IP ajoutée dans l'historique. Auto-refresh toutes les 10s.

**Décisions** :
- Auto-compare S3 mis sur l'`endpoint+operation+bucket` (pas par-IP). Si l'utilisateur veut comparer par-IP, c'est la **vue par IP** sur /s3 qui fait le job (pas le scheduler). Évite l'explosion combinatoire des baselines.
- Baseline S3 sur les runs réussis uniquement (`http_status BETWEEN 200 AND 399`). Sinon une rafale de 503 pollue la baseline.
- Webhook au format Slack par défaut mais payload générique : `{text, schedule_id, schedule_name, kind, status, severity, run_id, timestamp}`. Marche aussi pour Teams (texte simple), Mattermost, generic POST.
- Pas de retry webhook : la dégradation se reproduira au prochain tick.
- Vue par IP colorée selon **médiane des IPs** observées dans le filtre, pas un seuil absolu. Si toutes les IPs sont à 500ms, on a 0 alerte. Si une IP est à 1500ms et les autres à 50ms, l'écart saute aux yeux.

**Smoke-test à faire** (todo final) :
- Rebuild Docker
- Vérifier que la migration de l'ancienne DB ajoute bien `webhook_url`
- Créer un schedule avec webhook pointant vers `nc -l 9999` + un PUT inexistant pour générer un 403, vérifier la notif
- Créer un S3 LIST avec auto_compare, faire 5 runs, vérifier que le statut devient `ok:vs-baseline(N)`
- Ouvrir /s3, sélectionner Free Pro, vérifier le chart "par IP" (s'il y a des données depuis les tests précédents)

**À faire ensuite** :
- Chart pour MTR par hop dans une page dédiée
- Endpoint Prometheus /metrics
- Comparaison HTTP auto comme MTR/S3
- Export YAML des schedules pour versioning Git

---

---

## 2026-05-27 — alertes erreurs + auto-MTR + TCP scheduler + retention + refacto 3 phases

**Contexte** : session marathon. L'utilisateur a demandé en série :
1. Mieux voir les erreurs sur le dashboard (1 erreur invisible)
2. Auto-MTR quand on lance un test S3 (sinon courbe RTT vide dans le dashboard)
3. TCP retransmissions visibles dans le dashboard
4. Range temporel + auto-refresh dans le dashboard
5. Lookup d'un timestamp précis (panneau "run le plus proche")
6. Mode "alert externe" → webhooks
7. **Audit code + refacto** car la sensation que tout est couplé
8. **Retention auto** pour SQLite long terme

**Modifications fonctionnelles** :
- **Dashboard** :
  - Chart 5 (statuts HTTP) refait : hauteur constante, couleur = type (vert/jaune/rouge)
  - Chart 6 nouveau : taux d'erreurs glissant sur 10 runs (courbe rouge)
  - Panneau "⚠ Erreurs récentes" : table cliquable → zoom au timestamp
  - Chart 7 nouveau : TCP retrans % + OutSegs/s (double axe)
  - Range temporel : `15min / 1h / 6h / 24h / custom`
  - Custom : datetime picker + fenêtre ± + bouton "🔍 Run le plus proche"
  - Auto-refresh sélectionnable : Off / 1s / 5s / 10s / 15s / 30s avec banner
- **Auto-MTR** : champ `auto_mtr` dans S3 schedules + `S3TestRequest` (défaut `True`). À chaque S3 réussi, MTR background TCP:443 vers `resolved_ip` (label `auto-mtr`)
- **TCP scheduler** : nouveau kind `tcp` (config: `duration_s`). Tick → `tcp_stats.sample(N)` → ligne dans `tcp_samples`
- **Random_ops S3** : alternance auto LIST/HEAD/GET/PUT/DELETE avec garantie de safe-delete (jamais hors `s3_tracked_objects`). Pool min/max, prefix obligatoire
- **Cleanup tracked** : bouton 🧹 Purger sur cartes random_ops, popup à la suppression du schedule pour purger les objets restants
- **Webhooks** : `webhook_url` par schedule, POST JSON Slack-compatible sur statut warning/critical/err
- **Auto-compare S3** : médiane par étape sur N derniers runs (équivalent du MTR auto-compare)
- **Trim défensif** des credentials (tab/espace caché copié-collé → SignatureDoesNotMatch invisible auparavant)
- **Bug SigV4** : double-encoding query string corrigé (LIST avec prefix → 403 avant, 200 maintenant)
- **Toast moderne** remplace les `alert()` qui affichaient "127.0.0.1:8765 says" (perception)

**Retention & WAL** :
- WAL mode + PRAGMAs (`synchronous=NORMAL`, `busy_timeout=5000`, `temp_store=MEMORY`) activés au `_connect`
- Nouveau module `retention.py` : `apply_retention()` (DELETE old + VACUUM), `db_stats()`, `RetentionTask` thread
- CLI `mtrgraph retention [--dry-run] [--max-age-days N] [--no-vacuum]`
- API `GET /api/admin/db-stats` + `POST /api/admin/retention`
- `RetentionTask` démarrée par `web.create_app()` au startup. Env vars `MTRGRAPH_RETENTION_DAYS` (30) + `MTRGRAPH_RETENTION_PERIOD_HOURS` (24)
- Doctor : nouveau check `db size` (warn ≥ 500 MB, fail ≥ 2 GB)
- Volumes typiques documentés dans `docs/retention.md`

**Refacto 3 phases (5010 lignes, 14 modules)** :
- **Phase 1** : `web.py` (927 l.) → `web/` (6 fichiers via APIRouter). Routes splittées par domaine : mtr/http/s3/schedules/dashboard/admin. `create_app()` orchestre via `include_router(create_router(db_path, templates))`. Aucune URL changée, aucun comportement modifié. Smoke-test : tous endpoints 200 + scheduler/retention OK
- **Phase 2** : `db.py` (654 l.) → `db/` (8 fichiers). `db/__init__.py` garde le schema central + ré-exporte toutes les fonctions publiques via `from .mtr import *` etc. → 100% compat backward, le code existant (`from . import db; db.insert_run(...)`) marche sans modification. Smoke-test : retention CLI, run-now, scheduler tick — tout passe
- **Phase 3** : `scheduler.py` (588 l.) → `scheduler/` (7 fichiers dont 4 executors). `__init__.py` : Scheduler class + dispatcher `_run_schedule`. `webhooks.py` isolé. Executors par kind (`executors/{s3,http,mtr,tcp}.py`). `trigger_auto_mtr` ré-exporté depuis `__init__` pour compat. Smoke-test : run-now S3 → `ok:vs-baseline(10)`, scheduler TCP tick auto, dashboard 200

**Bilan refacto** :
| Module | Avant | Après |
|--------|-------|-------|
| web | 927 lignes monolithique | 6 fichiers (max 250 l.) |
| db  | 654 lignes monolithique | 8 fichiers (max 150 l.) |
| scheduler | 588 lignes monolithique | 7 fichiers (4 executors isolés) |

**Décisions** :
- Refacto : packages avec `__init__.py` qui ré-exporte, pour ne casser aucun import existant. Smoke-test entre chaque phase.
- Retention : `RetentionTask` en thread daemon dans le process web (pas un cron k8s séparé). Simple, et stoppe propre via `app.on_event('shutdown')`.
- WAL : activé en dur dans `_connect` (chaque session). PRAGMA est persisté, donc fonctionnel même si une seule session l'a appliqué.
- Schedule webhook stocké par schedule (pas global). Permet d'avoir un webhook Slack pour S3 prod et un autre pour MTR debug.
- Trim creds côté backend (`sign_request`) en plus du frontend, défense en profondeur.
- Auto-MTR par défaut activé sur S3 schedules (la majorité des users veut la courbe RTT). Désactivable par checkbox.

**Smoke-tests réussis** :
- Tous endpoints HTTP (15+ routes testées)
- WAL mode persisté en DB
- Retention CLI dry-run + apply (rien à purger sur DB récente)
- Webhook avec netcat reçoit POST JSON sur `http:403`
- Auto-MTR : POST /api/s3/test → `[auto-mtr] 88.212.152.166 → 7 hops`
- Scheduler TCP tick auto toutes les 10s
- Schedule random_ops MinIO : 25 runs avec mix LIST/HEAD/GET/PUT/DELETE, tracked correct, purge OK

**Nouvelles docs** :
- [retention.md](retention.md) : volumes, WAL, env vars, CLI, API, monitoring, quand passer à Postgres
- [architecture.md](architecture.md) : structure post-refacto avec arborescence détaillée
- [db-schema.md](db-schema.md) : ajout tables `s3_tracked_objects`, `tcp_samples`, `schedules`
- README + deployment + k8s : env vars retention

**À faire ensuite (idées)** :
- Endpoint `/metrics` Prometheus (rolling KPIs)
- Migration Postgres optionnelle (dépend de croissance réelle)
- Tests unitaires DB + scheduler (pas urgent vu la stabilité actuelle)
- Export YAML des schedules pour versioning Git

---

## Template pour la prochaine session

```
## YYYY-MM-DD — titre court

**Contexte** : pourquoi on reprend.
**Modifications** :
- …
**Décisions** :
- …
**À faire ensuite** :
- …
```

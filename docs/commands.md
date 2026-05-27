# Commandes CLI

Entrée principale : `python3 -m mtrgraph.cli <sous-commande>`

Option globale (toutes les commandes) : `--db PATH` (défaut `~/.local/share/mtrgraph/mtrgraph.db`).

## `run TARGET`
Lance un MTR unique et le sauvegarde.

| flag             | défaut         | rôle                                                       |
|------------------|----------------|------------------------------------------------------------|
| `-c, --cycles`   | 10             | nombre de cycles mtr                                       |
| `-i, --interval` | 1.0            | secondes entre cycles                                      |
| `--label`        | —              | étiquette libre stockée dans `runs`                        |
| `--proto`        | `icmp`         | `icmp`, `udp` ou `tcp` (TCP SYN probes)                    |
| `--port`         | auto           | port destination pour udp/tcp (défaut udp=33434, tcp=80)   |

Sortie : table colorée (Rich) + légende des seuils. Le titre montre `[icmp]`, `[udp:53]`, `[tcp:443]` selon le scope.

Exemples :
```bash
mtrgraph run 8.8.8.8                                    # ICMP par défaut
mtrgraph run 8.8.8.8 --proto udp                        # UDP port 33434
mtrgraph run 1.1.1.1 --proto tcp --port 443             # TCP SYN vers :443
mtrgraph run vpn.exemple.fr --proto tcp --port 443 -c 30 --label "vpn-tcp"
```

⚠ TCP et ICMP nécessitent `cap_net_raw` sur le binaire mtr : `sudo setcap cap_net_raw+ep /usr/bin/mtr`.
UDP fonctionne sans privilège. Vérification : `mtrgraph doctor`.

## `list`
Affiche la liste des runs enregistrés (plus récents d'abord).

| flag       | défaut | rôle                                     |
|------------|--------|------------------------------------------|
| `--target` | —      | filtre sur la cible                      |
| `--limit`  | 50     | nb max de runs                           |

## `show RUN_ID`
Réaffiche un run stocké (table colorée).

## `compare A B`
Diff hop par hop entre deux runs.

⚠ Si A et B n'ont pas le même `(target, proto, port)`, un avertissement s'affiche — la comparaison reste calculée mais elle est rarement pertinente (latence TCP:443 ≠ latence ICMP, etc.).

Variante baseline : `compare B --baseline [--baseline-n 10]`
→ compare `B` à la médiane par hop des N derniers runs sur le **même** `(target, proto, port)` que B. Une cible interrogée à la fois en ICMP et en TCP aura **deux baselines distinctes**.

Sévérités :
- `critical` : Δloss ≥ 10 pt
- `warning`  : Δloss ≥ 3 pt **ou** Δlatence ≥ +50% (et ≥ 20 ms)
- `ok` sinon

## `daemon TARGET`
Boucle de monitoring.

| flag           | défaut    | rôle                                   |
|----------------|-----------|----------------------------------------|
| `--every`      | 300       | intervalle en secondes                 |
| `-c, --cycles` | 10        | cycles mtr par itération               |
| `--label`      | "daemon"  | étiquette des runs                     |
| `--baseline-n` | 10        | taille de la fenêtre baseline          |
| `--proto`      | `icmp`    | `icmp`, `udp` ou `tcp`                 |
| `--port`       | auto      | port destination pour udp/tcp          |

Pour surveiller une même cible sur plusieurs protocoles : lancer un daemon par scope.
```bash
mtrgraph daemon vpn.exemple.fr --proto icmp                    --every 300 &
mtrgraph daemon vpn.exemple.fr --proto tcp --port 443          --every 300 &
mtrgraph daemon vpn.exemple.fr --proto tcp --port 1194         --every 300 &
```
Chaque scope a sa propre baseline indépendante.

À chaque tour : sauvegarde le run, compare à la baseline, log coloré (vert OK / rouge dégradation).

Arrêt : `Ctrl+C`.

## `web`
Lance le serveur web (FastAPI / uvicorn).

| flag     | défaut       |
|----------|--------------|
| `--host` | 127.0.0.1    |
| `--port` | 8765         |

Pages : `/` (runs + historique), `/run/{id}`, `/compare?a=X&b=Y`.

## `delete RUN_ID`
Supprime un run (cascade sur ses hops).

## `http URL`
Mesure les timings HTTP : DNS, TCP connect, TLS handshake, TTFB, total. Utile pour diagnostiquer la lenteur d'une API ou d'un S3 (voir [cookbook-s3.md](cookbook-s3.md)).

| flag              | défaut | rôle                                                  |
|-------------------|--------|-------------------------------------------------------|
| `-c, --count`     | 10     | nombre de samples                                     |
| `-m, --method`    | HEAD   | HEAD ou GET                                           |
| `-i, --interval`  | 0.5    | pause entre samples (s)                               |
| `-T, --timeout`   | 10.0   | timeout par sample (s)                                |
| `--label`         | —      | étiquette                                             |
| `-v, --verbose`   | —      | affiche aussi le détail par sample                    |

Connexion fermée à chaque sample (pas de keep-alive) → tu mesures le coût "cold start" d'un client.

Exemple :
```bash
mtrgraph http https://s3.eu-west-3.amazonaws.com/ -c 30 --label s3-test
```

## `http-daemon URL`
Probe HTTP en boucle avec alerte si dégradation par rapport à la baseline (médiane par étape sur les N derniers runs de la même URL).

| flag              | défaut       | rôle                                                  |
|-------------------|--------------|-------------------------------------------------------|
| `--every`         | 60           | intervalle en secondes                                |
| `-c, --count`     | 5            | samples par itération                                 |
| `-m, --method`    | HEAD         | HEAD ou GET                                           |
| `-T, --timeout`   | 10.0         | timeout par sample                                    |
| `--label`         | http-daemon  | étiquette                                             |
| `--baseline-n`    | 10           | nombre de runs dans la baseline                       |
| `--error-threshold` | 10.0       | alerter si %% d'erreurs dépasse ce seuil              |
| `--ip`            | —            | forcer une IP (DNS bypass, SNI/Host gardés)           |

Sévérités :
- `warning` : avg d'une étape ≥ 1.5× la baseline ET delta ≥ seuil minimum (DNS/TCP 20ms, TLS 30ms, TTFB 50ms, Total 100ms).
- `critical` : avg ≥ 3× la baseline.
- Sur erreurs : alerte si `errors / samples × 100 >= --error-threshold`.

Exemples :
```bash
# Surveillance simple toutes les 60s
mtrgraph http-daemon https://s3.fr-mar.freepro.com/ --every 60 -c 5

# Surveiller une IP précise (par exemple pour isoler un load-balancer)
mtrgraph http-daemon https://s3.fr-mar.freepro.com/ --ip 88.212.152.164 --every 30 -c 3

# Seuil d'erreur plus strict (alerte dès 5%)
mtrgraph http-daemon https://api.example.com/ --error-threshold 5
```

Voir aussi : [docs/kubernetes.md](kubernetes.md) pour faire tourner ça dans un pod.

## `http-list`
Liste les http_runs (plus récents d'abord).

| flag       | défaut | rôle                       |
|------------|--------|----------------------------|
| `--url`    | —      | filtre sur l'URL           |
| `--limit`  | 50     | nb max                     |

## `http-show RUN_ID`
Réaffiche un http_run avec résumé + détail par sample colorés.

## `s3-list` / `s3-head` / `s3-get` / `s3-put` / `s3-delete`
Opérations S3 authentifiées (SigV4 pur Python, compatible AWS / MinIO / Scaleway / OVH / Cellar / Free Pro / etc.).

Flags communs :
| flag                | défaut       | rôle                                                      |
|---------------------|--------------|-----------------------------------------------------------|
| `--endpoint URL`    | (requis)     | URL de l'endpoint sans le bucket                          |
| `--bucket`          | (requis)     | nom du bucket                                             |
| `--region`          | `us-east-1`  | région AWS                                                |
| `--access-key`      | env          | sinon `AWS_ACCESS_KEY_ID`                                 |
| `--secret-key`      | env          | sinon `AWS_SECRET_ACCESS_KEY`                             |
| `--session-token`   | env          | sinon `AWS_SESSION_TOKEN` (STS)                           |
| `-T, --timeout`     | 30.0         |                                                           |
| `--label`           | —            | étiquette                                                 |

Spécifiques :
- `s3-list` : `--prefix STR`, `--max-keys INT` (défaut 1000)
- `s3-head` / `s3-get` / `s3-delete` : `--key STR` requis
- `s3-put` : `--key STR` requis ; `--file PATH` OU `--size-kb INT` (random) ; `--content-type STR`

`s3-runs` : liste l'historique des runs S3 (avec `--endpoint`, `--operation`, `--limit`).

Doc dédiée : [s3-testing.md](s3-testing.md). Interface web sur `/s3` (recommandée pour le diagnostic interactif).

## `doctor`
Lance une batterie de vérifications de l'environnement et affiche un tableau coloré :

| check                | vérifie                                                     |
|----------------------|-------------------------------------------------------------|
| `deps python`        | présence de rich, fastapi, uvicorn, jinja2                  |
| `mtr binary`         | présence et version de `mtr`                                |
| `mtr -j`             | que `mtr -j` produit du JSON valide                         |
| `mtr capabilities`   | setuid root ou `cap_net_raw` (sinon UDP forcé)              |
| `mtr -T (TCP)`       | que les probes TCP SYN fonctionnent (sinon WARN + fix)      |
| `DNS`                | résolution de `one.one.one.one`                             |
| `HTTPS probe`        | DNS+TCP+TLS+TTFB vers cloudflare.com en 1 sample            |
| `disk`               | > 100 MB libres sur le dossier de la DB                     |
| `db`                 | DB accessible, tables `runs`/`hops` présentes               |
| `port 127.0.0.1:8765`| port web libre                                              |

Sortie :
- `✓ OK` en vert, `! WARN` en jaune, `✗ FAIL` en rouge gras ;
- une section **Suggestions** avec la commande à exécuter pour chaque problème ;
- exit code `1` si au moins un FAIL, `0` sinon.

→ Détails et résolutions dans [troubleshooting.md](troubleshooting.md).

## Exemples concrets

```bash
# Baseline initiale puis monitoring continu
python3 -m mtrgraph.cli run 8.8.8.8 -c 30 --label baseline
python3 -m mtrgraph.cli daemon 8.8.8.8 --every 600 -c 20

# Avant/après changement de config réseau
python3 -m mtrgraph.cli run vpn.exemple.fr --label avant
# ... change la conf ...
python3 -m mtrgraph.cli run vpn.exemple.fr --label apres
python3 -m mtrgraph.cli compare 12 13

# Web en arrière-plan
python3 -m mtrgraph.cli web --host 0.0.0.0 --port 8765 &
```

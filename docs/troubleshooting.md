# Diagnostic & résolution de problèmes

> **Avant de paniquer sur un hop rouge** : lis [reading-mtr.md](reading-mtr.md).
> 80% des "problèmes" mtr sont en réalité du rate-limiting côté routeur intermédiaire — pas un vrai souci réseau.

> **Premier réflexe pour les soucis d'environnement** : `python -m mtrgraph.cli doctor`
> Cette commande vérifie en un coup d'œil mtr, deps Python, DNS, disque, DB et port web, et te dit quoi faire si quelque chose cloche.

```
mtrgraph doctor
✓ deps python         OK     rich=15.0.0 fastapi=0.136.3 jinja2=3.1.6 …
✓ mtr binary          OK     /usr/bin/mtr — mtr 0.95
✓ mtr -j              OK     JSON OK sans privilège
! mtr capabilities    WARN   aucune capability ICMP — UDP utilisé par défaut
✓ DNS                 OK     résolution OK (one.one.one.one)
✓ disk                OK     libre 139418 MB sur /tmp
✓ db                  OK     /tmp/mtrgraph-smoke.db · runs=2
✓ port 127.0.0.1:8765 OK     libre
```

Légende : `✓ OK` (vert) · `! WARN` (jaune) · `✗ FAIL` (rouge gras).
Une section **Suggestions** liste les commandes à exécuter pour chaque WARN/FAIL.

---

## Symptômes → causes → fixes

### `mtr -j` retourne du texte tabulaire au lieu de JSON
**Cause** : version de mtr trop ancienne (< 0.86).
**Diag** : `mtr --version` (doit afficher ≥ 0.86, on tourne sur 0.95).
**Fix** : `sudo apt install --only-upgrade mtr` ou compiler depuis les sources.

### `mtr exited 1: Failure to start mtr`
**Cause** : pas la permission ICMP, fallback UDP indisponible (firewall sortant ?).
**Diag** :
```bash
mtr -u -j -c 1 8.8.8.8    # force UDP
mtr -T -j -c 1 8.8.8.8    # force TCP (utile derrière firewall strict)
```
**Fix** : utiliser le mode qui passe, ou donner ICMP au binaire :
```bash
sudo setcap cap_net_raw+ep /usr/bin/mtr
```

### Un hop intermédiaire affiche 300 ms / 50% perte, le suivant retombe à 5 ms / 0%
**Cause** : le routeur en question rate-limite la génération de ses ICMP TTL-exceeded (par design, anti-DoS — cf. RFC 1812). Le trafic *traverse* ce routeur normalement, seules les **réponses de contrôle** sont lentes/droppées.
**Diag** : regarde le **dernier hop** : c'est lui qui dit la vérité sur ton expérience réelle.
**Fix** : **aucun, c'est normal**. Détail complet et autres faux signaux dans [reading-mtr.md](reading-mtr.md).

### Tous les hops après le #1 affichent `???`
**Cause** : les routeurs intermédiaires bloquent les ICMP/UDP/TCP réponses TTL-exceeded — c'est normal sur certains chemins (transits, opérateurs).
**Diag** : tester une autre cible (`mtrgraph run 8.8.8.8`) pour exclure un souci local.
**Fix** : aucun, c'est le réseau. Comparer avec `--baseline` pour voir si la situation s'est dégradée vs avant.

### `permission denied: /home/.../.local/share/mtrgraph/mtrgraph.db`
**Cause** : le dossier parent n'est pas écrivable (ex. exécution sous un autre user).
**Diag** : `ls -la ~/.local/share/mtrgraph/`
**Fix** : `chown -R $USER ~/.local/share/mtrgraph/` ou pointer ailleurs : `--db /tmp/mtrgraph.db`.

### `sqlite3.OperationalError: database is locked`
**Cause** : un `daemon` tourne et un autre process tente d'écrire en même temps.
**Diag** : `ps -ef | grep mtrgraph`
**Fix** : un seul writer à la fois. Si tu veux deux daemons sur deux cibles, lance-les avec des `--db` différents OU accepte la contention (SQLite gère, juste plus lent).

### Port 8765 occupé au lancement de `web`
**Cause** : un autre service écoute (ou une instance précédente n'a pas été tuée).
**Diag** : `ss -ltnp | grep 8765` ou `lsof -i :8765`
**Fix** : tuer le process, ou lancer sur un autre port : `mtrgraph web --port 8766`.

### Pages web en `500 Internal Server Error`
**Cause typique** : incompatibilité de version (Starlette ≥ 1.0 exige `TemplateResponse(request, name, ctx)`).
**Diag** : regarder la stacktrace dans le terminal qui lance `web`. Chercher `TypeError: unhashable type: 'dict'`.
**Fix** : déjà corrigé dans `web.py`. Si tu vois le même bug avec un autre template, vérifie qu'on passe bien `request` en 1er argument positionnel.

### Web vide alors qu'il y a des runs en CLI
**Cause** : tu as utilisé un `--db` différent en CLI et en web. Les deux pointent par défaut sur `~/.local/share/mtrgraph/mtrgraph.db` mais si tu as fait un `run --db /tmp/x.db`, le web ne le verra pas.
**Diag** : `mtrgraph list` puis `mtrgraph web --db <mêmechemin>`.
**Fix** : harmoniser, ou définir un alias shell qui force toujours le même `--db`.

### Graphes Chart.js vides dans la page run / compare
**Cause possible** : pas de connexion internet (Chart.js est chargé via CDN jsdelivr).
**Diag** : ouvrir la console du navigateur — chercher un échec de chargement de `chart.js@4`.
**Fix** : se connecter, ou bundler Chart.js en local (télécharger `chart.umd.js` dans `mtrgraph/static/` et l'inclure depuis `base.html`).

### Daemon ne sauvegarde plus rien
**Cause** : un `mtr` qui timeout en boucle, ou un appel `Exception` qui n'est pas propre.
**Diag** : regarder la sortie console — chaque tour devrait afficher `[hh:mm:ss] OK run #N` ou un message rouge.
**Fix** : ajuster `-c` (cycles) si chaque mesure prend > intervalle, ou augmenter `--every`.

### Comparaison "tout est rouge" alors que les runs sont identiques visuellement
**Cause** : la baseline n'a qu'1-2 runs → médiane peu stable, le bruit normal du réseau dépasse le seuil.
**Diag** : `mtrgraph list --target X` → compter le nombre de runs **pour le même scope**.
**Fix** : laisser tourner le daemon plus longtemps (≥ 5-10 runs) avant de se fier à la baseline.

### "pas de baseline pour X [tcp:443]" alors que j'ai plein de runs
**Cause** : tu as des runs ICMP mais aucun TCP:443 — la baseline est scopée par `(target, proto, port)`.
**Diag** :
```bash
sqlite3 ~/.local/share/mtrgraph/mtrgraph.db \
  "SELECT target, protocol, dst_port, COUNT(*) FROM runs GROUP BY 1,2,3;"
```
**Fix** : lance d'abord plusieurs `mtrgraph run TARGET --proto tcp --port 443` pour construire la baseline du scope TCP.

### `mtr -T` (TCP) échoue : `Failure to open raw socket`
**Cause** : pas de capability `cap_net_raw` sur le binaire.
**Diag** : `mtrgraph doctor` → ligne `mtr -T (TCP)`.
**Fix** :
```bash
sudo setcap cap_net_raw+ep /usr/bin/mtr
```
Si tu ne peux pas (mtr installé en lecture seule), bascule sur UDP : `--proto udp`.

### TCP SYN bloqué par mon firewall vers ce port
**Cause** : le pare-feu local ou intermédiaire drop les SYN sortants vers le port choisi.
**Diag** : tester un port que tu sais ouvert dans ta direction (`--port 443` souvent OK).
**Fix** : changer le port ou utiliser ICMP/UDP.

### Comparaison entre un run ICMP et un run TCP — résultats bizarres
**Cause** : la latence ICMP et la latence TCP SYN ne sont pas équivalentes (handling CPU différent côté routeur, ICMP souvent depriorisé).
**Diag** : message d'avertissement "scope différent" affiché par `compare` (CLI et web).
**Fix** : compare des runs avec le même `(target, proto, port)`. Pour comparer "ce qui marche le mieux" entre protocoles, fais 2 runs séparés et lis-les côte à côte plutôt qu'un diff.

### Hops qui "apparaissent/disparaissent" d'un run à l'autre
**Cause** : load-balancing ECMP côté opérateur — chaque cycle mtr peut emprunter un chemin différent.
**Diag** : `mtr -e -n 8.8.8.8` (l'option `-e` montre les MPLS, `-n` désactive la résolution DNS).
**Fix** : aucun, c'est par design d'Internet. Augmenter le nombre de cycles (`-c 30`) lisse l'effet.

### `RuntimeError: cannot open file '.../templates/base.html'`
**Cause** : la dépendance Jinja2 ne trouve pas le dossier templates (paquet installé en zip-only, ou path bizarre).
**Diag** : `python -c "import mtrgraph, os; print(os.path.dirname(mtrgraph.__file__))"`
**Fix** : vérifier que `mtrgraph/templates/*.html` est bien à côté de `__init__.py`. Si tu packages en wheel, ajouter dans `pyproject.toml` :
```toml
[tool.setuptools.package-data]
mtrgraph = ["templates/*.html"]
```

---

## Diagnostiquer une lenteur API HTTPS / S3

Voir le [cookbook S3](cookbook-s3.md) pour la recette complète. Résumé :

1. `mtrgraph run $IP --proto tcp --port 443 -c 30` → si le hop final est OK, le réseau est innocent.
2. `mtrgraph http https://$HOST/ -c 30` → décompose DNS / TCP / TLS / TTFB.
3. Si le TTFB explose mais le reste est OK → c'est l'API qui rame (S3, ton backend, etc.), pas le réseau ni TLS.

Symptômes HTTP fréquents :

| Symptôme                                       | Cause probable                                     |
|------------------------------------------------|----------------------------------------------------|
| DNS > 100 ms                                   | Résolveur lent ou éloigné                          |
| TLS > 300 ms                                   | OCSP, TLS 1.2 forcé, certif lourd                  |
| TTFB > 1000 ms                                 | API lente, throttling S3 (vérifier `status_summary` pour 503) |
| Erreurs `tls:` dans la table samples           | TLS bloqué (firewall TLS inspection, certif KO)    |
| `status_summary` montre `err:N`                | TCP RST, timeouts — souvent middlebox/firewall     |

## Diagnostic réseau complémentaire (hors mtrgraph)

Quand `mtrgraph compare` montre une dégradation à un hop précis, ces commandes aident à comprendre :

```bash
# Qui possède l'IP ?
whois 162.158.20.40 | grep -iE "orgname|netname|country|origin"

# Numéro d'AS
dig +short -x 162.158.20.40
whois -h whois.cymru.com " -v 162.158.20.40"

# MTU sur le chemin (si suspect ICMP black hole)
tracepath 8.8.8.8

# Voir si une interface locale drop des paquets
ip -s link show

# Si pertes côté LAN
ping -f -c 1000 _gateway   # nécessite root
```

---

## TCP retransmissions

Confirme côté kernel ce que MTR voit côté réseau. Si MTR montre 5% de loss mais `tcp-stats` ne voit pas de retrans → la perte n'est probablement pas sur le chemin de tes vrais flows TCP.

```bash
# Snapshot instantané (depuis le démarrage)
mtrgraph tcp-stats --duration 0

# Sampling sur 5s (montre les retrans en cours)
mtrgraph tcp-stats --duration 5

# Avec en bonus le résumé ss
mtrgraph tcp-stats --duration 5 --ss
```

Interprétation :
- `retrans_pct < 0.1%` : nominal
- `0.1% - 1%` : un peu élevé (~10% perte d'efficacité TCP perçue)
- `> 1%` : problème actif. À corréler avec MTR.

⚠ Dans un container Docker/K8s **sans hostNetwork**, tu vois les compteurs du namespace réseau du pod, pas ceux de l'host. Pour des stats globales, utiliser `hostNetwork: true` ou un DaemonSet de monitoring dédié.

## Mode debug

Pour avoir des logs verbeux sur le daemon ou le web :

```bash
PYTHONUNBUFFERED=1 mtrgraph daemon 8.8.8.8 --every 60 -c 5 2>&1 | tee /tmp/mtrgraph.log
PYTHONUNBUFFERED=1 mtrgraph web 2>&1 | tee /tmp/mtrgraph-web.log
```

Les exceptions FastAPI sont déjà loggées par uvicorn avec stacktrace complète.

---

## Quand rien ne marche

1. Lance `mtrgraph doctor` → fixe d'abord tous les FAIL/WARN.
2. Reproduis avec une cible publique stable : `mtrgraph run 1.1.1.1 -c 5`.
3. Si ça plante → copie la stacktrace + sortie de `doctor` dans une note (`docs/session-log.md`) et ouvre un ticket de session pour reprendre.
4. Backup rapide de la DB avant de tenter quoi que ce soit de destructif :
   ```bash
   cp ~/.local/share/mtrgraph/mtrgraph.db ~/.local/share/mtrgraph/mtrgraph.db.bak
   ```

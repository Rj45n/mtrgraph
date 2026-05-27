# FAQ

### Comment diagnostiquer une lenteur sur S3 ou une API HTTPS ?
Combine `mtrgraph run --proto tcp --port 443` (couche réseau) et `mtrgraph http URL` (DNS/TCP/TLS/TTFB). Recette complète dans [cookbook-s3.md](cookbook-s3.md).

### Quelle différence entre `mtrgraph run --proto tcp --port 443` et `mtrgraph http https://...` ?
- `run --proto tcp --port 443` : mesure le **chemin réseau** vers le port (RTT, perte par hop). Ne fait pas de TLS, n'envoie pas de requête HTTP, ne mesure pas le TTFB.
- `http` : ouvre une vraie connexion HTTPS, mesure DNS + TCP + **TLS handshake** + **TTFB** + total. Te dit quelle **couche** rame.

Les deux sont complémentaires : `run` te dit *où* sur le chemin réseau ça coince, `http` te dit *quelle couche applicative* coince.

### Pourquoi mon hop 5 est rouge à 354 ms mais le hop 8 (final) est vert à 12 ms ?
Parce que la latence d'un hop **intermédiaire** mesure le temps que met *ce routeur* à renvoyer un paquet ICMP TTL-exceeded — pas le temps de transit du trafic à travers lui. Beaucoup de routeurs (Google, Cloudflare, transitaires…) depriorisent volontairement ces réponses (rate-limit anti-DoS).

Règle : **si les hops suivants sont plus rapides, le hop suspect n'est pas le problème**. Lire [reading-mtr.md](reading-mtr.md) pour la version longue.

### Pourquoi mtrgraph et pas Smokeping / Grafana ?
Smokeping est puissant mais lourd à installer pour un usage perso/petite équipe. mtrgraph tient en ~600 lignes de Python, zéro dépendance lourde, et te donne le diff comparatif `compare` qui n'existe pas tel quel ailleurs.

### Différence entre `compare A B` et `compare B --baseline` ?
- `compare A B` : diff de deux runs précis. Utile pour avant/après.
- `compare B --baseline` : diff de B contre la **médiane** des N derniers runs de la même cible. Utile pour détecter "ce run est-il dégradé par rapport à la normale".

### Pourquoi UDP par défaut et pas ICMP ?
ICMP nécessite `cap_net_raw` ou setuid root. UDP marche sans privilège et donne des résultats équivalents dans 95% des cas. Pour les 5% (firewall qui drop UDP au-delà de 33434, ou tu veux mesurer ce que voit un ping), `setcap cap_net_raw+ep /usr/bin/mtr`.

### Comment faire du MTR sur un port TCP précis ?
```bash
mtrgraph run vpn.exemple.fr --proto tcp --port 443 -c 30
```
mtr envoie des TCP SYN vers le port indiqué. Utile pour :
- traverser des firewalls qui bloquent ICMP/UDP mais laissent passer HTTPS ;
- mesurer la latence "telle qu'un client TCP la verrait" (souvent légèrement différente d'ICMP) ;
- vérifier qu'un port est réellement atteignable bout-en-bout (pas seulement que l'IP répond au ping).

Requiert `cap_net_raw` sur mtr — `mtrgraph doctor` te le dit.

### Quelle est la différence pratique entre ICMP / UDP / TCP pour mtr ?
| protocole | privilège | usage typique                                      | limite                                            |
|-----------|-----------|----------------------------------------------------|---------------------------------------------------|
| `icmp`    | cap_net_raw | "mesure réseau pure", équivalent à ping            | parfois depriorisé par les routeurs               |
| `udp`     | aucun     | défaut "safe", port 33434+ (style traceroute)      | peut être filtré par certains firewalls           |
| `tcp`     | cap_net_raw | mesure réaliste vers un port précis (443, 1194…)   | nécessite que le port soit ouvert ou ferme proprement |

### Une baseline par protocole, c'est pas redondant ?
Non — la latence et la perte mesurées dépendent du protocole. Un router peut prioriser TCP:443 et drop des paquets ICMP en cas de congestion, ou inversement. Mélanger les deux dans une baseline brouille les comparaisons.

### Combien de cycles `-c` choisir ?
- `5-10` : lecture instantanée, suffisant pour repérer un problème grossier (10-30 s d'attente).
- `30-60` : stats fiables, lisse le jitter ponctuel (30 s - 1 min).
- `300+` : analyse fine du jitter et de la stabilité, mais long.

### Le daemon va-t-il saturer mon réseau ?
Non. 10 cycles × ~30 paquets ICMP/UDP de 60 octets = ~20 ko émis par mesure. Toutes les 5 min = négligeable.

### Pourquoi pas Prometheus + Grafana ?
Si tu fais déjà du métrologique avec Prom/Grafana, c'est sûrement le bon outil. mtrgraph vise le cas "j'ai pas envie de monter une stack pour surveiller 3 chemins réseau".

### Comment changer les seuils de couleur ?
Pour l'instant : édite [mtrgraph/colors.py](../mtrgraph/colors.py) et [mtrgraph/compare.py](../mtrgraph/compare.py). Ticket [roadmap.md](roadmap.md) pour les externaliser en YAML.

### La DB peut-elle être partagée entre plusieurs machines ?
SQLite gère mal le multi-writer concurrent sur NFS. Pour un usage multi-sondes, basculer sur Postgres (changer `db.py`, le reste est agnostique).

### Comment supprimer **toutes** les données ?
```bash
rm ~/.local/share/mtrgraph/mtrgraph.db
```
Au prochain `run`, le schéma sera recréé vide.

### Comment exporter un run pour le partager ?
Pas de commande dédiée. Workaround SQL :
```bash
sqlite3 -json ~/.local/share/mtrgraph/mtrgraph.db \
  "SELECT * FROM runs WHERE id=42; SELECT * FROM hops WHERE run_id=42;" \
  > run-42.json
```

### Le web fonctionne sur 127.0.0.1, comment l'ouvrir au réseau local ?
`mtrgraph web --host 0.0.0.0`. ⚠️ Aucune authentification — utiliser un reverse proxy + auth (voir [deployment.md](deployment.md)).

### "Aucun hop ne s'affiche après le 1er" — c'est un bug ?
Non, c'est ton réseau (ou plus précisément, un routeur du chemin qui ne répond pas aux TTL-exceeded). Tester `mtr -T cible.fr` (TCP) ou `mtr -u cible.fr` (UDP) pour voir si un autre protocole passe mieux.

### Quel est l'impact disque d'un run ?
~30 lignes en `hops` + 1 ligne en `runs` ≈ 3 ko. 1000 runs = ~3 Mo. La DB reste très petite.

### Puis-je lancer plusieurs `daemon` sur la même cible ?
Possible mais inutile (et tu pollues la baseline avec des doublons). Si tu veux deux fréquences différentes pour la même cible, lance-les avec deux `--label` différents — mais la baseline les agrège quand même.

### Comment savoir si une cible a une baseline suffisante ?
```bash
sqlite3 ~/.local/share/mtrgraph/mtrgraph.db \
  "SELECT target, COUNT(*) FROM runs GROUP BY target;"
```
À partir de 5-10 runs, la médiane par hop est stable.

### Pourquoi mes timestamps sont en UTC ?
Pour la comparabilité multi-fuseaux et éviter les bugs DST. L'affichage humain (CLI + web) reste en chaîne ISO, libre à toi de convertir.

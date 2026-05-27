# Premiers pas

Walkthrough de 5 minutes pour faire le tour de mtrgraph.

## 0. Activer le venv

```bash
cd "/chemin/vers/mtr graphique"
source .venv/bin/activate
```

## 1. Vérifier l'environnement

```bash
python -m mtrgraph.cli doctor
```

Si tout est vert (ou seulement `mtr capabilities` en WARN), continue.

## 2. Premier MTR

```bash
python -m mtrgraph.cli run 1.1.1.1 -c 10 --label "premier-test"
```

Tu vois :
- une table colorée hop par hop ;
- une légende des seuils ;
- un message `✓ run #1 sauvegardé`.

Le run est dans la DB. Vérification :
```bash
python -m mtrgraph.cli list
```

## 3. Refaire un run, comparer

```bash
python -m mtrgraph.cli run 1.1.1.1 -c 10 --label "deuxieme"
python -m mtrgraph.cli compare 1 2
```

Tableau diff coloré : ΔAvg / ΔLoss / verdict (OK / lent / LATENCE++ / PERTE++).

## 4. Construire une baseline

Lance 5-10 mesures espacées (manuellement ou via daemon) :
```bash
for i in {1..8}; do
  python -m mtrgraph.cli run 1.1.1.1 -c 10 --label baseline-$i
  sleep 30
done
```

Puis compare le run le plus récent à la baseline (médiane des N derniers) :
```bash
python -m mtrgraph.cli list --target 1.1.1.1 | head -1   # repère l'ID
python -m mtrgraph.cli compare <ID> --baseline --baseline-n 8
```

## 5. Mode démon (monitoring continu)

```bash
python -m mtrgraph.cli daemon 1.1.1.1 --every 300 -c 10
```

Chaque 5 min : une mesure, comparaison à la baseline. Sortie :
- `[hh:mm:ss] OK run #N` (vert) si rien d'anormal,
- `[hh:mm:ss] DEGRADATION run #N` (rouge gras) + détail par hop si la baseline est dépassée.

`Ctrl+C` pour arrêter.

## 6. Interface web

```bash
python -m mtrgraph.cli web
# http://127.0.0.1:8765
```

Pages :
- `/` — liste de tous les runs + filtre par cible + graphe historique destination.
- `/run/{id}` — détail d'un run : table colorée + graphes (best/avg/worst, perte par hop).
- `/compare?a=X&b=Y` — diff visuel avec courbes superposées.

## 7. Nettoyage

```bash
python -m mtrgraph.cli list                   # voir ce qu'il y a
python -m mtrgraph.cli delete 3               # supprime un run
sqlite3 ~/.local/share/mtrgraph/mtrgraph.db \
  "DELETE FROM runs WHERE started_at < datetime('now', '-30 days');"
```

---

## Patterns utiles

### Avant/après un changement réseau
```bash
mtrgraph run vpn.exemple.fr --label "avant-mtu1500"
# ... modification ...
mtrgraph run vpn.exemple.fr --label "apres-mtu1500"
mtrgraph compare <id_avant> <id_apres>
```

### Surveillance multi-cibles en parallèle
Un daemon par cible (utilise des `--db` séparés pour éviter la contention, ou laisse SQLite gérer) :
```bash
mtrgraph daemon 8.8.8.8     --every 300 --label "google" &
mtrgraph daemon 1.1.1.1     --every 300 --label "cloudflare" &
mtrgraph daemon vpn.corp.fr --every 600 --label "vpn" &
```

### Export pour analyse externe
Pas de commande dédiée pour l'instant — utiliser SQLite directement :
```bash
sqlite3 -header -csv ~/.local/share/mtrgraph/mtrgraph.db \
  "SELECT r.target, r.started_at, h.* FROM hops h JOIN runs r ON r.id=h.run_id" \
  > /tmp/mtrgraph-export.csv
```

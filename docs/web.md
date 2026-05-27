# Interface web

Lancée par `python3 -m mtrgraph.cli web` (défaut `http://127.0.0.1:8765`).

## Pages

### `GET /`
- Liste paginée des runs (filtrable par cible via `?target=`).
- Si une cible est sélectionnée : graphe historique latence + perte de la destination.
- Formulaire pour lancer une comparaison (sélection A/B).

### `GET /run/{id}`
- Métadonnées du run (cible, date, source, label).
- Graphe ligne **Best / Avg / Worst** par hop.
- Graphe barres **perte %** par hop (couleur selon seuil).
- Tableau coloré + barres de latence.

### `GET /compare?a={id}&b={id}`
- En-tête A vs B.
- Graphe ligne `Avg A` vs `Avg B`.
- Tableau diff hop par hop avec ΔAvg, ΔLoss, verdict (`OK` / `WARNING` / `CRITICAL`).

## API JSON

### `GET /api/target/{target}/history?limit=50`
Retourne `[{run_id, started_at, hops_count, dst_avg_ms, dst_loss_pct}, …]` (ancien → récent), utilisé par le graphe historique.

### `GET /api/run/{id}`
Retourne `{run: {...}, hops: [...]}` (toutes les colonnes brutes).

## Templates

Fichiers dans `mtrgraph/templates/` :
- `base.html` — layout dark, header, CSS, chargement Chart.js (CDN jsdelivr).
- `index.html` — listing + filtre + graphe historique.
- `run.html` — détail d'un run + 2 charts.
- `compare.html` — diff + chart comparatif.

## Couleurs

Synchronisées entre TUI et web via `mtrgraph/colors.py` :
- `loss_hex(loss)` et `latency_hex(avg)` côté HTML.
- `loss_color(loss)`, `latency_color(avg)`, `jitter_color(stddev)` côté Rich.

Seuils :
| métrique | vert     | jaune     | rouge      | rouge foncé |
|----------|----------|-----------|------------|-------------|
| Loss%    | <1       | 1-5       | 5-10       | ≥10         |
| Latence  | <50 ms   | 50-100    | 100-200    | ≥200        |
| Jitter   | <5 ms    | 5-20      | 20-50      | ≥50         |

## Sécurité

- Pas d'authentification.
- Écoute par défaut sur `127.0.0.1` — ne pas exposer publiquement sans reverse proxy + auth.

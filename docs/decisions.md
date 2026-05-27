# Décisions techniques

## Pourquoi `mtr -j` (subprocess) plutôt qu'une lib ICMP Python ?
- Pas besoin de root (mtr peut faire UDP par défaut).
- Délégation totale du calcul de stats (Avg, Best, StDev) au binaire éprouvé.
- Sortie JSON stable depuis mtr 0.86+ (testé sur 0.95).
- Inconvénient : impossible d'avoir un *streaming* hop-par-hop pendant l'exécution → on attend la fin du `mtr -c N` avant d'afficher. Acceptable pour N ≤ 30 cycles (~30 s).

## Pourquoi SQLite ?
- Zéro install/serveur, idéal pour un outil local.
- Suffisant à plusieurs centaines de milliers de hops.
- Index simples sur `(target, started_at)`.
- Si besoin > 1M lignes ou multi-host → migrer vers Postgres (changer `db.py` uniquement, le reste est agnostique).

## Pourquoi Rich (et pas Textual) ?
- Rich `Table` colorée suffit pour l'usage "lance mtr, affiche le résultat".
- Textual aurait apporté un vrai TUI interactif (sélection clavier, panneaux) mais est plus lourd et l'utilisateur peut déjà tout faire en CLI + web.
- Évolution possible : ajouter un `mtrgraph tui` interactif basé sur Textual sans toucher au reste.

## Pourquoi FastAPI + Chart.js et pas un dashboard tout fait (Grafana) ?
- Aucune dépendance externe en plus de Python.
- Chart.js via CDN → zéro bundler, zéro `npm install`.
- Pour un usage perso/petite équipe c'est suffisant ; pour de la métrologie longue durée multi-cibles, exporter vers Prometheus/Grafana serait plus pertinent.

## Pourquoi une baseline = médiane ?
- Robuste aux outliers (un cycle dégradé n'écrase pas la baseline).
- Calculée en Python (SQLite n'a pas de fonction MEDIAN native, ajouter une dépendance pour ça n'en vaut pas la peine).
- Fenêtre glissante des N derniers runs → la baseline suit doucement les changements lents (déménagement, changement d'opérateur).

## Pourquoi des seuils en dur (pas configurables) au premier jet ?
- Évite un fichier de config pour le MVP.
- Les seuils sont dans `colors.py` et `compare.py` (constantes), facile à externaliser plus tard via YAML/TOML.

## Permissions mtr
- Par défaut mtr utilise UDP (port 33434+) → aucun privilège requis.
- Pour ICMP : `sudo setcap cap_net_raw+ep /usr/bin/mtr` (capability persistante, pas besoin de sudo à chaque appel).
- Le wrapper `probe.run_mtr` ne force pas d'option `-u`/`-T`/`-I` → utilise le mode par défaut du binaire.

## Format des timestamps
- Tout en ISO-8601 UTC pour comparabilité.
- Affichage CLI/web : tronqué à la minute pour la lisibilité.

## Non-objectifs explicites
- Pas d'alerting externe (email/Slack/PagerDuty) au MVP — sortie console suffit pour un usage interactif. À ajouter si besoin via un hook `on_degraded`.
- Pas d'auth sur le web → écoute par défaut sur 127.0.0.1. Ne PAS exposer publiquement en l'état.
- Pas de multi-utilisateur — une seule DB locale.

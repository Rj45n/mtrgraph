# Roadmap / pistes d'amélioration

Ordonnée par valeur perçue. Aucune n'est requise pour le MVP.

## Court terme
- [ ] Config externe (`~/.config/mtrgraph/config.toml`) pour : seuils couleur, taille baseline, cibles préchargées.
- [ ] `mtrgraph export RUN_ID --format json|csv` pour partager un run.
- [ ] `mtrgraph purge --older 30d` pour nettoyer la DB.
- [ ] Capture du PID dans le mode daemon + fichier de lock pour éviter double-instance par cible.

## Moyen terme
- [ ] Mode TUI interactif (Textual) : sélection clavier d'un run, drill-down hop, diff visuel temps réel.
- [ ] Webhook/notification sur dégradation (Slack incoming webhook, email SMTP).
- [ ] Page web "dashboard" multi-cibles avec status feu tricolore.
- [ ] Export Prometheus (`/metrics`) pour intégration Grafana.
- [ ] Tags multiples par run (au lieu d'un seul `label`).

## Long terme
- [ ] Détection automatique de la "frontière" : à partir de quel hop on entre dans un AS donné (whois/asn lookup, cache local).
- [ ] Corrélation multi-cibles : "tous les chemins passant par X se dégradent depuis 10:42".
- [ ] Stockage longue durée optimisé : downsampling (1 run/h après 7j, 1/jour après 30j).
- [ ] Backend Postgres optionnel pour usage multi-host (plusieurs sondes envoient vers une DB centrale).

## Idées non priorisées
- [ ] Mode "rapport PDF" pour audit ponctuel.
- [ ] Comparaison de chemins (`mtr A→B` vs `mtr A→C` pour repérer le tronc commun).
- [ ] Geo-IP des hops (carte Leaflet dans le web).

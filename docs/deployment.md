# Déploiement

Cibles : faire tourner le `daemon` ou le `web` en arrière-plan de façon robuste (relance auto, logs, sécurité).

> Voir aussi [retention.md](retention.md) pour la gestion de la croissance DB long terme.

> Pour un déploiement **Kubernetes** (cas où tu veux probe depuis l'intérieur d'un cluster), voir [kubernetes.md](kubernetes.md). Cette page couvre uniquement le déploiement systemd sur un serveur Linux.

## systemd : daemon de monitoring

Fichier `/etc/systemd/system/mtrgraph-daemon@.service` :

```ini
[Unit]
Description=mtrgraph monitoring daemon (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mtrgraph
Group=mtrgraph
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/mtrgraph/.venv/bin/python -m mtrgraph.cli daemon %i --every 300 -c 10 --db /var/lib/mtrgraph/mtrgraph.db
Restart=on-failure
RestartSec=15

# durcissement raisonnable
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/mtrgraph
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Setup :
```bash
sudo useradd -r -s /usr/sbin/nologin mtrgraph
sudo mkdir -p /var/lib/mtrgraph && sudo chown mtrgraph:mtrgraph /var/lib/mtrgraph
sudo cp -r "/chemin/vers/mtr graphique" /opt/mtrgraph
sudo chown -R mtrgraph:mtrgraph /opt/mtrgraph
sudo setcap cap_net_raw+ep /usr/bin/mtr     # pour ICMP (sinon UDP marche très bien)
sudo systemctl daemon-reload
sudo systemctl enable --now mtrgraph-daemon@1.1.1.1.service
sudo systemctl enable --now mtrgraph-daemon@8.8.8.8.service
journalctl -u 'mtrgraph-daemon@*' -f
```

`%i` est remplacé par le nom de l'instance (ici la cible mtr). Lance autant d'instances que de cibles à surveiller.

## systemd : interface web

`/etc/systemd/system/mtrgraph-web.service` :

```ini
[Unit]
Description=mtrgraph web UI
After=network-online.target

[Service]
Type=simple
User=mtrgraph
Group=mtrgraph
ExecStart=/opt/mtrgraph/.venv/bin/python -m mtrgraph.cli web --host 127.0.0.1 --port 8765 --db /var/lib/mtrgraph/mtrgraph.db
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths=/var/lib/mtrgraph

[Install]
WantedBy=multi-user.target
```

Activation :
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mtrgraph-web.service
```

Note : `ReadOnlyPaths` car le web ne fait que lire. Si tu ajoutes une feature qui écrit, basculer en `ReadWritePaths`.

## Reverse proxy avec authentification

Le web n'a **aucune auth** par défaut et écoute en `127.0.0.1`. Pour l'exposer, mettre un reverse proxy devant.

### nginx + Basic Auth

```nginx
server {
    listen 443 ssl http2;
    server_name mtrgraph.example.com;
    ssl_certificate     /etc/letsencrypt/live/mtrgraph.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mtrgraph.example.com/privkey.pem;

    auth_basic           "mtrgraph";
    auth_basic_user_file /etc/nginx/mtrgraph.htpasswd;

    location / {
        proxy_pass         http://127.0.0.1:8765;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Génération du fichier de mot de passe :
```bash
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/mtrgraph.htpasswd romain
```

### Caddy (plus simple, TLS auto)

```caddy
mtrgraph.example.com {
    basicauth {
        romain $2a$14$...   # hash bcrypt généré par `caddy hash-password`
    }
    reverse_proxy 127.0.0.1:8765
}
```

## Sauvegardes de la DB

SQLite est un simple fichier — sauvegarde = copie. Snapshot cohérent (pendant que le daemon écrit) :

```bash
sqlite3 /var/lib/mtrgraph/mtrgraph.db ".backup /backup/mtrgraph-$(date +%F).db"
```

Cron quotidien :
```cron
30 3 * * * sqlite3 /var/lib/mtrgraph/mtrgraph.db ".backup /backup/mtrgraph-$(date +\%F).db"
0 4 * * 0 find /backup -name 'mtrgraph-*.db' -mtime +30 -delete
```

## Purge de l'historique

```bash
sqlite3 /var/lib/mtrgraph/mtrgraph.db \
  "DELETE FROM runs WHERE started_at < datetime('now', '-90 days'); VACUUM;"
```

À mettre dans un cron mensuel si la DB grossit (chaque run = ~30 lignes ≈ 3 ko, donc ~1 Go pour 300 000 runs).

## Mise à jour en prod

```bash
sudo systemctl stop 'mtrgraph-daemon@*' mtrgraph-web
cd /opt/mtrgraph
sudo -u mtrgraph git pull
sudo -u mtrgraph .venv/bin/pip install -r requirements.txt --upgrade
sudo -u mtrgraph .venv/bin/python -m mtrgraph.cli doctor --db /var/lib/mtrgraph/mtrgraph.db
sudo systemctl start mtrgraph-web 'mtrgraph-daemon@*'
```

# Déployer mtrgraph dans Kubernetes

Cas d'usage typique : tu as une lenteur intermittente vers une API/S3 (AWS, MinIO, Scaleway, OVH, Free Pro, etc.) **depuis tes pods K8s**, et tu veux mesurer **exactement ce qu'ils voient** plutôt que ce que voit ton poste.

> mtrgraph n'est lié à aucun fournisseur S3 en particulier — toute URL HTTPS marche. Free Pro est cité dans les exemples pour des raisons de tests internes, **remplace par ton URL** dans tous les snippets ci-dessous.

La stack proposée :
- 1 **Deployment** `http-daemon` qui probe en continu une URL et alerte si dégradation vs baseline ;
- (option) 1 **Deployment** `mtr-daemon` qui fait du MTR L3/L4 ;
- 1 **Deployment** `web` qui sert l'UI sur `:8765` (ClusterIP par défaut) ;
- 1 **PVC** SQLite partagé entre les daemons et le web.

```
┌────────────────────┐    ┌─────────────────┐    ┌──────────────┐
│ http-daemon (pod)  │───▶│  PVC SQLite     │◀───│  web (pod)   │
│ probe S3 en boucle │    │ /data/mtr.db    │    │  port 8765   │
└────────────────────┘    └─────────────────┘    └──────────────┘
         │                                              ▲
         ▼ alertes stdout                               │
   journalctl/kubectl logs                       kubectl port-forward
                                                 ou Ingress + auth
```

## 1. Pré-requis

- Cluster K8s 1.24+.
- Une `StorageClass` capable de provisioner du `ReadWriteOnce` (la plupart).
- Un registry image accessible depuis le cluster (privé ou public).
- `kubectl` et (idéalement) `kustomize`.

## 2. Builder l'image

Depuis la racine du projet :

```bash
# Build local
docker build -t mtrgraph:0.1.0 .

# Test rapide en local (doctor doit passer)
docker run --rm mtrgraph:0.1.0 doctor --db /tmp/test.db

# Push vers ton registry
docker tag mtrgraph:0.1.0 registry.example.com/infra/mtrgraph:0.1.0
docker push registry.example.com/infra/mtrgraph:0.1.0
```

L'image est basée sur `python:3.12-slim` + `mtr-tiny` + les deps Python. ~150 MB.

> **Note ICMP/TCP** : l'image fonctionne sans privilège pour le `http-daemon` (Python pur sockets) et pour `mtr --proto udp`. Pour ICMP/TCP SYN, ajouter `CAP_NET_RAW` dans le pod (voir [deployment-mtr-daemon.yaml](../k8s/deployment-mtr-daemon.yaml)).

## 3. Adapter la config

Édite [k8s/configmap.yaml](../k8s/configmap.yaml) avec **ton** endpoint S3 (n'importe quel S3-compatible : AWS, MinIO, Scaleway, OVH, Cellar, Free Pro, …) ou n'importe quelle URL HTTPS :

```yaml
data:
  TARGET_URL: "https://s3.eu-west-3.amazonaws.com/"   # ← TON URL
  MTR_TARGET: "52.219.170.123"                        # ← TON IP cible (résolue depuis TARGET_URL)
  MTR_PROTO: "tcp"
  MTR_PORT: "443"
  MTR_CYCLES: "10"
  INTERVAL: "60"
  COUNT: "5"
  BASELINE_N: "10"
  METHOD: "HEAD"
  ERROR_THRESHOLD: "10"
```

Pour obtenir `MTR_TARGET` :
```bash
dig +short s3.eu-west-3.amazonaws.com | head -1
```

Édite [k8s/kustomization.yaml](../k8s/kustomization.yaml) :

```yaml
images:
  - name: mtrgraph
    newName: registry.example.com/infra/mtrgraph
    newTag: "0.1.0"
```

## 4. Déployer

```bash
kubectl apply -k k8s/

# Vérifier que tout démarre
kubectl -n mtrgraph get pods -w

# Logs du daemon HTTP
kubectl -n mtrgraph logs -l role=http-daemon -f

# Logs du daemon MTR (si activé)
kubectl -n mtrgraph logs -l role=mtr-daemon -f
```

Sortie attendue du daemon HTTP :
```
HTTP daemon https://s3.fr-mar.freepro.com/ every 60s, 5 samples, HEAD, baseline last 10 runs
[14:37:11] baseline run #1 stored  total=52 ms  status=200:5
[14:38:12] OK http_run #2 total=58 ms  status=200:5
[14:39:13] OK http_run #3 total=61 ms  status=200:5
[14:40:14] DEGRADATION http_run #4 https://... → TTFB warning 9→145 ms  status=200:5
```

## 5. Accéder à l'interface web

### Port-forward (rapide, depuis ton poste)

```bash
kubectl -n mtrgraph port-forward svc/mtrgraph-web 8765:80
# ouvre http://localhost:8765
```

### Ingress (exposé, ⚠ ajouter de l'auth)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: mtrgraph-web
  namespace: mtrgraph
  annotations:
    nginx.ingress.kubernetes.io/auth-type: basic
    nginx.ingress.kubernetes.io/auth-secret: mtrgraph/web-basic-auth
spec:
  rules:
    - host: mtrgraph.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: mtrgraph-web
                port:
                  number: 80
  tls:
    - hosts: [mtrgraph.example.com]
      secretName: mtrgraph-tls
```

Le service web **n'a aucune authentification native**. Ne jamais l'exposer sans une auth devant.

## 6. Surveiller plusieurs URLs

Une instance de Deployment = une URL surveillée. Pour plusieurs cibles :

```bash
# Duplique le ConfigMap et le Deployment, change le name + TARGET_URL
cp k8s/configmap.yaml k8s/configmap-rds.yaml
cp k8s/deployment-http-daemon.yaml k8s/deployment-http-daemon-rds.yaml
# … édite name/configMapRef … puis ajoute au kustomization.yaml
```

Ou plus propre : génère via un Helm chart (laissé en exercice).

## 7. Diagnostiquer avec les données collectées

Connecte-toi à l'UI web ou en CLI dans un pod éphémère :

```bash
# Lister les http_runs depuis l'intérieur du cluster
kubectl -n mtrgraph exec -it deploy/mtrgraph-web -- python -m mtrgraph.cli http-list --db /data/mtrgraph.db

# Voir un run précis (timings DNS/TCP/TLS/TTFB par sample)
kubectl -n mtrgraph exec -it deploy/mtrgraph-web -- python -m mtrgraph.cli http-show 42 --db /data/mtrgraph.db

# Vérifier l'environnement du pod (DNS, HTTPS, etc.)
kubectl -n mtrgraph exec -it deploy/mtrgraph-web -- python -m mtrgraph.cli doctor --db /data/mtrgraph.db
```

## 8. Sauvegarde de la DB

SQLite = un fichier. Sauvegarde à chaud :

```bash
kubectl -n mtrgraph exec deploy/mtrgraph-web -- \
  sqlite3 /data/mtrgraph.db ".backup /data/backup-$(date +%F).db"

# Récupérer en local
kubectl -n mtrgraph cp mtrgraph-web-XXXX:/data/backup-2026-05-26.db ./backup.db
```

À cron-ner via un `CronJob` k8s qui monte le même PVC en lecture, fait le backup, et envoie le résultat dans un stockage externe (S3 — oui, on boucle 🙃).

## 9. Limites de SQLite en K8s

- **Un seul writer à la fois** : `strategy: Recreate` partout sur les Deployments. Pas de rolling update.
- **PVC RWO** : tous les pods qui montent la DB doivent être sur le **même nœud**. K8s schedule automatiquement, mais si tu veux deux daemons en parallèle, il faut soit pinner les nodes soit basculer sur une DB partagée (Postgres → roadmap).
- **Lecture seule sur le web** : déjà géré (`ReadOnlyPaths` n'est pas appliqué côté SQLite, mais le web ne fait que des SELECT).

## 10. Surveiller les alertes

Le daemon écrit sur stdout. Pour l'intégrer à ton monitoring :

```bash
# Tail vers Loki/Promtail (déjà OK si tu collectes les logs K8s)
kubectl -n mtrgraph logs -l role=http-daemon -f | grep DEGRADATION

# Alertmanager : exposer un /metrics depuis mtrgraph est sur la roadmap.
# Pour l'instant, alerte sur la présence de "DEGRADATION" dans les logs
# (LogQL: {namespace="mtrgraph",role="http-daemon"} |= "DEGRADATION")
```

## 11. Désinstaller

```bash
kubectl delete -k k8s/
# La PVC est conservée par défaut. Pour effacer aussi les données :
kubectl -n mtrgraph delete pvc mtrgraph-data
```

## Troubleshooting K8s

| Symptôme                                          | Cause/Fix                                                  |
|---------------------------------------------------|------------------------------------------------------------|
| Pod CrashLoopBackOff, `permission denied: /data`  | `fsGroup` non appliqué — vérifier la `StorageClass`        |
| Pod `Pending` indéfiniment                        | PVC pas provisionné — `kubectl describe pvc mtrgraph-data` |
| Erreurs DNS dans les logs (`gaierror`)            | CoreDNS dans le namespace ne résout pas externe — vérifier `kubectl run -it --rm dns-test --image=busybox -- nslookup s3.fr-mar.freepro.com` |
| Daemon log : `tls: HANDSHAKE_FAILURE`             | Inspection TLS du firewall pod-egress — voir avec l'équipe réseau |
| `errors=N` élevé dans les http_runs               | Vérifier `status_summary` : 5xx = backend, autres = réseau |
| Liveness probe redémarre le pod sans cesse        | `INTERVAL` trop court par rapport au temps de probe — augmenter |

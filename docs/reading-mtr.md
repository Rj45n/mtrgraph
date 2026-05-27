# Lire un résultat MTR sans se faire avoir

Guide d'interprétation. À garder sous la main avant de paniquer sur un hop rouge.

---

## La règle d'or

> **La latence/perte d'un hop intermédiaire ne mesure PAS ce que subit ton trafic.**
> Elle mesure le temps que met **ce routeur** à renvoyer un paquet *TTL-exceeded* vers toi.

Conséquence pratique :

> **Si les hops suivants sont plus rapides que le hop suspect, le hop suspect n'est pas le problème.**

C'est le piège #1 de la lecture mtr. Tant que tu as ça en tête, tu évites 90% des faux diagnostics.

---

## Comment lire le tableau, dans l'ordre

1. **Regarde le DERNIER hop d'abord.** C'est lui qui dit la vérité sur ton expérience utilisateur :
   - Avg = latence réelle vers la destination.
   - Loss = perte réelle que vit ton trafic.
   - StDev = stabilité.
2. **Cherche un escalier ascendant.** Une latence qui *monte* à partir d'un hop **et reste haute** sur tous les suivants = vrai goulot. Une bosse isolée = artefact.
3. **Cherche une perte persistante.** Si la perte apparaît à hop X **et reste ≥ ce niveau** sur les hops suivants, c'est une vraie perte. Sinon (perte sur hop X, 0% sur X+1) = artefact de rate-limiting.
4. **Le `Bar`** : c'est une barre relative au max de la table. Un seul hop énorme va saturer la barre, ça ne veut rien dire dans l'absolu. Toujours lire les chiffres.

---

## Cas d'école : `www.google.fr` en TCP:443

```
 # │ Host                               │ Loss% │ Avg   │ Wrst   │ StDev
 1 │ _gateway                           │  0.0% │   2.4 │    5.3 │   0.8
 2 │ 85.31.218.61                       │  0.0% │   3.7 │    6.9 │   1.0
 3 │ te0-0-2-1-54...jaguar-network.net  │  0.0% │   4.5 │    8.0 │   1.2
 4 │ be1.er03.mar01.jaguar-network.net  │  0.0% │   6.6 │   12.3 │   1.3
 5 │ 72.14.209.69                       │  0.0% │ 354.1 │ 2034.6 │ 556.3   ← 😱
 6 │ 192.178.105.91                     │  0.0% │   7.8 │   36.9 │   5.6
 7 │ 142.251.78.83                      │  0.0% │  10.2 │   36.3 │  10.0
 8 │ ncmrsa-aq-in-f3.1e100.net          │  0.0% │  11.9 │   44.5 │  11.8
```

**Lecture en 3 secondes** :
- Hop 8 (final) : 11.9 ms, 0% perte. **La connexion vers Google est nickel.**
- Hop 5 affiche 354 ms… mais hop 6 retombe à 7.8 ms.
- Si hop 5 ajoutait vraiment 354 ms de latence, les hops 6-8 seraient à **>360 ms**. Ils sont à <15 ms.
- Donc le trafic *traverse* hop 5 en quelques ms ; seul le **paquet de réponse** est lent à être généré.

**Verdict** : faux positif, c'est du *control plane rate-limiting* côté routeur Google. Voir section suivante.

---

## Pourquoi les routeurs intermédiaires font ça

| Plan                | Hardware           | Rôle                                    |
|---------------------|--------------------|-----------------------------------------|
| **Forwarding plane**| ASIC               | Achemine le trafic utile à la vitesse de la fibre |
| **Control plane**   | CPU généraliste    | Génère les ICMP TTL-exceeded, traite BGP, OSPF, etc. |

Le control plane est **délibérément depriorisé** :
- Génération d'ICMP-error rate-limitée (RFC 1812 §4.3.2.8, généralement 1-2 par seconde par destination).
- Sur les routeurs cœur, la priorité du CPU va à BGP/IS-IS/etc., pas à répondre aux traceroutes.
- Protection anti-DoS : si on inonde un routeur de paquets piégés, il ne s'effondre pas.

Conséquence : **n'importe quel routeur sur le chemin peut afficher 100-500 ms ou 50% de perte en mtr alors qu'il fait passer ton trafic parfaitement**.

Ça concerne particulièrement :
- Les routeurs Google (`1e100.net`, `*.google.com`)
- Cloudflare
- Les transitaires de tier-1 (Level3, Cogent, Telia, GTT…)
- Les firewalls/load-balancers d'entreprise au milieu du chemin

---

## Vrais signaux d'alerte

### Latence en escalier qui persiste

```
hop 1   1 ms
hop 2   2 ms
hop 3   3 ms
hop 4  85 ms   ← saut
hop 5  87 ms   ← reste haut
hop 6  90 ms   ← reste haut
hop 7  92 ms (final)
```
→ Vrai goulot entre hop 3 et hop 4. À investiguer (changement d'opérateur, congestion).

### Perte qui persiste

```
hop 1   0.0%
hop 2   0.0%
hop 3   3.0%   ← apparaît
hop 4   3.5%
hop 5   3.0%   ← reste
hop 6   4.0% (final)
```
→ Vraie perte sur le chemin à partir de hop 3. La perte au hop final est ce qui compte pour l'application.

### Jitter élevé sur le dernier hop

```
hop final  Avg 80 ms  StDev 60 ms
```
→ Instabilité réelle. VoIP/visio vont en souffrir. Skype, Teams, gaming = inutilisable.

### Loss sur le dernier hop > 1%

→ TCP va retransmettre, débit utile s'effondre. Symptôme typique : "ça rame" mais le ping passe.

---

## Faux signaux courants

| Apparence                                  | Explication                                                                 |
|--------------------------------------------|-----------------------------------------------------------------------------|
| Hop X = 300 ms, hop X+1 = 5 ms             | Control plane rate-limit. Pas un problème.                                  |
| Hop X = 50% loss, hop X+1 = 0% loss        | Idem. Le routeur drop les ICMP-replies, pas le trafic.                      |
| `???` à un hop avant la fin                | Routeur qui ne génère pas d'ICMP-error du tout. Si la suite traverse, OK.   |
| Hop intermédiaire avec Worst = 2000 ms     | Un pic CPU côté routeur. Ignorer si Avg reste bas.                          |
| Hops qui changent d'un run à l'autre       | ECMP : load-balancing côté opérateur. Augmenter `-c` pour lisser.           |
| Latence asymétrique (aller ≠ retour)       | Normal, mtr mesure aller+retour. On ne sait pas qui est lent des deux sens. |

---

## Commandes pour creuser

```bash
# Refaire avec ICMP pour comparer (si la dégradation n'apparaît qu'en TCP, c'est rate-limit TCP-spécifique)
mtrgraph run TARGET --proto icmp -c 30
mtrgraph compare <id_icmp> <id_tcp>

# Plus de cycles pour lisser le jitter
mtrgraph run TARGET -c 100

# Voir les MPLS et désactiver le DNS (sortie plus brute)
mtr -e -n TARGET

# Tester si l'asymétrie est aller ou retour : ping depuis l'autre côté si possible
# (lancer mtr depuis le serveur cible vers toi)

# Identifier l'opérateur d'un hop
whois 72.14.209.69 | grep -iE "orgname|netname|country"

# Numéro d'AS et nom
whois -h whois.cymru.com " -v 72.14.209.69"
```

---

## Workflow "ce chemin est-il dégradé ?"

1. `mtrgraph run TARGET -c 30 --label "now"` — sauvegarde un run.
2. `mtrgraph compare <id> --baseline` — diff vs la médiane historique du **même scope**.
3. Si verdict OK partout → rien à signaler.
4. Si verdict `LATENCE++` ou `PERTE++` sur le **dernier hop** → vrai souci, à investiguer.
5. Si verdict rouge sur un hop intermédiaire **mais OK sur le dernier hop** → faux positif. Vérifier que c'est cohérent : `compare A B` à la main et lire la cascade.

---

## Pour aller plus loin

- RFC 1812 §4.3.2.8 — rate-limiting des ICMP-errors.
- RFC 1393 — Traceroute Using an IP Option.
- Article classique : ["MTR — Best Current Practice"](https://www.kernel.org/doc/man-pages/) (page man mtr suffit en pratique).
- Article Cisco : *"Understanding ICMP Generation Rate"* (motive le rate-limit côté constructeurs).

---

## TL;DR encadré

> **Regarde le dernier hop. C'est lui qui compte.**
> **Un hop intermédiaire "rouge" suivi de hops "verts" est un faux positif.**
> **La latence/perte n'est un vrai problème que si elle PERSISTE jusqu'à la fin du chemin.**

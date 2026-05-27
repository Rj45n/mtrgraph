# Installation

## Prérequis système

| logiciel | version | rôle                               |
|----------|---------|------------------------------------|
| Linux    | n'importe quel kernel récent | testé Ubuntu 24.04 / kernel 6.8 |
| `mtr`    | ≥ 0.86  | binaire de sondage (fournit `-j` JSON) |
| Python   | ≥ 3.10  | runtime mtrgraph (testé 3.12)      |
| `pip`    | —       | installation des deps              |
| `getcap` | —       | (optionnel) vérif des capabilities — paquet `libcap2-bin` |

### Debian / Ubuntu
```bash
sudo apt update
sudo apt install -y mtr python3-venv python3-pip libcap2-bin
```

### Fedora / RHEL
```bash
sudo dnf install -y mtr python3 python3-pip libcap
```

### Arch
```bash
sudo pacman -S --needed mtr python python-pip
```

---

## Installer mtrgraph

### Méthode standard (venv local)
```bash
cd /chemin/vers/mtr\ graphique
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m mtrgraph.cli doctor
```

La dernière commande lance le diagnostic — tout doit être vert sauf éventuellement `mtr capabilities` (warn par défaut).

### Méthode "système" (sans venv)
Déconseillé sur les distros récentes (PEP 668). Si tu y tiens :
```bash
pip install --user --break-system-packages -r requirements.txt
```

### Wrapper shell global
Pour appeler `mtrgraph` au lieu de `python -m mtrgraph.cli` :
```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/mtrgraph <<'EOF'
#!/usr/bin/env bash
exec "/chemin/vers/mtr graphique/.venv/bin/python" -m mtrgraph.cli "$@"
EOF
chmod +x ~/.local/bin/mtrgraph
```
Assure-toi que `~/.local/bin` est dans ton `$PATH`.

---

## Permissions ICMP (optionnel mais recommandé)

Par défaut mtr utilise UDP (aucun privilège). Pour ICMP — plus représentatif de ce que voient les utilisateurs finaux :

```bash
sudo setcap cap_net_raw+ep /usr/bin/mtr
```

Vérif :
```bash
getcap /usr/bin/mtr
# /usr/bin/mtr cap_net_raw=ep
python -m mtrgraph.cli doctor
# mtr capabilities  OK   cap_net_raw=ep
```

⚠️ `setcap` est perdu après chaque `apt upgrade mtr`. À ré-appliquer si la doctor repasse en WARN.

---

## Emplacement de la base de données

Par défaut : `~/.local/share/mtrgraph/mtrgraph.db` (créé au premier `run`).

Override possible :
```bash
mtrgraph run --db /chemin/perso/mtr.db 8.8.8.8
```

Pour fixer un chemin durable, alias :
```bash
echo 'alias mtrgraph="mtrgraph --db /var/lib/mtrgraph.db"' >> ~/.bashrc
```
(attention, `--db` est positionné après la sous-commande, donc ce hack ne marche que partiellement — préférer un wrapper script).

---

## Mise à jour

```bash
cd /chemin/vers/mtr\ graphique
git pull       # si versionné
source .venv/bin/activate
pip install -r requirements.txt --upgrade
python -m mtrgraph.cli doctor
```

Schéma DB : pas de migrations. Si une colonne est ajoutée, soit `init_db` la crée idempotemment (à coder), soit on supprime la DB locale.

---

## Désinstallation

```bash
rm -rf .venv                                # vire le venv
rm -rf ~/.local/share/mtrgraph              # vire la DB (irréversible)
rm -f ~/.local/bin/mtrgraph                 # vire le wrapper
sudo setcap -r /usr/bin/mtr 2>/dev/null     # retire les capabilities
```

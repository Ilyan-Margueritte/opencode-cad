# opencode-cad : de la 3D complexe avec des modèles open source

Plugin OpenCode (**API V2**) qui donne à un LLM (GLM, Qwen, DeepSeek, etc.) une vraie boucle d'ingénierie CAO : il écrit du **CadQuery** paramétrique, l'exécute, lit un rapport de validation précis, regarde des rendus multi-vues et corrige jusqu'à ce que la pièce soit valide et conforme.

## Ce que contient le plugin

| Élément | Rôle |
|---|---|
| `cad_run` | Exécute le script, valide la géométrie, compare à `EXPECT` / `PARTS_EXPECT`, refuse toute pièce plus grande que le plateau, exporte STEP + STL (un jeu par pièce), rend des vues PNG et une coupe optionnelle |
| `cad_env` | Vérifie / installe Python + CadQuery dans un `.venv` local |
| `cad_pitfalls` | Aide-mémoire des pièges CadQuery (plans, sélecteurs, congés, shell, révolution, booléens, découpe d'impression…), filtrable par sujet |
| `cad_example` | 3 scripts de référence qui tournent : boîtier 2 pièces (base + couvercle), équerre avec trous, poulie révolutionnée |
| agent `cad` | Ingénieur CAO : spec, plan, construction incrémentale, vérification, livraison |
| agent `cad-critic` | Sous-agent relecteur : rapport + vues + code, renvoie une liste de défauts avec corrections |

## Arborescence

```
.opencode/
├── plugins/opencode-cad/       le plugin (dossier-paquet auto-découvert)
│   ├── index.ts                outils + logique d'exécution
│   ├── package.json
│   └── cad/
│       ├── cad_runner.py       exécution + validation + rendus
│       ├── pitfalls.md         aide-mémoire
│       ├── requirements.txt
│       └── examples/           scripts de référence
└── agents/
    ├── cad.md
    └── cad-critic.md

tests/
└── test_cad_runner.py          tests de bout en bout du runner
```

## Installation

### En global (recommandé — disponible dans tous les projets)

PowerShell :

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\opencode\plugins" | Out-Null
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\opencode\agents" | Out-Null
Copy-Item -Recurse -Force .\.opencode\plugins\opencode-cad "$env:USERPROFILE\.config\opencode\plugins\opencode-cad"
Copy-Item -Force .\.opencode\agents\*.md "$env:USERPROFILE\.config\opencode\agents\"
```

macOS / Linux :

```bash
mkdir -p ~/.config/opencode/plugins ~/.config/opencode/agents
cp -r .opencode/plugins/opencode-cad ~/.config/opencode/plugins/
cp .opencode/agents/*.md ~/.config/opencode/agents/
```

Aucune modification d'`opencode.jsonc` n'est nécessaire : le plugin et les agents sont auto-découverts. Si la session en cours ne les voit pas, redémarre OpenCode.

### Dans un projet

Copier le dossier `.opencode/` à la racine du projet. Le plugin n'est alors actif que dans ce projet.

### Environnement Python

Au premier lancement, l'agent appelle `cad_env` et propose d'installer CadQuery dans le `.venv` du projet. À la main :

```bash
python -m venv .venv
.venv/Scripts/pip install -r <chemin-du-plugin>/cad/requirements.txt   # Windows
.venv/bin/pip install -r <chemin-du-plugin>/cad/requirements.txt       # macOS / Linux
```

Pour utiliser un autre interpréteur : variable d'environnement `CAD_PYTHON=/chemin/vers/python`.

## Utilisation

Exemple de prompt :

> Conçois un boîtier 120×80×40 mm pour une carte ESP32 avec 4 bossages de vis M3, un couvercle à clipser, une ouverture USB-C sur un côté et des aérations. Pièces à plat pour l'impression.

L'agent suit : **spec (dimensions réelles sourcées) → pitfalls/exemples → script avec `PARAMS` et `EXPECT`/`PARTS` → un ajout à la fois, `cad_run` après chacun → critique → livraison**. Les sorties sont dans `cad_out/<nom_du_script>/` : `model[_<pièce>].step`/`.stl`, `view_*.png`, `report.json`.

Choisis l'agent `cad` avec **Tab** dans l'interface.

## Convention des scripts

```python
import cadquery as cq

PARAMS = dict(longueur=80.0, ...)          # toutes les cotes ici
EXPECT = {"bbox": [80, 40, 50], "tol": 0.5, "solids": 1}   # garde-fous optionnels

...

result = ma_piece                          # pièce unique obligatoire

# OU, pour plusieurs pièces à imprimer :
PARTS = {"base": ma_base, "couvercle": mon_couvercle}   # chacune orientée pour l'impression, posée sur z=0
PARTS_EXPECT = {"base": {"bbox": [...], "solids": 1}}   # garde-fous par pièce
ASSEMBLY = cq.Assembly().add(...)          # optionnel : vue assemblée pour contrôler l'emboîtement
```

## Impression : Creality K1 SE

Le plugin connaît ton imprimante (volume 220 × 220 × 250 mm, buse 0,4 mm). À chaque `cad_run` :

- toute pièce (le `result` ou chaque entrée de `PARTS`) qui dépasse le volume utile (215 × 215 × 245 mm, marge de 5 mm) est **refusée** : l'agent doit la découper en pièces imprimables avec joints d'alignement, jamais la réduire ;
- un design qui doit s'ouvrir ou s'assembler est découpé en pièces (ex. boîtier → base + couvercle) modélisées chacune à plat, avec la vue assemblée et la coupe pour contrôler jeux et emboîtements.

Autre imprimante : dans le script, `PRINTER = {"x": 350, "y": 350, "z": 300, "margin": 5}` (fusion partielle), ou `PRINT_CHECK = False` pour désactiver le contrôle.

## Utiliser un modèle avec vision pour la critique

Si ton modèle principal n'a pas de vision, donne au critique un modèle qui en a, dans l'en-tête de l'agent (`~/.config/opencode/agents/cad-critic.md` en global, `.opencode/agents/cad-critic.md` en projet) :

```yaml
model: fournisseur/modele-avec-vision
```

Le critique lit alors les PNG ; sans vision il raisonne à partir des métriques et du code, ce qui attrape déjà beaucoup d'erreurs (pièces flottantes, mauvaise taille, volume incohérent).

## Tests

`tests/test_cad_runner.py` vérifie le runner de bout en bout (pièce simple, pièce trop grande refusée, multi-pièces avec exports séparés, assemblage + coupe + rendus, les 3 exemples) :

```bash
.venv/Scripts/python tests/test_cad_runner.py   # Windows
.venv/bin/python tests/test_cad_runner.py       # macOS / Linux
```

## Limites

- Les rendus utilisent matplotlib (tri des faces approximatif sur les formes très complexes) : ils servent à vérifier l'allure et l'orientation, pas à faire du rendu photo. Pour un contrôle fin, ouvre le STEP dans FreeCAD ou ton slicer.
- Filetages réels, engrenages à développante et surfaces organiques ne sont pas le point fort du CAD en code. Le prompt de l'agent lui demande de le signaler et d'utiliser un équivalent simple.
- Pour mesurer tes progrès : garde 10 à 20 pièces de test de difficulté croissante et relance-les à chaque changement de modèle, de prompt ou de `pitfalls.md`.

## Faire évoluer la base de connaissances

Chaque erreur récurrente d'un modèle doit finir dans `cad/pitfalls.md` (section `##` courte avec le piège et le bon motif), et chaque bonne solution dans `cad/examples/` (un script qui passe `cad_run` en `STATUS: OK`, première ligne de docstring = description). Lance `tests/test_cad_runner.py` avant de copier le dossier du plugin dans ton installation globale (voir la commande d'installation). C'est le levier qui rend un modèle open source de plus en plus fiable.

---

Nécessite OpenCode V2 (API plugins `@opencode/plugin`).

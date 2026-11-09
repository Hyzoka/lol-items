---
name: lol-items
description: Pendant une partie de League of Legends, lit la game en cours (champion joué, ennemis, leurs items) et calcule quel prochain item est le plus rentable pour le joueur, à partir des stats officielles (AD, AP, péné, armure, RM). À utiliser quand il dit « lol-items », « quoi acheter », « quel item », « build » pendant une game.
---

# lol-items — quel item acheter, au calcul

Réponds en français, ton informel, **très court** : le joueur est en pleine game (alt-tab, souvent au retour en base).

**Statut : V2g** (build adapté à la game : la part survie monte avec tes morts, l'ennemi le plus fed et les assassins adverses ; stase de la Zhonya pondérée selon la menace)

**Avant : V2e** (un item en une phrase, adapté à ton style : dégâts / bruiser / tank, parmi les items réellement joués sur ton champion dans ta lane (OP.GG), passifs des ~30 items clés compris : `passifs.json`. Plus : quel item vendre s'il manque un slot ou en fin de game, drakes Montagne ennemis comptés, Baron / Ancien ennemi actif signalé : `buffs.json`).

## Contraintes
- L'API live de Riot (`https://127.0.0.1:2999`) n'existe que **sur le PC de jeu, pendant une partie** (entraînement compris). Depuis l'app Claude ou l'iPhone, la skill ne peut pas tourner : dis-le en une ligne.
- Lecture seule : aucune action sur le client, le jeu ou le compte.
- On n'invente rien : chaque chiffre vient de l'API live, de Data Dragon (officiel) ou d'un calcul du script. Les limites (runes et shards ignorés, Baron / Ancien signalés mais pas calculés) sont toujours rappelées.

## Étapes
1. Lance le script (`python` sous Windows, `python3` sur Mac) :
   `python ~/.claude/skills/lol-items/scripts/lol_items.py`
   - Pas de partie détectée → dis-le et arrête-toi.
   - Hors game, pour tester : `--demo ~/.claude/skills/lol-items/tests/demo_allgamedata.json`.
   - Pour garder une partie et la rejouer plus tard : `--save partie-AAAA-MM-JJ.json`.
   - **Style annoncé par le joueur** (« je joue Malphite AP », « Kata AD », « en tank », « bruiser ») → traduis-le en options :
     `--dmg ap` / `--dmg ad` pour le type de dégâts, `--style degats|bruiser|tank` pour le rôle. « Malphite AP » = `--dmg ap` (le script passe en dégâts) ; « Kata AD conqué » = `--style bruiser --dmg ad`.
     Sans rien de dit, n'ajoute rien : le script lit la rune principale (Conquérant → bruiser, Poigne / Répercussions → tank, Électrocution, Comète… → dégâts), puis le rôle du champion (Malphite → tank).
   - **Lane annoncée** (« je suis top ») → `--lane top|jungle|mid|adc|support`. Sinon le script prend la lane de l'API live, puis la lane principale du champion.
   - « Sans méta », « calcul pur » → `--no-meta`.
   - Si le joueur veut comprendre ou débugger, ou demande « check mon stuff » / « je vends quoi » : ajoute `--details` (objectifs, inventaire item par item avec verdict garde / vendable, top 3 chiffré, combo modélisé, stats ennemies). Pour « check mon stuff », recopie seulement les lignes `OBJECTIFS` et `INVENTAIRE`.
2. **Recopie telle quelle la ligne `→ …` du script** (elle peut contenir « Vends X » et un `⚠ Baron ennemi`). Rien d'autre : pas de rapport, pas d'analyse (il est en game). Ne refais pas le calcul toi-même.
3. Avec `--details` seulement : si la ligne « Contrôle formule » montre plus de 5 % d'écart, signale-le (bug de modélisation probable).

## Fichiers
- `scripts/lol_items.py` : le moteur (lecture de la game, combo Meraki, score item par item et ennemi par ennemi) (cache Data Dragon dans `~/.cache/lol-items/`).
- `passifs.json` : passifs d'items modélisés (valeurs + source Data Dragon / Meraki) et sorts qui appliquent les effets à l'impact (R de Kata). À corriger à la main si un patch change une valeur.
- `buffs.json` : drakes (Montagne compté dans les résistances ennemies), Baron et Ancien (durée, perdus à la mort). Valeurs du wiki, à corriger à la main si un patch les change.
- `tests/demo_allgamedata.json` : partie d'exemple (Jhin contre 2 tanks).

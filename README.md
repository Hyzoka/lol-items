# lol-items

A [Claude Code](https://claude.com/claude-code) skill that tells you **which item to buy next in League of Legends**, by calculation rather than by tier list.

It reads your live game (your champion, gold, items, and the enemy team's champions, levels and items), recomputes the enemies' real stats (armor, MR, HP), models your combo, then scores every item you could buy, enemy by enemy. You get one line back:

```
→ Lord Dominik's Regards: +45% damage on the team. (buy now: Last Whisper, 1450 g) [AD damage · Marksman · ADC meta]
```

> The skill and its output are in **French** (that's what I play in). The code is plain Python; translating `SKILL.md` is enough to get English answers.

## How it works

- **Riot Live Client Data API** (`https://127.0.0.1:2999`): only available on the gaming PC, during a game (practice tool included). Read-only.
- **Data Dragon** (official): champion base stats, item prices and stats.
- **Meraki Analytics** (community): spell ratios for the combo model.
- **OP.GG** (unofficial API): items actually played on your champion in your lane, used as a meta filter (cached 24 h). Disable with `--no-meta`.
- `passifs.json`: hand-modelled passives of ~30 key items (values + source). Fix by hand if a patch changes a value.
- `buffs.json`: dragon, Baron and Elder buffs (wiki values). Objectives are read from the live game events: enemy Mountain drakes are applied to enemy resistances, an active enemy Baron / Elder is flagged in the output line.
- **Inventory check**: each item gets a keep / sellable verdict with its sell price. When you're out of slots, or late game (3+ completed items), the line tells you what to sell first (potions → control ward → starters → Dark Seal last), and the sell gold counts toward the next purchase. Ask "check my stuff" (or use `--details`) to see the full verdict.

Your style is inferred from your keystone rune (Conqueror → bruiser, Grasp / Aftershock → tank, Electrocute, Comet… → damage), then from the champion's role. You can force it.

**Game state:** the survivability weight follows the game. +5 points per 1k gold your team is behind, +10 points per 1k gold the most fed enemy is above the 10-player average (capped at 60%); when you're ahead, it drops 10% of your style's base per 1k (never below half). The output line shows it (`team −0.2k g, Yunara fed +3.0k, survival 32%`), so items like Zhonya's can come up even in damage style. Components of any top-3 item are never suggested for sale. Zhonya's stasis is modelled as a flat +25% effective HP (an estimate, not Riot data).

**Adapts to your game, not just your rune:** survivability also rises +5 points per death you have above your kills, and +5 points per enemy assassin who isn't behind (Kayn, Sylas…; marksmen tagged Assassin like Vayne don't count). The fed enemy is ranked by item gold plus 0.15k per net kill. Zhonya's stasis is weighted by who you face: ×1.6 vs assassins, ×1 vs mages, ×0.4 vs marksmen. Same Katarina game: 10/3 and ahead → Rabadon (survival 23%); 2/9 → Zhonya (58%). `--details` prints a `SURVIE` line showing where the weight comes from. All coefficients are estimates, tuned over games.

**Not modelled:** enemy runes and stat shards; Baron / Elder are flagged but not in the damage calculation (holders are estimated from kill events); Dark Seal stacks aren't exposed by the API. Every number comes from the live API, Data Dragon, or the script's own calculation; nothing is made up.

## Install

Requires Python 3.9+ (standard library only) and Claude Code.

```bash
git clone https://github.com/Hyzoka/lol-items ~/.claude/skills/lol-items
```

Then, in a game, ask Claude Code `/lol-items` (or "quoi acheter ?").

## Run the script directly

```bash
python scripts/lol_items.py                      # live game → one line
python scripts/lol_items.py --details            # top 3 with numbers, modelled combo, enemy stats
python scripts/lol_items.py --demo tests/demo_allgamedata.json   # replay a saved game (no LoL needed)
python scripts/lol_items.py --save my-game.json  # save the live game to replay later
python scripts/lol_items.py --style tank --dmg ap --lane top      # force style / damage type / lane
python scripts/lol_items.py --no-meta            # pure calculation, no OP.GG filter
python scripts/lol_items.py --json               # JSON output
```

Data Dragon / Meraki / OP.GG responses are cached in `~/.cache/lol-items/`.

## Disclaimer

lol-items isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc.

## License

MIT

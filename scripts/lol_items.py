#!/usr/bin/env python3
"""lol-items — V1 : un item à acheter, en une phrase, au calcul.

Sources (officielles Riot sauf mention) :
  - Live Client Data API : https://127.0.0.1:2999/liveclientdata/allgamedata (PC de jeu, partie en cours)
  - Data Dragon          : stats de base des champions, items (prix, stats parsées du bloc <stats>)
  - Meraki Analytics     : ratios des sorts (communautaire, peut retarder sur le patch)
  - OP.GG (API non officielle) : items joués par champion et par lane (filtre méta, cache 24 h)

Usage :
  python lol_items.py                 # partie en cours -> une phrase
  python lol_items.py --details       # + top 3 chiffré, stats des ennemis, limites
  python lol_items.py --demo FICHIER  # rejouer un allgamedata.json enregistré
  python lol_items.py --save FICHIER  # enregistrer l'allgamedata de la partie en cours (pour rejouer)
  python lol_items.py --json          # sortie JSON (pour Claude) au lieu du texte
  python lol_items.py --style tank --dmg ap --lane top   # forcer style, type de dégâts, lane
  python lol_items.py --no-meta       # calcul seul, sans filtre méta

Aucune action sur le jeu ni le compte : lecture seule de l'API locale.
"""
import argparse
import json
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

LIVE_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
DDRAGON = "https://ddragon.leagueoflegends.com"
MERAKI = "https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US/champions/{}.json"
OPGG = "https://lol-api-champion.op.gg/api/global/champions/ranked/{}/{}?tier=emerald_plus"
META_TTL = 24 * 3600
CACHE = Path.home() / ".cache" / "lol-items"
UA = {"User-Agent": "lol-items/0.1"}

# Libellé anglais du bloc <stats> -> (clé, en pourcentage ?)
STAT_LABELS = {
    ("Health", False): "hp",
    ("Armor", False): "armor",
    ("Magic Resist", False): "mr",
    ("Attack Damage", False): "ad",
    ("Ability Power", False): "ap",
    ("Ability Haste", False): "ah",
    ("Lethality", False): "lethality",
    ("Armor Penetration", True): "armor_pen_pct",
    ("Magic Penetration", False): "magic_pen_flat",
    ("Magic Penetration", True): "magic_pen_pct",
    ("Attack Speed", True): "as_pct",
    ("Critical Strike Chance", True): "crit",
    ("Critical Strike Damage", True): "crit_dmg",
    ("Life Steal", True): "lifesteal",
    ("Omnivamp", True): "omnivamp",
    ("Mana", False): "mana",
    ("Move Speed", False): "ms_flat",
    ("Move Speed", True): "ms_pct",
    ("Adaptive Force", False): "adaptive",
    ("Heal and Shield Power", True): "hsp",
    ("Tenacity", True): "tenacity",
}


# ---------------------------------------------------------------- réseau / cache
def http_json(url, insecure_local=False, timeout=20):
    ctx = None
    if insecure_local:
        # Certificat auto-signé de Riot, uniquement pour 127.0.0.1
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))


def cached_json(name, url):
    path = CACHE / name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = http_json(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def load_static():
    version = http_json(f"{DDRAGON}/api/versions.json")[0]
    base = f"{DDRAGON}/cdn/{version}/data"
    champs = cached_json(f"{version}/champion_fr.json", f"{base}/fr_FR/champion.json")["data"]
    items_en = cached_json(f"{version}/item_en.json", f"{base}/en_US/item.json")["data"]
    items_fr = cached_json(f"{version}/item_fr.json", f"{base}/fr_FR/item.json")["data"]
    return version, champs, items_en, items_fr


# ---------------------------------------------------------------- items
def parse_item_stats(description):
    """Parse le bloc <stats> (anglais) -> dict de stats."""
    out = {}
    m = re.search(r"<stats>(.*?)</stats>", description or "")
    if not m:
        return out
    for part in m.group(1).split("<br>"):
        text = re.sub(r"<[^>]+>", "", part).strip()
        num = re.match(r"^[+\-]?\s*([\d.]+)\s*(%?)\s*(.*)$", text)
        if not num:
            continue
        value, pct, label = float(num.group(1)), num.group(2) == "%", num.group(3).strip()
        key = STAT_LABELS.get((label, pct))
        if key:
            out[key] = out.get(key, 0) + (value / 100 if pct else value)
    return out


def build_items(items_en, items_fr):
    """Tous les items de la Faille (map 11), stats parsées + nom FR + prix."""
    db = {}
    for iid, it in items_en.items():
        if not iid.isdigit() or len(iid) > 4:  # 6 chiffres = variantes Arena / Ornn
            continue
        if not it.get("maps", {}).get("11"):
            continue
        db[int(iid)] = {
            "id": int(iid),
            "name": items_fr.get(iid, it)["name"],
            "price": it["gold"]["total"],
            "sell": it["gold"].get("sell", 0),
            "purchasable": it["gold"]["purchasable"],
            "into": [int(x) for x in it.get("into", []) if len(x) <= 4],
            "from": [int(x) for x in it.get("from", [])],
            "tags": it.get("tags", []),
            "stats": parse_item_stats(it.get("description", "")),
            "completed": not [x for x in it.get("into", []) if len(x) <= 4],
            "restricted": bool(it.get("requiredChampion") or it.get("requiredAlly")
                               or it.get("hideFromAll") or it.get("inStore") is False),
        }
    return db


# ---------------------------------------------------------------- champions
def champ_key(player, champs):
    """rawChampionName 'game_character_displayname_MasterYi' -> clé Data Dragon."""
    raw = player.get("rawChampionName", "")
    cand = raw.split("game_character_displayname_")[-1] if raw else player.get("championName", "")
    lower = {k.lower(): k for k in champs}
    if cand.lower() in lower:
        return lower[cand.lower()]
    # repli sur le nom affiché (localisé)
    for k, c in champs.items():
        if c["name"].lower() == player.get("championName", "").lower():
            return k
    return None


def growth(base, per_level, level):
    """Formule officielle de croissance par niveau."""
    n = level - 1
    return base + per_level * n * (0.7025 + 0.0175 * n)


def base_stats(champ, level):
    s = champ["stats"]
    return {
        "hp": growth(s["hp"], s["hpperlevel"], level),
        "armor": growth(s["armor"], s["armorperlevel"], level),
        "mr": growth(s["spellblock"], s["spellblockperlevel"], level),
        "ad": growth(s["attackdamage"], s["attackdamageperlevel"], level),
    }


def item_totals(player_items, items_db):
    tot, unknown = {}, []
    for it in player_items:
        db = items_db.get(it.get("itemID"))
        if not db:
            unknown.append(it.get("displayName", str(it.get("itemID"))))
            continue
        for k, v in db["stats"].items():
            tot[k] = tot.get(k, 0) + v * max(1, it.get("count", 1))
    return tot, unknown


def computed_stats(player, champs, items_db):
    key = champ_key(player, champs)
    if not key:
        return None
    level = player.get("level", 1)
    b = base_stats(champs[key], level)
    it, unknown = item_totals(player.get("items", []), items_db)
    return {
        "key": key,
        "level": level,
        "hp": round(b["hp"] + it.get("hp", 0)),
        "bonus_hp": round(it.get("hp", 0)),
        "armor": round(b["armor"] + it.get("armor", 0)),
        "bonus_armor": round(it.get("armor", 0)),
        "mr": round(b["mr"] + it.get("mr", 0)),
        "bonus_mr": round(it.get("mr", 0)),
        "ad": round(b["ad"] + it.get("ad", 0)),
        "ap": round(it.get("ap", 0)),
        "unknown_items": unknown,
        "item_gold": sum(items_db[i["itemID"]]["price"] * max(1, i.get("count", 1))
                         for i in player.get("items", []) if i.get("itemID") in items_db),
    }


def shard_hp(active_player, level):
    """PV des shards (5001 : 10 à 180 selon le niveau, 5011 : 65 flat)."""
    hp = 0
    for r in active_player.get("fullRunes", {}).get("statRunes", []):
        if r.get("id") == 5001:
            hp += 10 + 170 * (level - 1) / 17
        elif r.get("id") == 5011:
            hp += 65
    return hp


def damage_profile(totals):
    """Part AD / AP estimée d'un ennemi, d'après ses items (V0 : grossier)."""
    ad = totals.get("ad", 0) + totals.get("lethality", 0) * 2
    ap = totals.get("ap", 0) + totals.get("magic_pen_flat", 0) * 3
    if ad + ap == 0:
        return None
    return round(ad / (ad + ap), 2)


# ---------------------------------------------------------------- meraki (test V0)
def meraki_check(key):
    try:
        data = cached_json(f"meraki/{key}.json", MERAKI.format(key))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    n_ratios = 0
    for spells in data.get("abilities", {}).values():
        for sp in spells:
            for eff in sp.get("effects", []):
                for lv in eff.get("leveling", []):
                    for mod in lv.get("modifiers", []):
                        if any("%" in u for u in mod.get("units", [])):
                            n_ratios += 1
    return {"ok": n_ratios > 0, "ratios": n_ratios, "patch": data.get("patchLastChanged")}


# ---------------------------------------------------------------- analyse
def find_me(game):
    ap = game["activePlayer"]
    ids = {ap.get("riotId"), ap.get("summonerName"), ap.get("riotIdGameName")} - {None, ""}
    for p in game["allPlayers"]:
        if {p.get("riotId"), p.get("summonerName"), p.get("riotIdGameName")} & ids:
            return p
    return None


def objectives(game, my_team):
    """Drakes, âme, Baron et Ancien de chaque équipe, d'après les événements de l'API live.
    Baron / Ancien : actifs pendant leur durée, perdus à la mort (morts lues dans les kills)."""
    name_team, names = {}, {}  # pseudo -> équipe ; joueur -> ses pseudos
    for i, p in enumerate(game["allPlayers"]):
        names[i] = {n.split("#")[0] for n in (p.get("riotIdGameName"), p.get("summonerName"),
                                              p.get("riotId")) if n}
        for n in names[i]:
            name_team[n] = p.get("team")
    now = game.get("gameData", {}).get("gameTime", 0)
    events = game.get("events", {}).get("Events", [])
    deaths = [(e["EventTime"], e.get("VictimName", "")) for e in events if e["EventName"] == "ChampionKill"]
    out = {side: {"drakes": {}, "soul": None, "timed": []} for side in ("ally", "enemy")}
    for e in events:
        if e["EventName"] not in ("DragonKill", "BaronKill"):
            continue
        team = name_team.get(e.get("KillerName", "").split("#")[0])
        if not team:
            continue
        side = out["ally" if team == my_team else "enemy"]
        kind = e.get("DragonType") if e["EventName"] == "DragonKill" else "BaronKill"
        if kind in BUFFS["dragons"]:
            side["drakes"][kind] = side["drakes"].get(kind, 0) + 1
            if sum(side["drakes"].values()) == 4:
                side["soul"] = BUFFS["dragons"][kind]["nom"]
            continue
        buff = BUFFS["timed"].get(kind)
        t0 = e["EventTime"]
        if not buff or t0 + buff["duree"] <= now:
            continue
        holders = [i for i, p in enumerate(game["allPlayers"]) if p.get("team") == team and not any(
            v.split("#")[0] in names[i] and t0 - BUFFS["respawn_approx"] <= t <= now for t, v in deaths)]
        if holders:
            side["timed"].append({"nom": buff["nom"], "reste": round(t0 + buff["duree"] - now),
                                  "joueurs": len(holders)})
    return out


def mountain_mult(side):
    return 1 + side["drakes"].get("Earth", 0) * BUFFS["dragons"]["Earth"]["resist_pct"]


def apply_mountain(cs, mult):
    """Montagne : % d'armure et de RM totales ; le gain compte comme résistance bonus."""
    for k in ("armor", "mr"):
        gain = cs[k] * (mult - 1)
        cs[k] = round(cs[k] + gain)
        cs[f"bonus_{k}"] = round(cs[f"bonus_{k}"] + gain)


def analyse(game, champs, items_db, version):
    ap = game["activePlayer"]
    me = find_me(game)
    if not me:
        raise SystemExit("Impossible de te retrouver dans la liste des joueurs (spectateur ?).")
    st = ap.get("championStats", {})
    obj = objectives(game, me.get("team"))
    mine_calc = computed_stats(me, champs, items_db)
    if mine_calc:  # shards de PV : visibles seulement pour toi
        mine_calc["hp"] += round(shard_hp(ap, ap.get("level", 1)))
        apply_mountain(mine_calc, mountain_mult(obj["ally"]))
    out = {
        "patch": version,
        "game_time": round(game.get("gameData", {}).get("gameTime", 0)),
        "me": {
            "champion": me.get("championName"),
            "key": mine_calc["key"] if mine_calc else None,
            "level": ap.get("level"),
            "gold": round(ap.get("currentGold", 0)),
            "abilities": {k: v.get("abilityLevel", 0) for k, v in ap.get("abilities", {}).items()
                          if k in ("Q", "W", "E", "R")},
            "items": [i.get("displayName") for i in me.get("items", [])],
            "stats_live": {k: st.get(k) for k in (
                "attackDamage", "abilityPower", "attackSpeed", "critChance", "critDamage",
                "armor", "magicResist", "maxHealth", "physicalLethality", "armorPenetrationFlat",
                "armorPenetrationPercent", "bonusArmorPenetrationPercent", "magicLethality",
                "magicPenetrationFlat", "magicPenetrationPercent", "bonusMagicPenetrationPercent",
                "abilityHaste")},
            # contrôle de la formule : stats recalculées vs réelles, sur toi
            "check": {
                "armor_calc": mine_calc["armor"] if mine_calc else None, "armor_live": st.get("armor"),
                "mr_calc": mine_calc["mr"] if mine_calc else None, "mr_live": st.get("magicResist"),
                "hp_calc": mine_calc["hp"] if mine_calc else None, "hp_live": st.get("maxHealth"),
            },
        },
        "enemies": [],
        "objectives": obj,
        "warnings": ["Runes, shards et passifs à stacks ignorés : résistances ennemies légèrement "
                     "sous-estimées. Drakes Montagne ennemis comptés ; Baron / Ancien affichés, "
                     "pas dans le calcul (porteurs estimés d'après les kills)."],
    }
    if mine_calc:
        out["me"]["ratios"] = meraki_check(mine_calc["key"])
    for p in game["allPlayers"]:
        if p.get("team") == me.get("team"):
            continue
        cs = computed_stats(p, champs, items_db)
        if not cs:
            out["warnings"].append(f"Champion inconnu : {p.get('championName')}")
            continue
        apply_mountain(cs, mountain_mult(obj["enemy"]))
        totals, _ = item_totals(p.get("items", []), items_db)
        sc = p.get("scores", {})
        cs.update({
            "champion": p.get("championName"),
            "position": p.get("position") or "",
            "kda": f"{sc.get('kills', 0)}/{sc.get('deaths', 0)}/{sc.get('assists', 0)}",
            "cs": sc.get("creepScore", 0),
            "ad_share": damage_profile(totals),
            "items": [i.get("displayName") for i in p.get("items", [])],
            "class": champs[cs["key"]].get("tags", []),
        })
        if cs["unknown_items"]:
            out["warnings"].append(f"{cs['champion']} : items non reconnus {cs['unknown_items']}")
        out["enemies"].append(cs)
    return out


# ---------------------------------------------------------------- V1 : reco d'item
# Unités Meraki (en minuscules, sans espaces doublés) -> stat utilisée dans le calcul
UNIT_STAT = {
    "": "flat", "% ap": "ap", "% ad": "ad", "% bonus ad": "bonus_ad",
    "% of target's maximum health": "target_hp",
    "% armor": "armor", "% bonus armor": "bonus_armor",
    "% magic resistance": "mr", "% bonus magic resistance": "bonus_mr",
    "% maximum health": "hp", "% bonus health": "bonus_hp",
    "% per 100% bonus attack speed": "bonus_ad_per_as",  # R de Kata : ratio d'AD bonus qui monte avec l'AS
}
# Passifs d'items (V2d) : table éditable à la main, relue à chaque lancement
PASSIVES = json.loads((Path(__file__).resolve().parent.parent / "passifs.json").read_text(encoding="utf-8"))
# Drakes, Baron, Ancien (V2e) : valeurs du wiki, éditables à la main
BUFFS = json.loads((Path(__file__).resolve().parent.parent / "buffs.json").read_text(encoding="utf-8"))
# Lignes Meraki qui ne sont pas des dégâts sur un champion ennemi
SKIP_ATTR = ("minion", "monster", "turret", "reduced", "reduction", "clone", "per target death",
             "per stack", "minimum", "per tick", "per hit", "per dagger", "per bullet", "per sphere")
MIN_LEGENDARY_PRICE = 2000


def damage_type(attribute, spell_type, adaptive):
    a = attribute.lower()
    for word, t in (("physical", "phys"), ("magic", "magic"), ("true", "true"), ("mixed", "mixed")):
        if word in a:
            return t
    return {"PHYSICAL_DAMAGE": "phys", "MAGIC_DAMAGE": "magic", "TRUE_DAMAGE": "true",
            "MIXED_DAMAGE": "mixed"}.get(spell_type) or (
        "phys" if adaptive == "PHYSICAL_DAMAGE" else "magic")


def combo_lines(meraki, ranks, level, key):
    """Une ligne de dégâts par effet de sort, au rang actuel : (sort, type, [(stat, ratio)]).
    Renvoie aussi le nombre d'effets à l'impact appliqués par les sorts (efficacité comprise)."""
    lines, ignored, spell_onhit = [], set(), 0.0
    adaptive = meraki.get("adaptiveType")
    onhit_hits = PASSIVES.get("_champion_onhit", {}).get(key, {})
    for slot, spells in meraki.get("abilities", {}).items():
        rank = level if slot == "P" else ranks.get(slot, 0)
        if rank <= 0:
            continue
        for sp in spells:
            for eff in sp.get("effects", []):
                for lv in eff.get("leveling", []):
                    if slot in onhit_hits and "on-hit damage effectiveness" in lv["attribute"].lower():
                        vals = lv["modifiers"][0]["values"]
                        spell_onhit += onhit_hits[slot] * vals[min(rank, len(vals)) - 1] / 100
                cands = [lv for lv in eff.get("leveling", [])
                         if "damage" in lv["attribute"].lower()
                         and not any(s in lv["attribute"].lower() for s in SKIP_ATTR)]
                # Un effet peut mêler physique et magique (R de Katarina) : une ligne par type,
                # la version « maximum / total » en priorité
                by_type = {}
                for lv in cands:
                    t = damage_type(lv["attribute"], sp.get("damageType"), adaptive)
                    full = bool(re.search(r"\b(maximum|total|max)\b", lv["attribute"].lower()))
                    if t not in by_type or (full and not by_type[t][1]):
                        by_type[t] = (lv, full)
                for dtype, (lv, _) in by_type.items():
                    terms = combo_terms(lv, rank, ignored)
                    if terms:
                        lines.append((f"{slot} {sp.get('name')}", dtype, terms))
    return lines, sorted(ignored), spell_onhit


def combo_terms(lv, rank, ignored):
    """Ratios d'une ligne Meraki au rang donné -> [(stat, ratio)]."""
    terms = []
    for mod in lv.get("modifiers", []):
        vals, units = mod.get("values", []), mod.get("units", [])
        if not vals:
            continue
        i = min(rank, len(vals)) - 1
        unit = re.sub(r"\s+", " ", units[i] if i < len(units) else "").strip().lower()
        stat = UNIT_STAT.get(unit)
        if stat is None:
            ignored.add(unit)
            continue
        terms.append((stat, vals[i] if stat == "flat" else vals[i] / 100))
    return terms


def my_attack_stats(ap_live, champ, level):
    """Tes stats offensives réelles (API live) + ce qu'il faut pour ajouter un item."""
    s = ap_live.get("championStats", {})
    b = base_stats(champ, level)
    base_as = champ["stats"]["attackspeed"]
    live_as = s.get("attackSpeed") or base_as
    return {
        "ad": s.get("attackDamage", 0), "bonus_ad": s.get("attackDamage", 0) - b["ad"],
        "ap": s.get("abilityPower", 0),
        "hp": s.get("maxHealth", 0), "bonus_hp": s.get("maxHealth", 0) - b["hp"],
        "armor": s.get("armor", 0), "bonus_armor": s.get("armor", 0) - b["armor"],
        "mr": s.get("magicResist", 0), "bonus_mr": s.get("magicResist", 0) - b["mr"],
        "crit": s.get("critChance", 0), "crit_dmg": (s.get("critDamage") or 175) / 100,
        "base_as": base_as, "bonus_as": live_as / base_as - 1,  # croissance par niveau comprise
        # péné : l'API donne des multiplicateurs (1.0 = aucune péné, vérifié en game le 2026-11-05)
        "arpen_mult": s.get("armorPenetrationPercent", 1.0),
        "bonus_arpen_mult": s.get("bonusArmorPenetrationPercent", 1.0),
        "lethality": s.get("physicalLethality", 0) + s.get("armorPenetrationFlat", 0)
                     * (0 if s.get("physicalLethality") else 1),
        "mpen_mult": s.get("magicPenetrationPercent", 1.0),
        "bonus_mpen_mult": s.get("bonusMagicPenetrationPercent", 1.0),
        "mpen_flat": s.get("magicPenetrationFlat", 0) + s.get("magicLethality", 0),
        "base_ad": b["ad"], "level": level,
        "ranged": champ["stats"].get("attackrange", 125) > 300,
    }


def item_effects(iid):
    return PASSIVES.get(str(iid), {}).get("effects", [])


def with_item(me, it):
    """Tes stats après achat d'un item : stats du bloc <stats> + passifs qui donnent des stats.
    Les passifs de tes items actuels sont déjà dans les stats live (Rabadon compris)."""
    st = it["stats"]
    m = dict(me)
    m["items"] = me["items"] + [it["id"]]
    for k in ("ad", "bonus_ad"):
        m[k] += st.get("ad", 0)
    ap_mult = next((e["value"] for e in item_effects(it["id"]) if e["type"] == "ap_mult"), None)
    if ap_mult:
        m["ap"] = (m["ap"] + st.get("ap", 0)) * (1 + ap_mult)
        m["ap_mult"] = me["ap_mult"] * (1 + ap_mult)
    else:
        m["ap"] += st.get("ap", 0) * me["ap_mult"]
    for k, sk in (("hp", "hp"), ("bonus_hp", "hp"), ("armor", "armor"), ("bonus_armor", "armor"),
                  ("mr", "mr"), ("bonus_mr", "mr")):
        m[k] += st.get(sk, 0)
    m["crit"] = min(1.0, m["crit"] + st.get("crit", 0))
    m["crit_dmg"] += st.get("crit_dmg", 0)
    m["bonus_as"] += st.get("as_pct", 0)
    m["arpen_mult"] *= 1 - st.get("armor_pen_pct", 0)
    m["lethality"] += st.get("lethality", 0)
    m["mpen_mult"] *= 1 - st.get("magic_pen_pct", 0)
    m["mpen_flat"] += st.get("magic_pen_flat", 0)
    m["item_hp"] = me["item_hp"] + st.get("hp", 0)
    for e in item_effects(it["id"]):
        if e["type"] == "stat_from" and e["stat"] == "hp":  # Warmog : % des PV d'items
            gain = e["ratio"] * m["item_hp"]
            m["hp"] += gain
            m["bonus_hp"] += gain
        elif e["type"] == "stat_from" and e["stat"] == "ap":  # Riftmaker : AP selon les PV bonus
            m["ap"] += e["ratio"] * m[e["from"]] * m["ap_mult"]
    return m


def resist_mult(total, bonus, pct_mult, bonus_pct_mult, flat, shred=0.0):
    """Ordre officiel : réduction (Couperet), péné % (totale puis bonus), puis flat / léthalité.
    Jamais sous 0."""
    total, bonus = total * (1 - shred), bonus * (1 - shred)
    eff = (total - bonus) * pct_mult + bonus * pct_mult * bonus_pct_mult - flat
    return 100 / (100 + max(0.0, eff))


def lvl_value(spec, level):
    """[valeur min, valeur max, niveau min, niveau max] -> valeur linéaire au niveau."""
    lo, hi, l0, l1 = spec
    return lo + (hi - lo) * min(1.0, max(0.0, (level - l0) / (l1 - l0)))


def passive_raw(e, me, enemy):
    """Dégâts bruts d'un effet de passif (avant résistances)."""
    if me["ranged"] and "ranged" in e:
        e = {**e, **e["ranged"]}
    raw = (e.get("flat", 0) + e.get("ap", 0) * me["ap"] + e.get("ad", 0) * me["ad"]
           + e.get("base_ad", 0) * me["base_ad"] + e.get("hp", 0) * me["hp"]
           + e.get("bonus_hp", 0) * me["bonus_hp"] + e.get("target_hp", 0) * enemy["hp"]
           + e.get("target_current_hp", 0) * enemy["hp"])  # PV actuels ≈ max en début de combo
    if "flat_lvl" in e:
        raw += lvl_value(e["flat_lvl"], me["level"])
    if me["ranged"]:
        raw *= e.get("ranged_mult", 1)
    return raw * e.get("missing_hp_avg", 1)


def combo_damage(me, enemy, lines, n_autos, scale_autos_with_as):
    effects = [e for iid in me["items"] for e in item_effects(iid)]
    shred = sum(e["armor"] for e in effects if e["type"] == "shred")
    arm = resist_mult(enemy["armor"], enemy["bonus_armor"], me["arpen_mult"],
                      me["bonus_arpen_mult"], me["lethality"], shred)
    mr = resist_mult(enemy["mr"], enemy["bonus_mr"], me["mpen_mult"], me["bonus_mpen_mult"],
                     me["mpen_flat"])
    mult = {"phys": arm, "magic": mr, "true": 1.0}
    dmg = {}  # (type, vient d'un sort ?) -> dégâts après résistances

    def add(dtype, amount, spell):
        for t, part in ((("phys", 0.5), ("magic", 0.5)) if dtype == "mixed" else ((dtype, 1.0),)):
            dmg[(t, spell)] = dmg.get((t, spell), 0) + amount * part * mult[t]

    for _, dtype, terms in lines:
        raw = sum(ratio * (enemy["hp"] if stat == "target_hp" else 1 if stat == "flat"
                           else me["bonus_as"] * me["bonus_ad"] if stat == "bonus_ad_per_as"
                           else me[stat]) for stat, ratio in terms)
        add(dtype, raw, True)
    autos = 0.0
    if n_autos:
        att_speed = min(2.5, me["base_as"] * (1 + me["bonus_as"]))
        autos = n_autos * (att_speed / me["_as0"] if scale_autos_with_as else 1)
        add("phys", autos * me["ad"] * (1 + me["crit"] * (me["crit_dmg"] - 1)), False)

    # Passifs : effets à l'impact (autos + sorts qui les appliquent), lame enchantée (une seule), etc.
    onhits = autos + me.get("_spell_onhit", 0)
    spellblade = False
    for e in effects:
        t = e["type"]
        if t == "onhit":
            add(e["dmg"], onhits * passive_raw(e, me, enemy), False)
        elif t == "spellblade" and not spellblade and autos:
            spellblade = True
            add(e["dmg"], passive_raw(e, me, enemy), False)
        elif t == "every_n":
            add(e["dmg"], onhits / e["n"] * passive_raw(e, me, enemy), False)
        elif t == "once":
            add(e["dmg"], passive_raw(e, me, enemy), False)

    # Amplis de dégâts (additifs entre eux)
    amp = {k: 1.0 for k in dmg}
    for e in effects:
        if e["type"] != "amp":
            continue
        v = e.get("value", 0)
        if "per_100_bonus_hp" in e:  # Dominik : selon les PV bonus de la cible
            v = min(e["max"], e["per_100_bonus_hp"] * enemy["bonus_hp"] / 100)
        for (t, spell) in amp:
            if (e.get("dmg") and t not in e["dmg"]) or (e.get("spells_only") and not spell):
                continue
            amp[(t, spell)] += v
    return sum(v * amp[k] for k, v in dmg.items())


def remaining_cost(item_id, owned, items_db, used=None):
    """Prix restant : on retire les composants déjà possédés (chacun compté une fois).
    `used` reçoit les composants possédés qui partent dans la recette."""
    owned = list(owned)

    def walk(iid):
        if iid in owned:
            owned.remove(iid)
            if used is not None:
                used.append(iid)
            return 0
        it = items_db.get(iid)
        if not it:
            return 0
        sub = sum(walk(c) for c in it["from"])
        return it["price"] - sum(items_db[c]["price"] for c in it["from"] if c in items_db) + sub
    return walk(item_id)


def buy_now(item_id, owned, gold, items_db):
    """Composant le plus cher de la recette, pas encore possédé, que ton gold permet."""
    best, seen = None, set()

    def walk(iid):
        nonlocal best
        for c in items_db.get(iid, {}).get("from", []):
            if c in seen or c not in items_db:
                continue
            seen.add(c)
            it = items_db[c]
            if c not in owned and it["price"] <= gold and (not best or it["price"] > best["price"]):
                best = it
            walk(c)
    walk(item_id)
    return best


def is_boots(it):
    return "Boots" in it["tags"]


# ---------------------------------------------------------------- V2e : inventaire et revente
INV_SLOTS = 6  # le slot 6 est la balise
REFILLABLE_POT = 2031
CONTROL_WARD = 2055
DARK_SEAL, MEJAI = 1082, 3041
LATE_LEGENDARIES = 3  # à partir de 3 items complets, potions et starters ne servent plus


def is_role_item(db):
    """Item de support (tag GoldPer) ou familier de jungle (tag Jungle sans Lane : les potions
    ont les deux) : on ne le vend pas."""
    tags = set(db["tags"])
    return "GoldPer" in tags or "Trinket" in tags or ("Jungle" in tags and "Lane" not in tags)


def inventory_advice(me_p, top, owned, gold, items_db):
    """Verdict item par item + l'item à vendre s'il en faut un.
    prio : 1 = à vendre en premier … 4 = en dernier ; 0 = garde."""
    used = []
    if top:
        remaining_cost(top["id"], owned, items_db, used)
    inv = [i for i in me_p.get("items", []) if i.get("slot", 0) != INV_SLOTS]
    legendaries = sum(1 for i in inv if (db := items_db.get(i.get("itemID")))
                      and db["completed"] and db["price"] >= MIN_LEGENDARY_PRICE and not is_boots(db))
    late = legendaries >= LATE_LEGENDARIES
    rows = []
    for it in inv:
        iid, db = it.get("itemID"), items_db.get(it.get("itemID"))
        name = it.get("displayName") or (db and db["name"]) or str(iid)
        sell = db["sell"] if db else 0
        if iid in used:
            prio, why = 0, f"part dans {top['name']}"
        elif not db or is_role_item(db):
            prio, why = 0, "item de rôle"
        elif iid == CONTROL_WARD:
            prio, why = 2, "ward de contrôle : vends seulement si le slot manque"
        elif iid == REFILLABLE_POT or it.get("consumable") or "Consumable" in db["tags"]:
            prio, why = 1, "potion : inutile en fin de game"
        elif is_boots(db) or (db["completed"] and db["price"] >= MIN_LEGENDARY_PRICE):
            prio, why = 0, "item complet"
        elif iid == DARK_SEAL:
            prio, why = (0, "part dans Mejai") if top and top["id"] == MEJAI else \
                (4, "garde tes stacks, sauf si le slot manque")
        else:
            prio, why = 3, "starter / composant hors de ta prochaine recette"
        rows.append({"name": name, "id": iid, "sell": sell, "prio": prio, "why": why})
    # Slot : acheter un composant en prend un ; l'item complet libère ses composants
    free = INV_SLOTS - len(inv)
    need = 1 if not used or (top and gold < top["cost"]) else 0
    sellable = sorted((r for r in rows if r["prio"]), key=lambda r: (r["prio"], r["sell"]))
    sell, reason = None, None
    if need > free and sellable:
        sell, reason = sellable[0], "plus de slot"
    elif late and sellable and sellable[0]["prio"] in (1, 3):
        sell, reason = sellable[0], "fin de game, il ne sert plus"
    return {"rows": rows, "free": free, "late": late, "sell": sell, "reason": reason}


# ---------------------------------------------------------------- V2a : style de jeu
# Rune principale -> style (None = la rune ne tranche pas, on passe au rôle du champion)
KEYSTONE_STYLE = {
    8010: "bruiser",                                  # Conquérant
    8437: "tank", 8439: "tank", 8465: "tank",         # Poigne, Répercussions, Gardien
    8112: "degats", 8128: "degats", 9923: "degats",   # Électrocution, Moisson noire, Grêle de lames
    8214: "degats", 8229: "degats",                   # Aery, Comète arcanique
    8005: "degats", 8008: "degats", 8021: "degats",   # Attaque soutenue, Tempo mortel, Jeu de jambes
    8369: "degats",                                   # Première frappe
}
# Poids (dégâts, tankiness) du score selon le style
STYLE_WEIGHTS = {"degats": (1.0, 0.0), "bruiser": (0.5, 0.5), "tank": (0.25, 0.75)}
OFFENSE = {"ap": {"ap", "magic_pen_flat", "magic_pen_pct"},
           "ad": {"ad", "lethality", "armor_pen_pct", "crit", "crit_dmg", "as_pct"}}
# Stats qui décident du type d'un item (l'AS seule ne fait pas un item AD : Dent de Nashor = AP)
PRIMARY = {"ap": {"ap", "magic_pen_flat", "magic_pen_pct"},
           "ad": {"ad", "lethality", "armor_pen_pct", "crit"}}
DEFENSE = {"hp", "armor", "mr"}
STYLE_LABEL = {"degats": "dégâts", "bruiser": "bruiser", "tank": "tank"}


def pick_style(ap_live, champ, meraki, owned, items_db, style_arg, dmg_arg):
    """Style (degats / bruiser / tank) et type de dégâts (ad / ap).
    Priorité : ce que dit le joueur > sa rune principale > le rôle du champion."""
    ks = ap_live.get("fullRunes", {}).get("keystone") or {}
    if style_arg:
        style, why = style_arg, "demandé"
    elif dmg_arg:  # « Malphite AP » = jouer dégâts, sauf Conquérant (bruiser)
        style = "bruiser" if KEYSTONE_STYLE.get(ks.get("id")) == "bruiser" else "degats"
        why = "demandé"
    elif KEYSTONE_STYLE.get(ks.get("id")):
        style, why = KEYSTONE_STYLE[ks["id"]], f"rune {ks.get('displayName', '?')}"
    else:
        first = (champ.get("tags") or [""])[0]
        style = {"Tank": "tank", "Fighter": "bruiser"}.get(first, "degats")
        why = f"rôle {first or '?'}"
    if dmg_arg:
        dmg = dmg_arg
    else:  # d'après tes items offensifs, sinon le type adaptatif du champion
        tot, _ = item_totals([{"itemID": i} for i in owned], items_db)
        ad = tot.get("ad", 0) + 2 * tot.get("lethality", 0)
        ap = tot.get("ap", 0) + 3 * tot.get("magic_pen_flat", 0)
        if ad + ap > 0:
            dmg = "ad" if ad > ap else "ap"
        else:
            dmg = "ad" if meraki.get("adaptiveType") == "PHYSICAL_DAMAGE" else "ap"
    return style, dmg, why


def enemy_ad_share(e, champs):
    """Part physique des dégâts d'un ennemi : ses items, sinon sa fiche Data Dragon."""
    if e.get("ad_share") is not None:
        return e["ad_share"]
    info = champs[e["key"]].get("info", {})
    a, m = info.get("attack", 5), info.get("magic", 5)
    return a / (a + m) if a + m else 0.5


# ---------------------------------------------------------------- V2b : filtre méta (OP.GG)
# Lane de l'API live (ou --lane) -> lane OP.GG
LANES = {"top": "TOP", "jungle": "JUNGLE", "middle": "MID", "mid": "MID", "bottom": "ADC",
         "adc": "ADC", "bot": "ADC", "utility": "SUPPORT", "support": "SUPPORT", "supp": "SUPPORT"}
META_TIE = 0.97  # à moins de 3 % de rentabilité du n°1, l'item le plus joué passe devant
META_MIN_PICK = 0.05  # en dessous, l'item n'est pas « méta » sur ce champion


def opgg(cid, lane):
    path = CACHE / "opgg" / f"{cid}_{lane}.json"
    if path.exists() and time.time() - path.stat().st_mtime < META_TTL:
        return json.loads(path.read_text(encoding="utf-8"))
    data = http_json(OPGG.format(cid, lane), timeout=10)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def meta_items(champ, live_position, lane_arg):
    """Items joués sur ce champion dans cette lane : {item_id: taux de pick}, lane, patch OP.GG."""
    cid = champ["key"]  # id numérique Data Dragon
    lane = LANES.get((lane_arg or live_position or "").lower())
    if not lane:  # pas de lane (partie contre bots) : lane principale du champion
        first = opgg(cid, "TOP")["data"]["summary"].get("positions") or [{"name": "TOP"}]
        lane = max(first, key=lambda p: p.get("stats", {}).get("play", 0))["name"]
    d = opgg(cid, lane)
    D = d["data"]
    pick = {}
    for key in ("last_items", "boots"):
        for x in D.get(key, []):
            for iid in x["ids"]:
                pick[iid] = max(pick.get(iid, 0), x.get("pick_rate", 0))
    for x in D.get("core_items", []):
        for iid in x["ids"]:
            pick.setdefault(iid, x.get("pick_rate", 0))
    return {"lane": lane, "pick": pick, "patch": d.get("meta", {}).get("version")}


def ehp(me, share):
    """PV effectifs face à un ennemi dont `share` des dégâts sont physiques.
    Passifs défensifs en combat : Jak'Sho (résistances bonus), Force de la nature (RM), Rookern (bouclier)."""
    armor, mr, shield = me["armor"], me["mr"], 0.0
    for e in (e for iid in me["items"] for e in item_effects(iid) if e["type"] == "def"):
        armor += e.get("bonus_resist_mult", 0) * me["bonus_armor"]
        mr += e.get("bonus_resist_mult", 0) * me["bonus_mr"] + e.get("mr", 0)
        shield += e.get("magic_shield_hp", 0) * me["hp"]
    return share * me["hp"] * (1 + armor / 100) + (1 - share) * (me["hp"] + shield) * (1 + mr / 100)


def recommend(game, champs, items_db, result, style_arg=None, dmg_arg=None, lane_arg=None,
              use_meta=True):
    ap_live = game["activePlayer"]
    me_p = find_me(game)
    key = result["me"].get("key")
    if not key:
        return {"error": "Champion joué inconnu de Data Dragon."}
    champ, level = champs[key], ap_live.get("level", 1)
    try:
        meraki = cached_json(f"meraki/{key}.json", MERAKI.format(key))
    except Exception as e:  # noqa: BLE001
        return {"error": f"Ratios des sorts indisponibles (Meraki) : {e}"}
    ranks = result["me"]["abilities"]
    lines, ignored_units, spell_onhit = combo_lines(meraki, ranks, level, key)
    marksman = "Marksman" in champ.get("tags", [])
    n_autos = 3 if marksman else 1
    owned = [i.get("itemID") for i in me_p.get("items", [])]
    me = my_attack_stats(ap_live, champ, level)
    me["_as0"] = min(2.5, me["base_as"] * (1 + me["bonus_as"]))
    me["_spell_onhit"] = spell_onhit
    me["items"] = owned
    me["ap_mult"] = 1.0
    for iid in owned:
        for e in item_effects(iid):
            if e["type"] == "ap_mult":
                me["ap_mult"] *= 1 + e["value"]
    me["item_hp"] = item_totals([{"itemID": i} for i in owned], items_db)[0].get("hp", 0)
    style, dmg, why = pick_style(ap_live, champ, meraki, owned, items_db, style_arg, dmg_arg)
    w_dmg, w_tank = STYLE_WEIGHTS[style]

    # Candidats selon le style : un item qui n'apporte rien à ton style ne sort pas
    offense = OFFENSE[dmg]
    # Bruiser : l'item doit garder de l'offensif (sinon les PV effectifs, qui montent plus vite
    # en %, font sortir des items 100 % tank)
    wanted = {"degats": offense, "tank": DEFENSE, "bruiser": offense}[style]
    # Un item dont l'offensif est de l'autre type est exclu (Kata AP : pas de Couperet noir)
    own, other = PRIMARY[dmg], PRIMARY["ad" if dmg == "ap" else "ap"]
    has_boots = any(is_boots(items_db[i]) and i != 1001 for i in owned if i in items_db)
    cands = []
    for it in items_db.values():
        if it["id"] in owned or it["restricted"] or not it["purchasable"] or not it["completed"]:
            continue
        boots_t2 = is_boots(it) and 1001 in it["from"]
        if boots_t2 and has_boots:
            continue
        if not boots_t2 and it["price"] < MIN_LEGENDARY_PRICE:
            continue
        stats = set(it["stats"])
        if not stats & wanted or (stats & other and not stats & own):
            continue
        cands.append(it)

    # Filtre méta : seulement les items réellement joués sur ce champion dans cette lane
    meta, meta_note = None, "méta désactivée"
    if use_meta:
        try:
            meta = meta_items(champ, me_p.get("position"), lane_arg)
            kept = [it for it in cands if meta["pick"].get(it["id"], 0) >= META_MIN_PICK]
            if kept:
                cands, meta_note = kept, f"méta {meta['lane']}"
            else:
                meta_note = f"méta {meta['lane']} : aucun item joué ne colle à ton style, calcul seul"
        except Exception as e:  # noqa: BLE001
            meta_note = f"méta indisponible (OP.GG : {e}), calcul seul"

    enemies = [e for e in result["enemies"]]
    gold_mean = sum(e["item_gold"] for e in enemies) / len(enemies) if enemies else 0
    for e in enemies:  # menace = gold d'items / moyenne de l'équipe adverse
        e["threat"] = e["item_gold"] / gold_mean if gold_mean else 1.0
    w_sum = sum(e["threat"] for e in enemies) or 1.0
    base_dmg = {e["champion"]: combo_damage(me, e, lines, n_autos, marksman) for e in enemies}
    shares = {e["champion"]: enemy_ad_share(e, champs) for e in enemies}
    base_ehp = {c: ehp(me, s) for c, s in shares.items()}

    def wmean(d):
        return sum(e["threat"] * d[e["champion"]] for e in enemies) / w_sum

    scored = []
    for it in cands:
        m2 = with_item(me, it)
        g_dmg, g_tank = {}, {}
        for e in enemies:
            c, before = e["champion"], base_dmg[e["champion"]]
            g_dmg[c] = (combo_damage(m2, e, lines, n_autos, marksman) - before) / before if before else 0.0
            g_tank[c] = ehp(m2, shares[c]) / base_ehp[c] - 1
        per_enemy = {c: w_dmg * g_dmg[c] + w_tank * g_tank[c] for c in g_dmg}
        gain = wmean(per_enemy)
        if gain <= 0:
            continue
        scored.append({"id": it["id"], "name": it["name"], "price": it["price"],
                       "cost": remaining_cost(it["id"], owned, items_db), "gain": gain,
                       "gain_dmg": wmean(g_dmg), "gain_tank": wmean(g_tank),
                       "value": gain / it["price"] * 1000, "per_enemy": per_enemy})
    scored.sort(key=lambda s: -s["value"])
    if not scored:
        return {"error": "Aucun item candidat ne fait monter tes dégâts (ratios du champion introuvables ?)."}
    for s in scored:
        s["pick"] = meta["pick"].get(s["id"], 0) if meta else 0
    if meta:  # quasi-égalité : le plus joué passe devant
        close = [s for s in scored if s["value"] >= META_TIE * scored[0]["value"]]
        best = max(close, key=lambda s: s["pick"])
        scored.remove(best)
        scored.insert(0, best)
    top = scored[0]
    gold = result["me"]["gold"]
    inv = inventory_advice(me_p, top, owned, gold, items_db)
    gold_after = gold + (inv["sell"]["sell"] if inv["sell"] else 0)
    now = buy_now(top["id"], owned, gold_after, items_db) if gold_after < top["cost"] else None
    return {"top": scored[:3], "buy_now": now, "gold": gold, "gold_after": gold_after, "inv": inv,
            "objectives": result["objectives"], "lines": lines,
            "style": style, "dmg": dmg, "why": why, "meta_note": meta_note, "spell_onhit": spell_onhit,
            "meta_patch": meta["patch"] if meta else None,
            "ignored_units": ignored_units, "n_autos": n_autos,
            "meraki_patch": meraki.get("patchLastChanged")}


def fmt_reco(r):
    if "error" in r:
        return f"→ Pas de reco : {r['error']}"
    t = r["top"][0]
    # « surtout contre » : les ennemis où l'item rapporte nettement plus que la moyenne
    strong = sorted((c for c, g in t["per_enemy"].items() if g >= 1.2 * t["gain"]),
                    key=lambda c: -t["per_enemy"][c])[:2]
    if r["style"] == "degats":
        what = f"+{t['gain_dmg']:.0%} de dégâts sur l'équipe"
    elif r["style"] == "tank":
        what = f"+{t['gain_tank']:.0%} de tankiness face à leur équipe"
        if t["gain_dmg"] >= 0.05:
            what += f" (+{t['gain_dmg']:.0%} de dégâts)"
    else:
        what = f"+{t['gain_dmg']:.0%} de dégâts et +{t['gain_tank']:.0%} de tankiness"
    line = f"→ {t['name']} : {what}"
    line += f", surtout contre {' et '.join(strong)}." if strong else "."
    sell = r["inv"]["sell"]
    if sell:
        line += f" Vends {sell['name']} (+{sell['sell']} PO, {r['inv']['reason']})."
    if r["buy_now"]:
        line += f" (achetable maintenant : {r['buy_now']['name']}, {r['buy_now']['price']} PO)"
    elif r["gold_after"] < t["cost"]:
        line += f" (il te manque {t['cost'] - r['gold_after']} PO)"
    meta = r["meta_note"] if r["meta_note"].startswith("méta ") and ":" not in r["meta_note"] \
        else "sans méta"
    line += f" [{STYLE_LABEL[r['style']]} {r['dmg'].upper()} · {r['why']} · {meta}]"
    # Buffs ennemis qui changent le fight maintenant
    for b in r["objectives"]["enemy"]["timed"]:
        line += f" ⚠ {b['nom']} ennemi encore {b['reste'] // 60}:{b['reste'] % 60:02d} ({b['joueurs']} joueurs)"
    return line


def fmt_objectives(obj):
    def side(s):
        d = ", ".join(f"{BUFFS['dragons'][k]['nom']} x{n}" for k, n in s["drakes"].items()) or "aucun drake"
        if s["soul"]:
            d += f" · âme {s['soul']}"
        for b in s["timed"]:
            d += f" · {b['nom']} {b['reste'] // 60}:{b['reste'] % 60:02d} ({b['joueurs']} joueurs)"
        return d
    return f"OBJECTIFS : toi {side(obj['ally'])} | eux {side(obj['enemy'])}"


def fmt_inventory(inv):
    L = [f"INVENTAIRE ({inv['free']} slot(s) libre(s){', fin de game' if inv['late'] else ''})"]
    for row in inv["rows"]:
        verdict = "garde" if not row["prio"] else f"vendable +{row['sell']} PO"
        L.append(f"  {row['name']} : {verdict} ({row['why']})")
    return "\n".join(L)


def fmt_reco_details(r):
    if "error" in r:
        return ""
    wd, wt = STYLE_WEIGHTS[r["style"]]
    L = ["\n" + fmt_objectives(r["objectives"]), fmt_inventory(r["inv"]),
         f"\nSTYLE : {STYLE_LABEL[r['style']]} {r['dmg'].upper()} ({r['why']}) · score = "
         f"{wd:.0%} dégâts + {wt:.0%} tankiness (PV effectifs face au mix AD/AP adverse)",
         f"MÉTA : {r['meta_note']}" + (f" (OP.GG patch {r['meta_patch']}, Émeraude+)" if r["meta_patch"] else ""),
         "TOP 3 (rentabilité = % de score gagné pour 1000 PO ; pick = joué dans x % des parties)"]
    for i, t in enumerate(r["top"], 1):
        per = " · ".join(f"{c} +{g:.0%}" for c, g in sorted(t["per_enemy"].items(), key=lambda x: -x[1]))
        L.append(f"  {i}. {t['name']} ({t['price']} PO, reste {t['cost']}) : +{t['gain']:.1%} "
                 f"(dégâts +{t['gain_dmg']:.0%}, tankiness +{t['gain_tank']:.0%}) "
                 f"· {t['value']:.1%}/1000 PO · pick {t['pick']:.0%}\n     {per}")
    L.append(f"\nCOMBO MODÉLISÉ ({r['n_autos']} auto(s) + sorts, ratios Meraki patch {r['meraki_patch']})")
    for name, dtype, terms in r["lines"]:
        L.append(f"  {name} [{dtype}] : " + " + ".join(
            f"{v:g}" if s == "flat" else f"{v:.0%} {s}" for s, v in terms))
    if r["ignored_units"]:
        L.append(f"  Ratios ignorés (unités non gérées) : {', '.join(r['ignored_units'])}")
    if r["spell_onhit"]:
        L.append(f"  Effets à l'impact appliqués par les sorts : {r['spell_onhit']:.1f} coup(s) par combo")
    mod = [t["name"] for t in r["top"] if str(t["id"]) in PASSIVES]
    L.append(f"⚠ Passifs modélisés ({len(PASSIVES) - 2} items, passifs.json) : {', '.join(mod) or 'aucun du top 3'}. "
             "Les autres passifs sont ignorés ; valeurs Meraki parfois datées.")
    return "\n".join(L)


# ---------------------------------------------------------------- affichage
def fmt_text(r):
    m = r["me"]
    L = [f"Patch {r['patch']} · {r['game_time'] // 60}:{r['game_time'] % 60:02d}",
         f"\nTOI : {m['champion']} niv. {m['level']} · {m['gold']} PO · sorts {m['abilities']}",
         f"  Items : {', '.join(m['items']) or '—'}"]
    s = m["stats_live"]
    L.append(f"  AD {s['attackDamage']:.0f} · AP {s['abilityPower']:.0f} · AS {s['attackSpeed']:.2f} · "
             f"crit {s['critChance']:.0%} · armure {s['armor']:.0f} · RM {s['magicResist']:.0f} · "
             f"PV {s['maxHealth']:.0f}")
    L.append(f"  Léthalité {s['physicalLethality']:.0f} · péné armure {s['armorPenetrationPercent']} · "
             f"péné magique flat {s['magicPenetrationFlat']:.0f} / % {s['magicPenetrationPercent']}")
    c = m["check"]
    L.append(f"  Contrôle formule (calculé / réel) : armure {c['armor_calc']}/{c['armor_live']:.0f} · "
             f"RM {c['mr_calc']}/{c['mr_live']:.0f} · PV {c['hp_calc']}/{c['hp_live']:.0f}")
    ra = m.get("ratios", {})
    L.append(f"  Ratios des sorts (Meraki) : {'OK' if ra.get('ok') else 'ABSENTS'}"
             f" · {ra.get('ratios', 0)} ratios · données patch {ra.get('patch', '?')}")
    L.append("\nENNEMIS (stats recalculées)")
    L.append(f"  {'Champion':<14}{'Niv':>4}{'KDA':>9}{'PV':>7}{'Armure':>8}{'RM':>6}"
             f"{'AD':>6}{'AP':>6}{'%AD':>6}{'Items PO':>10}")
    for e in sorted(r["enemies"], key=lambda e: -e["item_gold"]):
        share = f"{e['ad_share']:.0%}" if e["ad_share"] is not None else "—"
        L.append(f"  {e['champion'][:13]:<14}{e['level']:>4}{e['kda']:>9}{e['hp']:>7}{e['armor']:>8}"
                 f"{e['mr']:>6}{e['ad']:>6}{e['ap']:>6}{share:>6}{e['item_gold']:>10}")
    L.append("\n⚠ " + "\n⚠ ".join(r["warnings"]))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="lol-items V1")
    ap.add_argument("--demo", help="allgamedata.json enregistré à rejouer")
    ap.add_argument("--save", help="enregistre l'allgamedata live dans ce fichier")
    ap.add_argument("--details", action="store_true", help="top 3 chiffré + stats des ennemis")
    ap.add_argument("--json", action="store_true", help="sortie JSON")
    ap.add_argument("--style", choices=["degats", "bruiser", "tank"],
                    help="force le style (sinon : rune principale, puis rôle du champion)")
    ap.add_argument("--dmg", choices=["ad", "ap"], help="force le type de dégâts (sinon : tes items)")
    ap.add_argument("--lane", choices=sorted(LANES), help="force la lane (sinon : API live, puis lane principale)")
    ap.add_argument("--no-meta", action="store_true", help="calcul seul, sans filtre méta OP.GG")
    a = ap.parse_args()

    if a.demo:
        game = json.loads(Path(a.demo).read_text(encoding="utf-8"))
    else:
        try:
            game = http_json(LIVE_URL, insecure_local=True, timeout=5)
        except Exception:  # noqa: BLE001
            sys.exit("Pas de partie en cours détectée (API locale 127.0.0.1:2999 injoignable). "
                     "Lance une game (même en entraînement) puis relance.")
        if a.save:
            Path(a.save).write_text(json.dumps(game, ensure_ascii=False, indent=1), encoding="utf-8")

    version, champs, items_en, items_fr = load_static()
    items_db = build_items(items_en, items_fr)
    result = analyse(game, champs, items_db, version)
    reco = recommend(game, champs, items_db, result, a.style, a.dmg, a.lane, not a.no_meta)
    if a.json:
        result["reco"] = {k: v for k, v in reco.items() if k != "lines"}
        result["reco"]["phrase"] = fmt_reco(reco)
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif a.details:
        print(fmt_reco(reco) + "\n")
        print(fmt_text(result) + fmt_reco_details(reco))
    else:
        print(fmt_reco(reco))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows
    main()

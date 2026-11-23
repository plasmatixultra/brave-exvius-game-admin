"""Generate the English name overlays from the community data dumps.

The game ships no English for story areas (verified against every decoded text table), and the wiki
only exposes English as prose. Two community dumps carry the whole catalogue keyed by the SAME story
key our master table uses (`F_STORY_SUB_MST.6uIYE15X`), so the join is exact — no ordering, no
guessing:

    https://raw.githubusercontent.com/aEnigmatic/ffbe/master/dungeons.json   # names[0] = English
    https://raw.githubusercontent.com/aEnigmatic/ffbe/master/worlds.json     # subregion names
    https://raw.githubusercontent.com/kaikwb/ffbe-data-jp/master/dungeons.json  # JP cross-check

    python3 tools/gen_area_names.py            # report coverage
    python3 tools/gen_area_names.py --write    # rewrite data/*.tsv
"""

import json
import os
import sys
import urllib.request

BASE = "https://raw.githubusercontent.com/aEnigmatic/ffbe/refs/heads/master"
CACHE = "scratch/cache/ffbe-data-en"
MST = "scratch/cache/data/mst_decoded/F_STORY_SUB_MST.ndjson"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dump(name):
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        url = "%s/%s" % (BASE, name)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl/8"}), timeout=120) as r:
            open(path, "wb").write(r.read())
    return json.load(open(path, encoding="utf-8"))


def story_areas():
    with open(os.path.join(ROOT, MST), encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return [row for row in rows if str(row.get("Lxj5u3WB")) == "3"]


def subregion_names(worlds):
    out = {}
    for world in worlds.values():
        for region in world.get("regions", {}).values():
            for sub_id, sub in region.get("subregions", {}).items():
                names = sub.get("names") or []
                if names:
                    out[sub_id] = names[0]
    return out


def main(argv):
    dungeons = dump("dungeons.json")
    worlds = dump("worlds.json")
    areas = story_areas()
    subs = subregion_names(worlds)

    area_rows, missing, by_region = {}, [], {}
    for row in areas:
        key = str(row["6uIYE15X"])
        area_id = str(row["6a9TDRcj"])
        entry = dungeons.get(key) or {}
        names = entry.get("names") or []
        if names and names[0].strip():
            area_rows[area_id] = names[0].strip()
        else:
            missing.append(key)
        sub = entry.get("subregion_id")
        by_region.setdefault(int(row["0J2VIT8w"]), {}).setdefault(sub, []).append(area_id)

    print("story areas: %d | English names: %d | unresolved: %d %s"
          % (len(areas), len(area_rows), len(missing), missing[:5]))

    region_rows = {}
    for region_id, subs_seen in by_region.items():
        # Our regions are the table's internal groupings; several span the game's own subregions
        # (region 9 is Pharm + Land of the Crystals + Wandering Lands). Label with every subregion
        # that holds at least a fifth of the region's areas, in the order they are walked.
        total = sum(len(ids) for ids in subs_seen.values())
        parts, seen = [], set()
        for area_id in sorted((a for ids in subs_seen.values() for a in ids), key=int):
            sub = next(s for s, ids in subs_seen.items() if area_id in ids)
            if sub in seen or len(subs_seen[sub]) < max(2, total * 0.2):
                continue
            seen.add(sub)
            parts.append(subs.get(sub) or "Region %d" % region_id)
        region_rows[region_id] = " · ".join(parts) or (subs.get(max(subs_seen, key=lambda s: len(subs_seen[s]))) or "Region %d" % region_id)
    print("regions labelled from their subregions:", len(region_rows))

    if "--write" not in argv:
        for region_id in sorted(region_rows)[:6]:
            print("  region %-2d %s" % (region_id, region_rows[region_id]))
        return 0

    with open(os.path.join(ROOT, "data", "area_names_en.tsv"), "w", encoding="utf-8") as fh:
        fh.write("# area_id\tglobal name — joined on the story key against aEnigmatic/ffbe dungeons.json\n")
        for area_id in sorted(area_rows, key=lambda value: (len(value), value)):
            fh.write("%s\t%s\n" % (area_id, area_rows[area_id]))
    with open(os.path.join(ROOT, "data", "region_names_en.tsv"), "w", encoding="utf-8") as fh:
        fh.write("# region_id\tglobal name — the game's own subregion name for the region's largest part\n")
        for region_id in sorted(region_rows):
            fh.write("%s\t%s\n" % (region_id, region_rows[region_id]))
    print("wrote", len(area_rows), "area names and", len(region_rows), "region names")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

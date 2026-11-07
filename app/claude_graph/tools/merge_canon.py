"""Merge claude_graph/canon/*.json into backend/sorghum_intel/canonical_map.json."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = root.parent / "backend" / "sorghum_intel" / "canonical_map.json"
merged: dict = {}
for f in sorted((root / "canon").glob("*.json")):
    for etype, groups in json.loads(f.read_text()).items():
        for canon, variants in groups.items():
            cur = merged.setdefault(etype.upper(), {}).setdefault(canon, [])
            for v in variants:
                if v != canon and v not in cur:
                    cur.append(v)
out.write_text(json.dumps(merged, indent=1, ensure_ascii=False))
print({t: len(g) for t, g in merged.items()}, "groups;", sum(len(v) for g in merged.values() for v in g.values()), "variants")

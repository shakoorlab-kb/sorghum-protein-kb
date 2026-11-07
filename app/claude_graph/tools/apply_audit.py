"""Apply independent-audit verdicts to the extraction files.

unsupported -> claim deleted; other verdicts -> the reviewer's field fixes applied.
Originals are kept in extractions_preaudit/ (first run only). Writes audit/report.json.
"""
import json, shutil, sys
from collections import Counter, defaultdict
from pathlib import Path

root = Path(__file__).resolve().parents[1]
ext, backup = root / "extractions", root / "extractions_preaudit"
if not backup.exists():
    shutil.copytree(ext, backup)

issues, total, ok = [], 0, 0
for f in sorted((root / "audit").glob("verdicts_*.json")):
    v = json.loads(f.read_text())
    total += int(v.get("total", 0)); ok += int(v.get("ok", 0))
    issues += v.get("issues", [])

by_file = defaultdict(dict)
for it in issues:
    name, _, idx = it["ref"].rpartition("#")
    by_file[name][int(idx)] = it

counts, applied = Counter(), Counter()
for name, items in by_file.items():
    # Always edit from the pre-audit original so re-running is idempotent.
    data = json.loads((backup / name).read_text())
    kept = []
    for i, c in enumerate(data.get("claims", [])):
        it = items.get(i)
        if it:
            counts[it["verdict"]] += 1
            if it["verdict"] == "unsupported" or (it["verdict"] == "entity" and not it.get("fix")):
                applied["deleted"] += 1
                continue
            for k, val in (it.get("fix") or {}).items():
                if k in ("relation", "kind", "subject", "object", "value", "subject_type", "object_type") and (val or k == "value"):
                    c[k] = val
                    applied[f"fixed:{k}"] += 1
            c["audit"] = it["verdict"]
        kept.append(c)
    data["claims"] = kept
    (ext / name).write_text(json.dumps(data, indent=1, ensure_ascii=False))

report = {"judged": total, "ok": ok, "precision_before_fixes": round(ok / total, 4) if total else None,
          "issues": dict(counts), "applied": dict(applied)}
(root / "audit" / "report.json").write_text(json.dumps(report, indent=1))
print(json.dumps(report, indent=1))

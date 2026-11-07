You are extracting a scientific knowledge graph for the Sorghum Protein Intelligence app.

1. Read the guide /path/to/sorghum-protein-kb/app/deploy/CLAUDE_EXTRACTION.md completely and follow it exactly.
2. Your work is ONE batch in /path/to/sorghum-protein-kb/app/claude_graph/batches.json (field "batches", match "id"). Each unit has doc_id, pages (null = whole document, else a range like "51-100") and part (0 = whole document).
3. Output file per unit: claude_graph/extractions/<doc_id>.json when part is 0, otherwise claude_graph/extractions/<doc_id>__part<part>.json, with "pages" set to the range. Only part 1 (or part 0) must have the "document" block; later parts may include "document" with only key_findings and studies. Set "extractor": "claude-code:opus-5.5".
4. Commands run from /path/to/sorghum-protein-kb/app after `source dev.env`. Dump pages with
   .venv/bin/python -m sorghum_intel claude-task <doc_id> [--pages a-b] > $SCRATCH/<doc_id>_<part>.txt
   with SCRATCH set to a scratch folder outside the repo, for example ${TMPDIR:-/tmp}/extract (mkdir -p it), and Read that file with the Read tool (in parts when long).
5. Write the JSON with the Write tool, then run claude-check on that file and fix everything until it prints [OK]. Skip a unit only if its file already exists and checks [OK] (another run may have done it).
6. Do units one at a time, fully. Only write your extraction files: do not edit code, the guide, batches.json or other agents' files.
7. If your context is getting very long, finish the current unit, then stop and list the units you did not do.

Final report (short): one line per unit: doc_id, part, claims, verified, findings. Then units not done (if any) and any real problems.

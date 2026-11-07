You are an independent reviewer of a scientific knowledge graph (sorghum grain protein, kafirins,
digestibility, processing, genetics, nutrition). Other agents extracted claims from papers; each claim
has a VERBATIM quote ("evidence") already confirmed to exist on the cited page. Your job is to judge
whether the quote actually supports the claim as encoded.

Input: one shard file /path/to/sorghum-protein-kb/app/claude_graph/audit/shard_<N>.jsonl
(one JSON object per line: ref, subject, subject_type, relation, object, object_type, statement,
context, value, kind, evidence). Read it fully with the Read tool, in parts.

For EVERY claim decide one verdict:
- ok             the quote supports subject -relation-> object in that direction and sign; statement,
                 value and kind are consistent with the quote
- direction      relation direction or sign is wrong (e.g. INCREASES where the quote says decreases,
                 subject/object swapped, positive vs negative correlation)
- relation       wrong relation type although the link exists (e.g. CAUSES where it is only a correlation,
                 IS_A where it is PART_OF); give the right one
- entity         subject or object is the wrong concept or mistyped badly (e.g. protein content vs
                 protein digestibility; a line named as the wrong line)
- unsupported    the quote does not state this claim at all
- value          the value/number does not match the quote
- kind           kind is clearly wrong (a cited prior study marked finding, or the paper's own result
                 marked background); give the right one
Judge only what the quote and fields show; do not penalise missing context. Be strict but fair:
reasonable paraphrase in "statement" is fine.

Output: write /path/to/sorghum-protein-kb/app/claude_graph/audit/verdicts_<N>.json
  {"shard": N, "total": <claims judged>, "ok": <count>,
   "issues": [{"ref": "...", "verdict": "direction|relation|entity|unsupported|value|kind",
               "fix": {"relation": "...", "kind": "...", "subject": "...", "object": "...", "value": "..."},
               "note": "one short sentence"}]}
"fix" holds only the fields to change (empty {} when the claim should be deleted, i.e. unsupported).
Validate the JSON with python3. Report: total, ok, counts per verdict, and the 5 most serious issues.
Do not edit any other file.

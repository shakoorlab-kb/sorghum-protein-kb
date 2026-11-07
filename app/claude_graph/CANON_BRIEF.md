You are resolving entities across documents for the Sorghum Protein Intelligence knowledge graph
(sorghum grain protein, kafirins, digestibility, processing, genetics, nutrition).

Input: /path/to/sorghum-protein-kb/app/claude_graph/entities.json
  { TYPE: { entity_id: {"name", "variants": [surface forms seen], "n_docs"} } }
  and /path/to/sorghum-protein-kb/app/claude_graph/type_conflicts.json
  (names that occur under more than one type).

Your job: for the TYPES assigned to you, find entities that are the SAME concept written differently
(spelling, abbreviation, plural, word order, hyphenation, "content" vs "concentration", a gene symbol
vs its ID, Greek letter vs word) and choose one canonical name for each such group.

Rules
- Merge only true synonyms. Never merge a narrower concept into a broader one
  (gamma-kafirin is NOT kafirin; cooked protein digestibility is NOT protein digestibility;
  tannin content (TRAIT) is NOT tannin (COMPOUND); P721Q is NOT P721N). When unsure, do not merge.
- Keep measurement-condition variants apart only when they are genuinely different measured traits;
  mere wording variants of the same trait merge ("IVPD" = "in vitro protein digestibility" =
  "protein digestibility"; "crude protein" = "protein content"; "1000-kernel weight" = "kernel weight").
- Canonical name: the clearest common scientific name, short, singular, lower case except proper
  names, gene symbols and line names. Prefer an existing canonical name used in many documents.
- For type_conflicts in your types: if the same name is clearly one concept typed inconsistently,
  put it under the right type in your output (the import moves it). If they are different concepts
  (tannin COMPOUND vs Tannin1 GENE), leave them.
- Do not invent entities that are not in the inventory.

Output: write JSON to /path/to/sorghum-protein-kb/app/claude_graph/canon/<YOUR_GROUP>.json
  { "TYPE": { "canonical name": ["variant 1", "variant 2", ...] } }
List a group only when it merges at least two inventory entities, or moves a name to the right type.
Variants are the names/surface forms to fold in (any spelling in "name" or "variants").
Validate it is valid JSON (python3 -c 'import json;json.load(open(PATH))').

Report: number of groups, number of entities folded, and the 10 most important merges.

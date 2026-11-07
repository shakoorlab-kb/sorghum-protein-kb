# Knowledge-graph extraction guide (for Claude Code agents)

You are building the knowledge graph of the Sorghum Protein Intelligence app: sorghum grain
protein quality, kafirins, digestibility, grain composition, processing, genetics, breeding
and nutrition. Your output is loaded into Neo4j. Every claim you write becomes a link that
researchers click to see the highlighted sentence on the original PDF page, so **precision and
verbatim quotes matter more than volume**.

## Commands (run from /path/to/sorghum-protein-kb/app)

```bash
source dev.env
# 1. Dump the document's pages to a file, then Read the file (in parts if long):
.venv/bin/python -m sorghum_intel claude-task <DOC_ID> [--pages a-b] > $SCRATCH/<DOC_ID>.txt
# 2. Write claude_graph/extractions/<DOC_ID>.json   (or <DOC_ID>__part<N>.json for a page range)
# 3. Check it; fix everything it reports; repeat until it prints [OK]:
.venv/bin/python -m sorghum_intel claude-check claude_graph/extractions/<DOC_ID>*.json
```

Read the WHOLE document (all assigned pages) before writing, so names and findings are
consistent across pages. Pages marked "reference list or near-empty page: skip" need nothing.

## File format

```json
{
  "doc_id": "doi_10_1002_cche_10643",
  "extractor": "claude-code:opus",
  "pages": "1-12",
  "document": {
    "summary": "2-4 sentences: what was studied, on what material, how, and the main result.",
    "study_type": "experimental | review | patent | thesis | methods | survey | modelling | other",
    "key_findings": ["5-10 one-sentence findings with numbers where given"],
    "studies": [{"name": "sorghum association panel", "type": "GERMPLASM", "role": "material"},
                {"name": "protein digestibility", "type": "TRAIT", "role": "measured trait"},
                {"name": "pepsin digestion assay", "type": "METHOD", "role": "method"}]
  },
  "entities": [
    {"name": "gamma-kafirin", "type": "PROTEIN", "aliases": ["γ-kafirin", "27 kDa γ-kafirin"],
     "description": "cysteine-rich kafirin subclass at the protein body periphery"}
  ],
  "claims": [
    {"page": 3,
     "subject": "tannin", "subject_type": "COMPOUND",
     "relation": "DECREASES",
     "object": "protein digestibility", "object_type": "TRAIT",
     "statement": "Tannin content was negatively related to in vitro protein digestibility across the SAP.",
     "context": "uncooked grain; 254 SAP accessions; pepsin assay",
     "value": "R² = 0.65, p < .001",
     "kind": "finding",
     "evidence": "Tannin content recorded a significant negative relationship (R2 = 0.65, p < .001) with protein digestibility",
     "confidence": 0.95}
  ]
}
```

For a document split into page ranges, only `__part1` needs the `document` block (later parts
may add `key_findings` and `studies`); every part lists the entities its claims use.

## Entity types (use exactly these)

| type | what | examples |
|---|---|---|
| GENE | genes, loci, transgenes, gene IDs, transcription factors | Opaque2, k1C, Sobic.005G189000, kafirin gene family |
| PROTEIN | proteins, protein fractions, enzymes | kafirin, gamma-kafirin, pepsin, granule-bound starch synthase |
| TRAIT | measurable properties | protein digestibility, protein content, lysine content, grain hardness, kernel weight |
| PROCESS | food processing, treatments, biological processes, breeding/editing techniques | wet cooking, fermentation, malting, CRISPR, protein body formation |
| COMPOUND | chemicals, nutrients, bonds | tannin, phytic acid, starch, lysine, disulfide bond, 2-mercaptoethanol |
| STRUCTURE | grain, tissue, cell or protein structures | protein body, endosperm, testa, protein-starch matrix |
| PHENOTYPE | named phenotypes or mutant classes | highly digestible phenotype, floury endosperm, opaque endosperm |
| GERMPLASM | accessions, cultivars, lines, mutants, hybrids, populations, panels, races | P721Q, BTx623, KAT369, SC1056, Caudatum, sorghum association panel |
| ORGANISM | species | Sorghum bicolor, Zea mays |
| METHOD | assays, analytical, statistical and genetic methods | pepsin digestion assay, SDS-PAGE, GWAS, near-infrared spectroscopy |
| MARKER | QTL, SNPs, markers, chromosomal regions | qPD1.1, SNP S5_1234567 |
| LOCATION | countries, regions, environments when part of a finding | Ethiopia, Kansas |
| FOOD | foods and products | porridge, injera, ugali, flour, biscuit |

## Relations (direction: subject → object)

| relation | use when the text says the subject … the object |
|---|---|
| INCREASES / DECREASES | raises / lowers a quantity (tannin DECREASES protein digestibility) |
| IMPROVES / IMPAIRS | makes a quality better / worse (when "more" is not the point) |
| NO_EFFECT_ON | was tested and did not change it (a negative result is a finding, keep it) |
| AFFECTS | changes it, direction not stated |
| CAUSES | leads to an outcome or state (wet cooking CAUSES disulfide cross-linking) |
| CORRELATES_WITH | statistical correlation (put r / R² and sign in `value`, sign also in `statement`) |
| ASSOCIATED_WITH | linked, e.g. a marker with a trait, no direction or mechanism |
| ENCODES | gene → protein |
| REGULATES | gene/protein controls expression or activity of another |
| INTERACTS_WITH | physical or chemical interaction (tannin INTERACTS_WITH kafirin) |
| PART_OF / CONTAINS | composition (gamma-kafirin PART_OF protein body; protein body CONTAINS kafirin) |
| IS_A | class membership: gamma-kafirin IS_A kafirin; kafirin IS_A prolamin; P721Q IS_A highly digestible phenotype is wrong (use HAS_TRAIT) |
| HAS_TRAIT | germplasm/organism shows a trait or phenotype (P721Q HAS_TRAIT highly digestible phenotype) |
| DERIVED_FROM | line/material derived from another (P721Q DERIVED_FROM P721N) |
| TARGETS | an intervention aimed at a gene/protein (CRISPR TARGETS k1C) |
| MEASURED_BY | trait/compound → the METHOD that measures it |

## Names

* One concept, one name, everywhere. Prefer these canonical names: kafirin, alpha-kafirin,
  beta-kafirin, gamma-kafirin, delta-kafirin, prolamin, zein, protein digestibility,
  protein content, lysine content, grain hardness, endosperm vitreousness, starch
  digestibility, total phenolic content, wet cooking, fermentation, germination, malting,
  extrusion, decortication, tannin, phenolic compound, phytic acid, disulfide bond,
  protein body, protein-starch matrix, endosperm, testa, highly digestible phenotype,
  floury endosperm, Sorghum bicolor, Zea mays, pepsin digestion assay, INFOGEST, SDS-PAGE,
  GWAS, QTL mapping, near-infrared spectroscopy, sorghum association panel, P721Q.
* Short noun phrases (at most 5 words), singular, lower case except proper names, gene
  symbols and line names. Qualifiers go in `context`, not the name: "protein digestibility"
  with context "cooked flour", not "cooked flour protein digestibility". Exception: keep
  "cooked protein digestibility" vs "raw protein digestibility" only when the paper treats
  them as distinct measured traits; otherwise use context.
* Greek letters as words (gamma-kafirin). Symbol-font PDFs show "g-kafirin" or "a-kafirin":
  that is gamma / alpha.
* Line and accession IDs exactly as written (P721Q, BTx623, PI 533919, SC1056).

## Conventions for common cases

* **Group comparisons** ("non-tannin accessions had higher protein content than tannin
  accessions"): `<group> HAS_TRAIT <trait>` for the group the sentence is about, with the
  direction and numbers in `value` ("higher: 14.6% vs 14.2%, p = .031") and the comparison group
  in `context`. If the text names the factor that separates the groups, ALSO add the factor
  claim when it is stated as an effect (tannin DECREASES protein content).
* **HAS_TRAIT** subjects may be GERMPLASM, ORGANISM or PHENOTYPE groups (red grain, white
  grain, waxy endosperm). Grain colours are PHENOTYPE ("red grain").
* **Signed associations**: statistical relationships use CORRELATES_WITH with the sign and
  statistic in `value` ("negative, r = -0.46"); use INCREASES/DECREASES only when the text
  states an effect. **Null results** (no significant difference, no correlation) are
  NO_EFFECT_ON with `value` "n.s." and the test in `context`.
* **Hedged main conclusions** of a research article ("appears to be caused by", "we believe")
  are still the paper's findings: kind finding, confidence 0.7. Use hypothesis only for
  speculation beyond the paper's data (future work, possible mechanisms not tested).
* The authors' own earlier published results are background. Material descriptions and
  pedigrees (P851171 DERIVED_FROM P721Q) are kind method.
* **Sorghum races** are GERMPLASM named "kafir race", "durra race", "caudatum race",
  "guinea race", "bicolor race" (never just "kafir", which collides with kafirin).
* Genes vs mutants: the gene is GENE ("Floury2", "Opaque2"); a mutant line or class is
  GERMPLASM ("floury2 mutant", "opaque2 maize").
* Locations within protein bodies: "protein body periphery", "protein body interior"
  (STRUCTURE), PART_OF "protein body".
* **One claim per (subject, relation, object, conditions)** per document. When the abstract and
  Results say the same thing, keep the Results sentence (it has the numbers). Per-line claims
  that share one quote are fine when each line has its own value.
* Canonical method names: pepsin digestion assay, SKCS, vanillin-HCl assay, Folin-Ciocalteu
  assay, Western blot, ELISA, SDS-PAGE, RP-HPLC, transmission electron microscopy, scanning
  electron microscopy, confocal laser scanning microscopy, near-infrared spectroscopy, GWAS,
  QTL mapping, amino acid analysis. Traits: kernel weight, kernel diameter.

## Claims

* `kind`: **finding** = a result of this document's own data (or, in a review, its own
  synthesised conclusion); **background** = a statement attributed to other work (intro,
  discussion with citations); **method** = how something was done or measured;
  **hypothesis** = proposed, speculative or "may" statements. Mark honestly; the app ranks
  findings above background.
* `evidence` is copied **character for character** from ONE page of the task text, as one
  contiguous span (one or two sentences, up to ~300 characters). Do not paraphrase, fix typos,
  merge two places, or add ellipses. Line breaks may become single spaces. A word hyphenated
  across a line break ("bod-\nies") may be written whole ("bodies"); a real hyphen at a line
  end ("dark-\nstaining") is written "dark-staining". In line-numbered manuscripts you may
  leave out the margin line numbers that interrupt a sentence. `page` is that page.
* `value`: numbers with units and statistics, as stated (e.g. "IVPD 82.4% vs 58.1% (cooked)",
  "r = -0.83", "+15%"). Empty if none.
* `context`: conditions that limit the claim: raw vs cooked, assay, material/population,
  environment, sample size.
* `confidence`: 0.9-1.0 explicit result with data; 0.7-0.85 clearly stated but indirect;
  0.5-0.65 hedged.
* Coverage: every result in Results/Discussion that has a direction or a number, every
  gene-protein-trait link, every processing effect, every germplasm with a notable trait value,
  key mechanisms, and class hierarchy (IS_A) where the text establishes it. Typically 4-10
  claims per content page of a research article; fewer for methods pages. Theses and reviews:
  focus on findings and synthesised conclusions, do not transcribe every citation.
* Do not make claims whose only content is "X is in sorghum" or "the study measured X".
  Do not extract from reference lists, affiliations, acknowledgements or journal boilerplate.

## Before you run the check: verify every claim yourself

For each claim ask: does the quoted text, read alone, support subject → relation → object in
that direction, with that sign? Is `kind` right? Are both names canonical? Fix or delete the
claim if not. Then run `claude-check`: it searches every quote on its page and lists
anything not found. Correct each one by copying the exact text from the task file (or delete
the claim). Do not finish a document until the check prints `[OK]`.

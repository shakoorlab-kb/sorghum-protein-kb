/** One color per entity type, used by chips, graph nodes and charts alike. */
export const TYPE_COLORS: Record<string, string> = {
  GENE: "#7c3aed",
  PROTEIN: "#db2777",
  TRAIT: "#0891b2",
  PROCESS: "#ea580c",
  COMPOUND: "#16a34a",
  STRUCTURE: "#b45309",
  PHENOTYPE: "#e11d48",
  GERMPLASM: "#65a30d",
  ORGANISM: "#0d9488",
  METHOD: "#4f46e5",
  MARKER: "#9333ea",
  LOCATION: "#64748b",
  FOOD: "#ca8a04",
  DOCUMENT: "#475569",
};

export const TYPE_LABELS: Record<string, string> = {
  GENE: "Gene", PROTEIN: "Protein", TRAIT: "Trait", PROCESS: "Process", COMPOUND: "Compound",
  STRUCTURE: "Structure", PHENOTYPE: "Phenotype", GERMPLASM: "Germplasm", ORGANISM: "Organism",
  METHOD: "Method", MARKER: "Marker", LOCATION: "Location", FOOD: "Food", DOCUMENT: "Document",
};

export const ENTITY_TYPES = Object.keys(TYPE_COLORS).filter((t) => t !== "DOCUMENT");

export function typeColor(t?: string | null): string {
  return TYPE_COLORS[(t || "").toUpperCase()] ?? "#64748b";
}

export function typeLabel(t?: string | null): string {
  return TYPE_LABELS[(t || "").toUpperCase()] ?? (t || "Entity");
}

/** Relation verbs read better in sentence case. */
export function relLabel(r: string): string {
  return r.toLowerCase().replace(/_/g, " ");
}

export const POSITIVE_RELS = new Set(["INCREASES", "IMPROVES", "CAUSES"]);
export const NEGATIVE_RELS = new Set(["DECREASES", "IMPAIRS"]);

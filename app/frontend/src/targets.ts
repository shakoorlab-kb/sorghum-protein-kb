import type { Claim, Source, SourceTarget } from "./types";

export function sourceTarget(s: Source): SourceTarget {
  return {
    doc_id: s.doc_id,
    page: s.page,
    quote: s.quote || s.text,
    title: s.title,
    year: s.year,
    label: s.kind === "claim" ? `Graph claim [${s.n}]` : `Passage [${s.n}]`,
    claim: s.kind === "claim" && s.claim
      ? { ...s.claim, subject: s.subject, object: s.object }
      : undefined,
  };
}

export function claimTarget(c: Claim, fallbackDoc?: { id: string; title: string; year?: number | null }): SourceTarget {
  const d = c.document ?? (fallbackDoc ? { id: fallbackDoc.id, title: fallbackDoc.title, year: fallbackDoc.year } : undefined);
  return {
    doc_id: d?.id ?? "",
    page: c.page,
    quote: c.evidence,
    title: d?.title,
    year: d?.year,
    label: "Graph claim",
    claim: {
      relation: c.relation, statement: c.statement, context: c.context, value: c.value, kind: c.kind,
      verified: c.verified,
      confidence: c.confidence, subject: c.subject, object: c.object,
    },
  };
}

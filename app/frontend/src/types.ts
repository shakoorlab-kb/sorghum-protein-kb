export type EntityType =
  | "GENE" | "PROTEIN" | "TRAIT" | "PROCESS" | "COMPOUND" | "STRUCTURE" | "PHENOTYPE"
  | "GERMPLASM" | "ORGANISM" | "METHOD" | "MARKER" | "LOCATION" | "FOOD" | "DOCUMENT";

export interface EntityRef { id: string; name: string; type: string }

export interface GraphNode {
  id: string;
  name: string;
  type: string;
  kind: "entity" | "document";
  n_docs?: number;
  degree?: number;
  ring?: number;
  focus?: boolean;
  doc_id?: string;
  title?: string;
  year?: number | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  n?: number;
  n_docs?: number;
  verified?: number | boolean | null;
}

export interface GraphView {
  nodes: GraphNode[];
  edges: GraphEdge[];
  center?: string;
  paths?: string[][];
}

export interface DocRef {
  id: string;
  title: string;
  year?: number | null;
  doi?: string;
  file_type?: string;
}

export interface Claim {
  id: string;
  relation: string;
  statement: string;
  evidence: string;
  page: number;
  confidence: number;
  verified: boolean;
  context?: string;
  value?: string;
  kind?: string;
  method?: string;
  chunk_id?: string | null;
  subject: EntityRef;
  object: EntityRef;
  document?: DocRef;
}

export interface Source {
  n: number;
  kind: "passage" | "claim";
  doc_id: string;
  title: string;
  year?: number | null;
  doi?: string | null;
  page: number;
  text: string;
  quote?: string;
  file_type?: string;
  chunk_id?: string | null;
  claim_id?: string;
  cited?: boolean;
  entities?: EntityRef[];
  claim?: { relation: string; statement: string; context?: string; value?: string; kind?: string; confidence: number; verified: boolean };
  subject?: EntityRef;
  object?: EntityRef;
}

export interface Step { step: string; detail: string; ms: number; queries?: string[]; entities?: EntityRef[] }

export interface ChartPoint { x: string | number; y: number; label?: string; series: string; n?: number }

export interface ChartSpec {
  type: "bar" | "grouped_bar" | "scatter" | "line";
  title: string;
  x_label: string;
  y_label: string;
  unit: string;
  source: "table" | "evidence";
  points: ChartPoint[];
  sources: { n: number | null; doc_id: string; title: string; page: number; year?: number | null }[];
  truncated?: boolean;
  n_rows?: number | null;
  dropped?: number;
  note?: string;
}

export interface ChatResult {
  chart?: ChartSpec | null;
  answer: string;
  sources: Source[];
  cited: number[];
  invalid_citations?: number[];
  graph: GraphView;
  entities: EntityRef[];
  standalone?: string;
  queries?: string[];
  steps: Step[];
  model?: string;
  elapsed_ms?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  payload?: Partial<ChatResult>;
  pending?: boolean;
  error?: string;
}

export interface Conversation { id: string; title: string; created_at: number; updated_at: number; n_messages?: number }

export interface DocumentRow {
  id: string;
  title: string;
  year?: number | null;
  doi?: string;
  journal?: string;
  authors?: string[];
  origin: "kb" | "upload";
  doc_type?: string;
  file_type?: string;
  n_pages?: number;
  status?: string;
  extracted?: boolean;
  n_claims?: number;
  n_entities?: number;
}

export interface DocumentDetail extends DocumentRow {
  abstract?: string;
  summary?: string;
  study_type?: string;
  key_findings?: string[];
  keywords?: string[];
  url?: string;
  file_path?: string;
  n_chunks: number;
  n_cites: number;
  n_cited_by: number;
  extracted_at?: string | null;
  updated_at?: string | null;
  extract_model?: string;
  top_entities: { id: string; name: string; type: string; n: number }[];
  supplements?: { id: string; title: string; n_pages?: number }[];
  parent?: { id: string; title: string } | null;
}

export interface PageView {
  doc_id: string;
  page: number;
  n_pages: number;
  file_type?: string;
  text: string;
  rects: number[][];
  width: number | null;
  height: number | null;
  suggested_page?: number;
  error?: string;
}

export interface EntitySearchHit {
  id: string; name: string; type: string; aliases?: string[]; n_docs: number; degree: number; score: number;
}

export interface EntityDetail {
  id: string;
  name: string;
  type: string;
  aliases?: string[];
  description?: string;
  mentions?: number;
  n_docs?: number;
  degree?: number;
  documents: { id: string; title: string; year?: number | null; n: number }[];
  claims: Claim[];
}

export interface Job {
  id: string;
  kind: string;
  label: string;
  status: "queued" | "running" | "done" | "failed";
  progress: number;
  message: string;
  detail?: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

export interface Stats {
  documents: number;
  chunks: number;
  entities: number;
  claims: number;
  relations: number;
  authors: number;
  citations: number;
  verified_claims: number;
  extracted_documents: number;
  pages: number;
  entity_types: { type: string; n: number }[];
  relation_types: { type: string; n: number }[];
  origins: { origin: string; doc_type: string; n: number }[];
  years: { year: number; n: number }[];
  top_entities: { id: string; name: string; type: string; degree: number; n_docs: number; mentions: number }[];
  usage: { calls: number; cost: number; prompt_tokens: number; completion_tokens: number;
           by_purpose: { purpose: string; model: string; calls: number; cost: number }[] };
  models: { chat: string; extract: string; embed: string };
  jobs: Job[];
}

export interface Health { ok: boolean; neo4j: boolean; llm: boolean; chat_model: string; extract_model: string }

/** What the source viewer is asked to show. */
export interface SourceTarget {
  doc_id: string;
  page: number;
  quote?: string;
  title?: string;
  year?: number | null;
  claim?: {
    relation: string; statement?: string; context?: string; value?: string; kind?: string;
    verified?: boolean; confidence?: number;
    subject?: EntityRef; object?: EntityRef;
  };
  label?: string;
}

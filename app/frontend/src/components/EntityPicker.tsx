import { useEffect, useRef, useState } from "react";

import { api, qs } from "../api";
import { typeLabel } from "../palette";
import type { EntitySearchHit } from "../types";
import { TypeDot } from "./Chips";
import { Icon } from "./Icon";

/** Search-as-you-type over graph entities (names and aliases). */
export function EntityPicker({
  placeholder = "Search entities", onPick, value, autoFocus, compact,
}: { placeholder?: string; onPick: (e: EntitySearchHit) => void; value?: string; autoFocus?: boolean; compact?: boolean }) {
  const [q, setQ] = useState(value ?? "");
  const [hits, setHits] = useState<EntitySearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const seq = useRef(0);

  useEffect(() => setQ(value ?? ""), [value]);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) {
      setHits([]);
      return;
    }
    const my = ++seq.current;
    const t = setTimeout(() => {
      api
        .get<{ items: EntitySearchHit[] }>(`api/graph/search${qs({ q: term, limit: 12 })}`)
        .then((r) => {
          if (my === seq.current) {
            setHits(r.items);
            setActive(0);
            setErr(null);
          }
        })
        .catch((e) => my === seq.current && setErr((e as Error).message));
    }, 180);
    return () => clearTimeout(t);
  }, [q]);

  const pick = (h: EntitySearchHit) => {
    setQ(h.name);
    setOpen(false);
    onPick(h);
  };

  return (
    <div className={`picker ${compact ? "picker-compact" : ""}`}>
      <div className="search-field">
        <Icon name="search" size={14} />
        <input
          value={q}
          autoFocus={autoFocus}
          placeholder={placeholder}
          aria-label={placeholder}
          role="combobox"
          aria-expanded={open && hits.length > 0}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            else if (e.key === "Enter" && hits[active]) { e.preventDefault(); pick(hits[active]); }
            else if (e.key === "Escape") setOpen(false);
          }}
        />
      </div>
      {open && (hits.length > 0 || err) ? (
        <ul className="picker-menu" role="listbox">
          {err ? <li className="picker-err">{err}</li> : null}
          {hits.map((h, i) => (
            <li key={h.id} role="option" aria-selected={i === active}>
              <button type="button" className={i === active ? "picker-item active" : "picker-item"} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(h)}>
                <TypeDot type={h.type} />
                <span className="picker-name">{h.name}</span>
                <span className="picker-type">{typeLabel(h.type)}</span>
                <span className="picker-docs">{h.n_docs} docs</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

import DOMPurify from "dompurify";
import { marked } from "marked";
import { useMemo } from "react";

marked.setOptions({ gfm: true, breaks: false });

/** Markdown with [n] citations turned into buttons. Clicks are delegated to onCite. */
export function Markdown({
  text, onCite, valid, citedTitles,
}: { text: string; onCite?: (n: number) => void; valid?: Set<number>; citedTitles?: Record<number, string> }) {
  const html = useMemo(() => {
    const raw = marked.parse(text || "", { async: false }) as string;
    // Replace [n] outside of code blocks. Group adjacent citations into one cluster.
    const parts = raw.split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>)/g);
    const withCites = parts
      .map((p, i) => {
        if (i % 2 === 1) return p;
        return p.replace(/\[(\d{1,3})\]/g, (m, n) => {
          const num = Number(n);
          if (valid && !valid.has(num)) return m;
          const t = citedTitles?.[num] ? ` title="${citedTitles[num].replace(/"/g, "&quot;")}"` : "";
          return `<button type="button" class="cite" data-cite="${num}"${t}>${num}</button>`;
        });
      })
      .join("");
    return DOMPurify.sanitize(withCites, { ADD_ATTR: ["data-cite", "type"], ADD_TAGS: ["button"] });
  }, [text, valid, citedTitles]);

  return (
    <div
      className="md"
      onClick={(e) => {
        const el = (e.target as HTMLElement).closest("[data-cite]") as HTMLElement | null;
        if (el && onCite) onCite(Number(el.dataset.cite));
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

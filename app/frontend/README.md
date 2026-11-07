# Sorghum Protein Intelligence console

React 19 + TypeScript + Vite. Builds into `../backend/sorghum_intel/static`, which the
FastAPI app serves at its root (and behind a reverse-proxy prefix such as `/sorghum-intel/`:
every asset and API URL is relative, routing is by hash).

```bash
npm install
npm run dev     # http://localhost:5173, proxies /api to the backend on 127.0.0.1:8770
npm run build   # tsc + vite build into the backend package
```

Views: Chat (`#/chat`), Graph explorer (`#/graph/<entity id>`), Library (`#/library`,
`#/library/<doc id>`), Ingest (`#/ingest`), Overview (`#/overview`). Clicking any citation
or claim opens the source viewer: the original page rendered by the backend with the
quoted text outlined.

# MediGraph frontend

React + TypeScript (Vite). Chat on the left, and for each answer a panel with the pipeline steps,
the generated Cypher, the retrieved rows and the subgraph (vis-network).

It talks to the FastAPI backend through the Vite proxy (`/api` -> `localhost:8000`), and the
answer streams in over server-sent events (`src/api.ts`).

```bash
npm install
npm run dev     # http://localhost:5173, needs the API running (make api)
npm run build
```

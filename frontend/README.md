# Frontend source

The app serves `app/static/main.js` directly so you can still run with only Python.

`src/main.ts` is the TypeScript source for the served bundle. Build it with Vite:

```bash
cd frontend
npm install
npm run typecheck
npm run build
```

The build writes `../app/static/main.js` and keeps the checked-in static files available for Docker/Python-only runs.

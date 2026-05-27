# Frontend source

The runnable prototype serves `/app/static/main.js` directly so you can start with only Python.

`src/main.ts` is the TypeScript source mirror for editing. Optional type-checking:

```bash
cd frontend
npm install
npm run typecheck
```

The running app currently uses CDN Three.js and the checked-in `app/static/main.js`, so no Node build is required to experiment.

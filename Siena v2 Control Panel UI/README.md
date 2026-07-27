# Siena Desktop

Electron + React shell for the local Siena backend.

```powershell
npm install
npm run typecheck
npm test
npm run build
npm run desktop
```

The backend must be available at `http://127.0.0.1:8000`. The renderer has no
Node integration and uses only the documented local HTTP API.

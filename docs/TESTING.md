# Testing

```powershell
.\scripts\test.ps1
cd "Siena v2 Control Panel UI"
npm run typecheck
npm test
npm run build
```

Unit tests redirect stores to temporary paths and block real network and
subprocess calls.

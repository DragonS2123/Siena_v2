# Models

`GET /api/models` reads `/api/tags` and `/api/ps` from Ollama on demand.
`POST /api/models/refresh` forces another read. Assign any installed model with
`PUT /api/models/roles/{role}`.

Roles: chat, deep, coder, reviewer, memory, OCR, vision and embedding. Missing
persisted assignments remain visible and become usable again after reinstall.
New assignments must name a currently installed model.

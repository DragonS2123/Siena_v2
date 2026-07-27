# Architecture

`api/app.py` is the application factory. Its lifespan calls
`core/runtime.py`, the only composition root. Routers perform HTTP validation;
services own business rules; stores own persistence. Importing the API creates
no stores, probes or subprocesses.

Flow: Desktop → router → service → store or local provider. Chat selection is
one-turn override → conversation override → `chat` role. `deep` is manual only.

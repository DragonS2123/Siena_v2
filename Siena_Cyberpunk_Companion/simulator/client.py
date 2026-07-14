import httpx


class TelemetryClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=5.0)

    async def send(self, state: dict) -> dict:
        response = await self._client.post("/api/v1/telemetry/state", json=state)
        response.raise_for_status()
        return response.json()

    async def bridge_status(self) -> dict:
        response = await self._client.get("/api/v1/bridge/status")
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()

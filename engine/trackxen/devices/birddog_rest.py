"""BirdDog RESTful API 2.0 (HTTP, port 8080), used for camera setup only.

It has no pan/tilt/zoom move commands; movement goes through VISCA over IP.
Endpoints: https://birddog.tv/AV/API/index.html
"""

from __future__ import annotations

import httpx

REST_PORT = 8080


class BirdDogRest:
    def __init__(self, host: str, port: int = REST_PORT, timeout: float = 2.0):
        self.base = f"http://{host}:{port}"
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BirdDogRest:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _get(self, path: str) -> dict | str:
        r = await self._client.get(self.base + path)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return r.text.strip()

    async def _post(self, path: str, body: dict) -> dict | str:
        r = await self._client.post(self.base + path, json=body)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return r.text.strip()

    async def about(self) -> dict | str:
        """Firmware, hostname, IP, serial and status."""
        return await self._get("/about")

    async def version(self) -> dict | str:
        return await self._get("/version")

    async def ptz_setup(self) -> dict | str:
        """Max speeds for NDI PTZ control: PanSpeed 0-21, TiltSpeed 0-18, ZoomSpeed 0-7."""
        return await self._get("/birddogptzsetup")

    async def encode_setup(self) -> dict | str:
        return await self._get("/encodesetup")

    async def recall_preset(self, number: int) -> dict | str:
        """Presets 1-9 only through REST (VISCA reaches all 128)."""
        if not 1 <= number <= 9:
            raise ValueError("REST presets are 1-9")
        return await self._post("/recall", {"Preset": f"Preset-{number}"})

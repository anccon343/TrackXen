"""Web API: REST for commands, a WebSocket for live status (10 Hz) and preview images.

Preview messages are binary: the camera id, a newline, then the JPEG. With the optional PIN on
(core/access.py), every /api route except signing in, and the WebSocket, need a signed-in
device, the PIN in an X-TrackXen-PIN header, or a request from this PC (opened as localhost).

Only requests addressed to this PC are answered (its IP address, localhost or its own name), so a
web page elsewhere whose name was pointed at this PC (DNS rebinding) is refused.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from pathlib import Path

from urllib.parse import urlparse, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.websockets import WebSocketState

from trackxen.core import logview
from trackxen.core.access import COOKIE, HEADER, LOCAL, AccessGuard
from trackxen.core.app import App
from trackxen.core.config import AppConfig, CameraConfig
from trackxen.core.performance import PRESETS

STATIC = Path(__file__).resolve().parent / "static"
OPEN_ROUTES = {"/api/access", "/api/access/login", "/api/access/logout"}  # usable before signing in
COOKIE_AGE = 365 * 24 * 3600
SAME_SITE = {"same-origin", "none"}  # Sec-Fetch-Site of the page's own requests, and of typed-in addresses
SIGNED_OUT = 4000  # live connection closed after a sign-out or PIN change: the page connects again
STALE_SETTINGS = "The settings were changed on another device. Reload them and try again."


def same_origin(headers) -> bool:
    """Browsers send Origin; it must be this server. Tools without a browser send none."""
    origin = headers.get("origin")
    return not origin or urlparse(origin).netloc == headers.get("host")


def check_origin(request: Request) -> None:
    if not same_origin(request.headers):
        raise HTTPException(status_code=403, detail="cross-site request refused")


def own_names() -> set[str]:
    """This PC's own names, which may be used instead of its IP address."""
    name = socket.gethostname()
    short = name.split(".")[0]
    names = {"localhost", name, short, f"{short}.local", socket.getfqdn()}
    return {n.lower().rstrip(".") for n in names if n}


def _host_name(host: str | None) -> str:
    """The name or address in a Host header, without the port ("" if there is none)."""
    if not host or any(c in host for c in "@/\\?#"):
        return ""
    try:
        return (urlsplit(f"//{host}").hostname or "").rstrip(".")
    except ValueError:
        return ""


def own_host(host: str | None, names: set[str]) -> bool:
    """Was the request sent to this PC: an IP address, localhost or this PC's name? A page from
    elsewhere sends its own name even when that name was pointed at this PC."""
    name = _host_name(host)
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return name in names


def _loopback_host(host: str | None) -> bool:
    name = _host_name(host)
    try:
        return name == "localhost" or ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _address(conn: Request | WebSocket) -> str:
    """The device's address. This PC counts as itself (no PIN) only when it opened TrackXen as
    localhost or 127.0.0.1: under another name the page could be another web site's."""
    address = conn.client.host if conn.client else ""
    if address in LOCAL and not _loopback_host(conn.headers.get("host")):
        return f"{address} (by name)"
    return address


def _with_cookie(body: dict, token: str | None) -> JSONResponse:
    response = JSONResponse(body)
    if token:
        response.set_cookie(COOKIE, token, max_age=COOKIE_AGE, httponly=True, samesite="strict", path="/")
    return response


def create_server(app: App, guard: AccessGuard | None = None) -> FastAPI:
    api = FastAPI(title="TrackXen")
    ui = STATIC / "app"  # the built web UI (ui/ -> npm run build)
    if (ui / "assets").exists():
        api.mount("/assets", StaticFiles(directory=ui / "assets"), name="assets")
    guarded = [Depends(check_origin)]
    guard = guard or AccessGuard(app.data / "access.json",
                                 log=lambda level, message: app.log.event("access", message, level=level))
    names = own_names()
    config_lock = asyncio.Lock()  # one settings change at a time (its revision is checked first)
    live_errors: dict[str, float] = {}  # live connection error -> when last logged

    @api.middleware("http")
    async def require_pin(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in OPEN_ROUTES:
            address, cookie, pin = _address(request), request.cookies.get(COOKIE), request.headers.get(HEADER)
            if not guard.allowed(address, cookie) and (
                    pin is None or not await asyncio.to_thread(guard.allowed, address, cookie, pin)):
                return JSONResponse({"detail": "PIN required"}, status_code=401)
        return await call_next(request)

    @api.middleware("http")
    async def this_pc_only(request: Request, call_next):
        """(Runs before require_pin.) Another site's page can't use the API, even with a renamed
        address or without an Origin (an <img> pointing at /api/discover)."""
        if not own_host(request.headers.get("host"), names):
            return PlainTextResponse("Open TrackXen by this PC's IP address or name.", status_code=403)
        if request.url.path.startswith("/api/") and request.headers.get("sec-fetch-site", "none") not in SAME_SITE:
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        return await call_next(request)

    @api.get("/api/access")
    async def access_state(request: Request):
        return guard.status(_address(request), request.cookies.get(COOKIE))

    @api.post("/api/access/login", dependencies=guarded)
    async def sign_in(request: Request, body: dict):
        address = _address(request)
        token = None if guard.wait_left(address) else await asyncio.to_thread(guard.sign_in, address, body.get("pin"))
        if not token:
            wait = guard.wait_left(address)
            raise HTTPException(status_code=429 if wait else 401,
                                detail=f"Too many wrong PINs: wait {wait:.0f} s" if wait else "Wrong PIN")
        return _with_cookie({"ok": True}, token)

    @api.post("/api/access/logout", dependencies=guarded)
    async def sign_out(request: Request):
        guard.sign_out(request.cookies.get(COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @api.post("/api/access/pin", dependencies=guarded)
    async def set_pin(request: Request, body: dict):
        """{"pin": "1234"} switches the PIN on or changes it, {"pin": null} switches it off. From another
        device the current PIN is needed too, so a borrowed signed-in tablet can't change it."""
        address = _address(request)
        if guard.enabled and address not in LOCAL and not await asyncio.to_thread(
                guard.check_pin, address, body.get("current")):
            raise HTTPException(status_code=403, detail="The current PIN is wrong")
        try:  # (hashing the new PIN takes ~0.1 s: not on the event loop, which drives the cameras)
            token = await asyncio.to_thread(guard.set_pin, body.get("pin"), bool(body.get("ask_local", False)))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        return _with_cookie({"ok": True, **guard.status(address, token)}, token)

    @api.post("/api/access/ask-local", dependencies=guarded)
    async def set_ask_local(request: Request, body: dict):
        guard.set_ask_local(bool(body.get("on")))
        return {"ok": True, **guard.status(_address(request), request.cookies.get(COOKIE))}

    @api.post("/api/access/forget-devices", dependencies=guarded)
    async def forget_devices(request: Request):
        return {"ok": True, "signed_out": guard.forget_devices(keep=request.cookies.get(COOKIE))}

    @api.get("/")
    async def index():
        return FileResponse(ui / "index.html" if (ui / "index.html").exists() else STATIC / "test.html")

    @api.get("/test")
    async def test_page():
        return FileResponse(STATIC / "test.html")

    @api.get("/api/status")
    async def status():
        return app.status()

    @api.post("/api/command", dependencies=guarded)
    async def command(cmd: dict):
        return await app.command(cmd)

    @api.get("/api/config")
    async def get_config():
        """The settings, with their revision (a settings page sends it back with its changes)."""
        return {**app.config.model_dump(), "_rev": getattr(app, "config_rev", 0)}

    @api.post("/api/config", dependencies=guarded)
    async def set_config(body: dict):
        rev = body.pop("_rev", None)  # (tools may leave it out)
        try:
            cfg = AppConfig.model_validate(body)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors(include_url=False)) from e
        async with config_lock:
            if rev is not None and rev != getattr(app, "config_rev", 0):
                raise HTTPException(status_code=409, detail=STALE_SETTINGS)
            await app.apply_config(cfg)
        return {"ok": True}

    @api.get("/api/system")
    async def system():
        """This PC: CPU, memory, GPUs and the engines ONNX Runtime can use (for the first-run check)."""
        from trackxen.core.hwtest import system_info

        return await asyncio.to_thread(system_info)

    @api.get("/api/camera-models")
    async def camera_models():
        """The camera types and the settings each starts with (core/camera_models.py)."""
        from trackxen.core.camera_models import MODELS, camera_defaults

        return {key: {"name": m["name"], "about": m["about"], "estimate": m["estimate"],
                      "defaults": camera_defaults(key)} for key, m in MODELS.items()}

    @api.post("/api/camera-test", dependencies=guarded)
    async def camera_test(body: dict):
        try:
            cam = CameraConfig.model_validate(body)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors(include_url=False)) from e
        return await app.test_camera(cam)

    @api.get("/api/capture-devices")
    async def capture_devices():
        """HDMI/SDI capture devices on this PC (opened briefly to see that they work)."""
        from trackxen.vision.sources import capture_devices as probe

        return await asyncio.to_thread(probe)

    @api.get("/api/logs")
    async def log_days():
        return logview.days(app.log.folder)

    @api.get("/api/logs/{day}/events")
    async def log_events(day: str):
        try:
            return await asyncio.to_thread(logview.events, app.log.folder, day)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @api.get("/api/logs/{day}/stats")
    async def log_stats(day: str, points: int = 2000):
        try:
            return await asyncio.to_thread(logview.stats, app.log.folder, day, max(10, min(points, 20000)))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @api.get("/api/logs/{day}/file/{kind}")
    async def log_download(day: str, kind: str):
        try:
            path = logview.file_of(app.log.folder, day, kind)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=404, detail="no such log") from e
        if not path.exists():
            raise HTTPException(status_code=404, detail="no such log")
        return FileResponse(path, filename=path.name)

    @api.get("/api/faces")
    async def faces():
        return {"faces": app.faces.store.listing(),
                "cameras": {cam: [r.model_dump() for r in s.cfg.faces] for cam, s in app.sessions.items()}}

    @api.get("/api/faces/{face_id}.jpg")
    async def face_thumbnail(face_id: str):
        jpeg = app.faces.store.thumbnail(face_id)
        if jpeg is None:
            raise HTTPException(status_code=404, detail="no such face")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @api.post("/api/faces/wipe", dependencies=guarded)
    async def wipe_faces():
        app.faces.wipe()
        return {"ok": True}

    @api.get("/api/slots/{camera}")
    async def slots(camera: str):
        if camera not in app.sessions:
            raise HTTPException(status_code=404, detail="unknown camera")
        return app.slots(camera)

    @api.get("/api/show")
    async def save_show():
        """The whole setup as one file (registered faces themselves stay on this PC)."""
        stamp = time.strftime("%Y%m%d-%H%M")
        return Response(app.config.model_dump_json(indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="trackxen-show-{stamp}.json"'})

    @api.post("/api/show", dependencies=guarded)
    async def load_show(body: dict):
        try:
            cfg = AppConfig.model_validate(body)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors(include_url=False)) from e
        here = app.config  # what belongs to this PC: its web address and what its hardware test found
        cfg.web_host, cfg.web_port = here.web_host, here.web_port
        cfg.performance_mode, cfg.auto_preset = here.performance_mode, here.auto_preset
        cfg.model_override, cfg.engines = here.model_override, list(here.engines)
        app.faces.unmap_mics(cfg.audio)  # faces stay on their PC: mics can't name faces this PC lacks
        async with config_lock:
            await app.apply_config(cfg, whole=True)
        return {"ok": True}

    @api.get("/api/discover")
    async def discover():
        return await app.discover()

    @api.get("/api/presets")
    async def presets():
        return {name: p.__dict__ for name, p in PRESETS.items()}

    @api.post("/api/hwtest", dependencies=guarded)
    async def start_hwtest(body: dict):
        started = app.run_hardware_test(int(body.get("cameras", 4)), float(body.get("fps", 30)),
                                        bool(body.get("shared_with_vmix", False)), bool(body.get("quick", False)))
        return {"ok": started}

    @api.get("/api/hwtest")
    async def hwtest_state():
        return app.hwtest

    @api.post("/api/statistics/reset", dependencies=guarded)
    async def reset_statistics():
        app.reset_statistics()
        return {"ok": True}

    @api.get("/api/log", response_class=PlainTextResponse)
    async def log_tail(lines: int = 200):
        path = app.log.text_path
        if not path.exists():
            return ""
        return await asyncio.to_thread(tail_lines, path, lines)  # (the Statistics page asks every 3 s)

    @api.websocket("/ws")
    async def ws(socket: WebSocket):
        if not same_origin(socket.headers) or not own_host(socket.headers.get("host"), names):
            await socket.close(code=1008)  # another web site's page must not drive the cameras
            return
        await socket.accept()
        address, cookie = _address(socket), socket.cookies.get(COOKIE)
        if not guard.allowed(address, cookie):
            await socket.close(code=4401)  # the page then shows the PIN screen
            return
        new_preview = asyncio.Event()
        notify = new_preview.set
        app.listeners.add(notify)
        send_lock = asyncio.Lock()
        sent: dict[str, int] = {}

        def connected() -> bool:
            return socket.application_state == WebSocketState.CONNECTED == socket.client_state

        async def close(code: int) -> None:
            async with send_lock:
                if connected():
                    await socket.close(code=code)

        async def sender():
            next_status = 0.0
            try:
                while connected():
                    try:
                        await asyncio.wait_for(new_preview.wait(), timeout=0.1)
                    except TimeoutError:
                        pass
                    new_preview.clear()
                    if not guard.allowed(address, cookie):  # signed out, or the PIN changed, since it connected
                        await close(SIGNED_OUT)  # (when it connects again it gets 4401 if it must sign in)
                        return
                    async with send_lock:
                        if not connected():
                            return
                        for cam, (seq, jpeg) in list(app.previews.items()):
                            if sent.get(cam) != seq:
                                sent[cam] = seq
                                await socket.send_bytes(cam.encode() + b"\n" + jpeg)
                        if time.monotonic() >= next_status:
                            next_status = time.monotonic() + 0.1
                            await socket.send_json({"type": "status", **app.status()})
            except WebSocketDisconnect:
                pass  # the page went away
            except Exception as e:  # noqa: BLE001 - rather than frozen states on the page: log it, and it connects again
                if not connected():
                    return
                message = f"live connection closed after an error: {e!r}"
                if time.monotonic() - live_errors.get(message, -60.0) >= 60.0:  # (once a minute, not every retry)
                    live_errors[message] = time.monotonic()
                    app.log.event("app", message, level="error")
                await close(1011)

        task = asyncio.create_task(sender())
        try:
            while connected():
                cmd = await socket.receive_json()
                if not guard.allowed(address, cookie):  # signed out since it connected: no more commands
                    await close(SIGNED_OUT)
                    break
                result = await app.command(cmd)
                async with send_lock:
                    if connected():
                        await socket.send_json({"type": "reply", "request": cmd.get("id"), **result})
        except WebSocketDisconnect:
            pass
        finally:
            task.cancel()
            app.listeners.discard(notify)

    return api


def tail_lines(path: Path, lines: int) -> str:
    """The last `lines` lines of a text file, reading only as much of its end as they need (the day's
    log grows to megabytes)."""
    lines = max(1, lines)
    with path.open("rb") as f:
        size = f.seek(0, 2)
        chunk = 64 * 1024
        while True:
            start = max(0, size - chunk)
            f.seek(start)
            data = f.read(size - start)
            if start == 0 or data.count(b"\n") > lines:
                break
            chunk *= 4
    text = data.decode("utf-8", errors="replace").splitlines()
    return "\n".join((text[1:] if start else text)[-lines:])  # (the first line read may be cut)

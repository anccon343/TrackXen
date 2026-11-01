"""Registered faces, kept only on this PC: data/faces/faces.json (names and face fingerprints) and
one small thumbnail per face. Shared by all cameras; each camera's settings list which of them it
uses, in priority order (config.FaceRef). Canon: get people's consent before registering them,
and delete the data when it is no longer needed (wipe)."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from pydantic import BaseModel

from trackxen.core.config import FaceRef


class Face(BaseModel):
    id: str
    name: str = ""
    feature: list[float]
    created: float = 0.0


class FaceStore:
    def __init__(self, folder: Path):
        self.folder = folder
        self.faces: dict[str, Face] = {}
        path = folder / "faces.json"
        if path.exists():
            for item in json.loads(path.read_text(encoding="utf-8")):
                face = Face.model_validate(item)
                self.faces[face.id] = face

    def add(self, name: str, feature: list[float], jpeg: bytes) -> Face:
        face = Face(id=uuid.uuid4().hex[:12], name=name[:40], feature=feature, created=time.time())
        self.faces[face.id] = face
        self.folder.mkdir(parents=True, exist_ok=True)
        if jpeg:
            (self.folder / f"{face.id}.jpg").write_bytes(jpeg)
        self._save()
        return face

    def rename(self, face_id: str, name: str) -> None:
        self.faces[face_id].name = name[:40]
        self._save()

    def delete(self, face_id: str) -> None:
        self.faces.pop(face_id, None)
        (self.folder / f"{face_id}.jpg").unlink(missing_ok=True)
        self._save()

    def wipe(self) -> None:
        """Delete every face (names, fingerprints and thumbnails)."""
        self.faces.clear()
        if self.folder.exists():
            shutil.rmtree(self.folder)

    def thumbnail(self, face_id: str) -> bytes | None:
        path = self.folder / f"{face_id}.jpg"
        return path.read_bytes() if face_id in self.faces and path.exists() else None

    def gallery(self) -> list[dict]:
        """What the face thread compares against."""
        return [{"id": f.id, "feature": f.feature} for f in self.faces.values()]

    def listing(self) -> list[dict]:
        return [{"id": f.id, "name": f.name, "created": f.created} for f in self.faces.values()]

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / "faces.tmp"
        tmp.write_text(json.dumps([f.model_dump() for f in self.faces.values()]), encoding="utf-8")
        tmp.replace(self.folder / "faces.json")


class FaceManager:
    """Registered faces for the whole app: the store, each camera's list of them (priority order,
    on/off) and registration through the vision worker's face thread."""

    def __init__(self, app):
        self.app = app
        self.store = FaceStore(app.data / "faces")
        self.names: dict[str, str] = {f.id: f.name for f in self.store.faces.values()}  # shared with the sessions
        self.guides: dict[str, dict] = {}  # camera -> newest registration frame check
        self.captures: dict[int, tuple[str, str]] = {}  # capture number -> camera, name

    def _to_worker(self, msg: dict) -> None:
        if self.app.worker:
            self.app.worker[1].put(msg)

    def worker_started(self) -> None:
        if self.store.faces:
            self._to_worker({"type": "faces", "gallery": self.store.gallery()})

    def on_message(self, msg: dict) -> bool:
        """Face messages from the vision worker; False for anything else."""
        if msg.get("type") == "face_guide":
            self.guides[msg["camera"]] = {**msg, "at": time.monotonic()}
        elif msg.get("type") == "face_captured":
            self._captured(msg)
        else:
            return False
        return True

    def camera_status(self, camera: str) -> dict:
        guide = self.guides.get(camera)
        return {"face_guide": ({k: guide[k] for k in ("ok", "problem", "box")}
                               if guide and time.monotonic() - guide["at"] < 1.0 else None),
                "face_capturing": any(cam == camera for cam, _ in self.captures.values())}

    def _captured(self, msg: dict) -> None:
        camera, name = self.captures.pop(msg["capture"], (msg["camera"], ""))
        session = self.app.sessions.get(camera)
        face = self.store.add(name or f"Face {len(self.store.faces) + 1}", msg["feature"], msg["jpeg"])
        self.names[face.id] = face.name
        if session and len(session.cfg.faces) < 7:
            session.cfg.faces.append(FaceRef(id=face.id))
            session.settings_rev += 1
        self._changed()
        self.app.log.event("faces", f"face registered: {face.name}", source=camera)

    def _changed(self) -> None:
        """Face list edits are kept at once (like Canon's registration), and the face thread is told."""
        if self.app.config_path:
            self.app.config.save(self.app.config_path)
        self._to_worker({"type": "faces", "gallery": self.store.gallery()})

    def command(self, session, kind: str, cmd: dict) -> dict:
        refs = session.cfg.faces
        if kind == "face_guide":  # the registration frame on this camera's picture, on or off
            self._to_worker({"type": "face_guide", "camera": session.cfg.id, "on": bool(cmd["on"])})
            self.guides.pop(session.cfg.id, None)
            return {"ok": True}
        if kind == "face_capture":
            if len(refs) >= 7:
                return {"ok": False, "error": "all 7 face slots of this camera are in use"}
            number = max(self.captures, default=0) + 1
            self.captures[number] = (session.cfg.id, str(cmd.get("name", ""))[:40])
            self._to_worker({"type": "face_capture", "camera": session.cfg.id, "capture": number})
            return {"ok": True, "capture": number}
        face_id = str(cmd.get("id", ""))
        ref = next((r for r in refs if r.id == face_id), None)
        if kind == "face_add":  # a face registered on another camera
            if face_id not in self.store.faces or ref or len(refs) >= 7:
                return {"ok": False, "error": "can't add that face here"}
            refs.append(FaceRef(id=face_id))
        elif kind == "face_update":
            if ref is None:
                return {"ok": False, "error": "unknown face"}
            if "enabled" in cmd:
                ref.enabled = bool(cmd["enabled"])
            if "name" in cmd:
                self.store.rename(face_id, str(cmd["name"]))
                self.names[face_id] = self.store.faces[face_id].name
        elif kind == "face_order":  # ids from highest to lowest priority
            order = [str(i) for i in cmd["ids"]]
            refs.sort(key=lambda r: order.index(r.id) if r.id in order else len(order))
        elif kind == "face_delete":  # from this camera; the face itself once no camera uses it
            session.cfg.faces = [r for r in refs if r.id != face_id]
            if not any(face_id in [r.id for r in s.cfg.faces] for s in self.app.sessions.values()):
                self.store.delete(face_id)
                self.names.pop(face_id, None)
                self.app.log.event("faces", "face deleted", source=session.cfg.id)
        else:
            return {"ok": False, "error": f"unknown command {kind}"}
        session.settings_rev += 1
        self._changed()
        return {"ok": True}

    def wipe(self) -> None:
        """Delete every registered face from this PC (Canon: when the system is given away)."""
        self.store.wipe()
        self.names.clear()
        for s in self.app.sessions.values():
            s.cfg.faces = []
            s.settings_rev += 1
        self._changed()
        self.app.log.event("faces", "all face data deleted", level="warning")

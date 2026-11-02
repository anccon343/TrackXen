"""NVIDIA's CUDA, cuDNN and TensorRT libraries, downloaded on the first start.

The installed program doesn't carry them (they were 4.0 of its 4.8 GB). On the first start
TrackXen fetches the DLLs this PC needs straight out of NVIDIA's official Python wheels (CUDA and
cuDNN from PyPI, TensorRT from pypi.nvidia.com). Only the needed files are read out of each wheel,
with HTTP range requests; TensorRT's builder files for other GPU generations are left out. Every
file is checked against the SHA-256 recorded when TrackXen was built (assets/gpu-libs.json,
written by tools/gpu_libs_manifest.py). Files that arrived survive an interruption.

Until the libraries are here, detection runs on the processor; then the vision worker restarts
on the GPU (and TensorRT prepares its engines as before).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import threading
import zlib
from collections.abc import Callable
from pathlib import Path

MANIFEST = Path(__file__).with_name("assets") / "gpu-libs.json"
DEFLATED = 8  # zip compression method (0 = stored as it is)
RETRY_AFTER = (2.0, 5.0, 15.0)  # seconds between tries of one file
RETRY_ALL_S = 60.0  # after a failed download (no internet...)
CHUNK = 1 << 20  # bytes taken from the network at a time
OLDEST_GPU = 75  # CUDA 13, and the cuDNN and TensorRT made for it, run on Turing (sm75) and newer
USER_AGENT = "TrackXen/0.1.0 (downloads NVIDIA's runtime libraries once)"


def default_root() -> Path:
    """%LOCALAPPDATA%\\TrackXen\\gpu-libs: machine-specific and big, so not in the roaming profile."""
    if os.environ.get("TRACKXEN_GPU_LIBS"):
        return Path(os.environ["TRACKXEN_GPU_LIBS"])
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TrackXen" / "gpu-libs"


def gpu_generations() -> set[str]:
    """The NVIDIA GPUs' generations as TensorRT names them (an RTX 4080 is "sm89"); empty: no NVIDIA GPU."""
    try:
        import pynvml

        pynvml.nvmlInit()
        found = set()
        for i in range(pynvml.nvmlDeviceGetCount()):
            major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(pynvml.nvmlDeviceGetHandleByIndex(i))
            found.add(f"sm{major}{minor}")
        return found
    except Exception:  # noqa: BLE001 - no NVIDIA driver: nothing to download
        return set()


def wanted(files: list[dict], generations: set[str]) -> list[dict]:
    """The files for these GPUs: all CUDA and cuDNN ones, TensorRT's builder file for each GPU
    generation, and its generic one when TensorRT has no file of its own for a generation.
    GPUs older than CUDA 13 supports (GTX 10xx and before) get nothing: they couldn't use them."""
    generations = {g for g in generations if not g[2:].isdigit() or int(g[2:]) >= OLDEST_GPU}
    if not generations:
        return []
    known = {f["gpu"] for f in files if f["gpu"]}
    pick = generations & known
    if generations - known:
        pick.add("ptx")
    return [f for f in files if not f["gpu"] or f["gpu"] in pick]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


class Partial:
    """How far a file's download got. Kept between tries, so a broken connection carries on where it
    stopped instead of fetching the whole file again (one of them is 372 MB)."""

    def __init__(self, f: dict):
        self.method = f["method"]
        self.restart()

    def restart(self) -> None:
        self.unpack = zlib.decompressobj(-15) if self.method == DEFLATED else None
        self.sha, self.size, self.received = hashlib.sha256(), 0, 0  # (size: unpacked, in the part file)
        self.checked = False  # complete and matching NVIDIA's file, only not moved into place yet


class GpuLibraries:
    """Which NVIDIA DLLs this PC needs, which are here, and the download of the rest."""

    def __init__(self, root: Path | None = None, manifest: Path = MANIFEST, generations: set[str] | None = None,
                 log: Callable[[str, str], None] | None = None):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.id = data["id"]
        self.dir = (root or default_root()) / self.id
        self.generations = gpu_generations() if generations is None else generations
        self.files = wanted(data["files"], self.generations)
        self.log = log or (lambda level, message: None)
        self.state = "checking" if self.files else "not_needed"  # downloading, ready, failed
        self.done = 0  # bytes downloaded (compressed, as they come over the network)
        self.total = 0
        self.current = ""
        self.error = ""
        self._partial: dict[str, Partial] = {}  # files begun and not finished, by path
        self._retry = threading.Event()
        self._stop = threading.Event()

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    # --- what is here --------------------------------------------------------------------

    def _verified(self) -> dict:
        try:
            return json.loads((self.dir / "verified.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _mark_verified(self, verified: dict, f: dict) -> None:
        st = (self.dir / f["path"]).stat()
        verified[f["path"]] = [st.st_size, st.st_mtime_ns, f["sha256"]]
        (self.dir / "verified.json").write_text(json.dumps(verified), encoding="utf-8")

    def missing(self) -> list[dict]:
        """Files not here (or not intact). A file is hashed once; afterwards its size and time say it's unchanged."""
        verified, out = self._verified(), []
        for f in self.files:
            path = self.dir / f["path"]
            if not path.exists():
                out.append(f)
                continue
            st = path.stat()
            if verified.get(f["path"]) == [st.st_size, st.st_mtime_ns, f["sha256"]]:
                continue
            if st.st_size == f["size"] and sha256_of(path) == f["sha256"]:
                self._mark_verified(verified, f)
            else:
                out.append(f)
        return out

    def check(self) -> bool:
        """Quick look at start: are all the files here? (Sets the state to ready.)"""
        if self.files and not self.missing():
            self.state = "ready"
        return self.ready

    # --- the download ----------------------------------------------------------------------

    def _client(self, system_certificates: bool):
        import httpx

        # certifi's certificates first; Windows' own store for networks that inspect HTTPS traffic
        verify = ssl.create_default_context() if system_certificates else True
        return httpx.Client(timeout=httpx.Timeout(30.0, read=60.0), follow_redirects=True, verify=verify,
                            headers={"User-Agent": USER_AGENT})

    def fetch(self, client, f: dict, partial: Partial | None = None) -> None:
        """One DLL: its compressed bytes from the wheel, unpacked and checked, then moved into place.
        With the `partial` of an earlier try, only the bytes still missing are fetched."""
        import httpx

        p = partial or Partial(f)
        target = self.dir / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        if p.received and (not part.exists() or part.stat().st_size != p.size):
            self._restart(p)  # the part file isn't what was written before: from the start
        if not p.checked:
            if p.received < f["compressed"]:
                start, end = f["data_offset"] + p.received, f["data_offset"] + f["compressed"] - 1
                with client.stream("GET", f["url"], headers={"Range": f"bytes={start}-{end}"}) as r:
                    if r.status_code != 206:
                        raise OSError(f"{r.url.host} sent HTTP {r.status_code} instead of a part of the file")
                    try:
                        with open(part, "ab" if p.received else "wb") as out:
                            for chunk in r.iter_bytes(CHUNK):
                                data = p.unpack.decompress(chunk) if p.unpack else chunk
                                out.write(data)
                                p.sha.update(data)
                                p.size += len(data)
                                p.received += len(chunk)
                                self.done += len(chunk)
                    except httpx.HTTPError:
                        raise  # the connection broke between two pieces: the next try carries on from here
                    except Exception:
                        self._restart(p)  # unpacking or writing broke off halfway through a piece
                        raise
            try:
                data = p.unpack.flush() if p.unpack else b""
                with open(part, "ab") as out:
                    out.write(data)
            except Exception:
                self._restart(p)
                raise
            p.sha.update(data)
            p.size += len(data)
            if p.size != f["size"] or p.sha.hexdigest() != f["sha256"]:
                part.unlink(missing_ok=True)
                self._restart(p)
                raise OSError(f"{Path(f['path']).name} didn't match NVIDIA's file (size or checksum)")
            p.checked = True
        os.replace(part, target)  # (if this fails, e.g. a virus scanner has the file open, the next try only does this)

    def _restart(self, p: Partial) -> None:
        self.done -= p.received
        p.restart()

    def download(self) -> None:
        """Fetch every missing file (each tried a few times); raises if one can't be had."""
        import httpx

        todo = self.missing()
        if not todo:
            self.state = "ready"
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        need = sum(f["size"] for f in todo) + 200_000_000
        free = shutil.disk_usage(self.dir).free
        if free < need:
            raise OSError(f"not enough disk space on {self.dir.drive or self.dir}: needs {need / 1e9:.1f} GB, "
                          f"{free / 1e9:.1f} GB free")
        self.state, self.error = "downloading", ""
        self.total = sum(f["compressed"] for f in todo)
        self.done = sum(self._partial[f["path"]].received for f in todo if f["path"] in self._partial)
        self.log("info", f"downloading NVIDIA's libraries: {len(todo)} files, {self.total / 1e6:.0f} MB")
        verified, system_certificates = self._verified(), False
        client = self._client(system_certificates)
        try:
            for f in todo:
                self.current = Path(f["path"]).name
                partial = self._partial.setdefault(f["path"], Partial(f))
                for attempt in range(len(RETRY_AFTER) + 1):
                    try:
                        self.fetch(client, f, partial)
                        break
                    except (httpx.HTTPError, OSError, zlib.error) as e:
                        if "CERTIFICATE_VERIFY_FAILED" in str(e) and not system_certificates:
                            system_certificates = True  # try Windows' certificate store instead
                            client.close()
                            client = self._client(True)
                        if attempt == len(RETRY_AFTER) or self._stop.is_set():
                            raise
                        self._stop.wait(RETRY_AFTER[attempt])
                del self._partial[f["path"]]
                self._mark_verified(verified, f)
        finally:
            client.close()
            self.current = ""
        self.state = "ready"
        self.log("info", f"NVIDIA's libraries are ready ({self.total / 1e6:.0f} MB downloaded)")

    def run(self, on_ready: Callable[[], None]) -> None:
        """Thread: download until it works (again every minute after a failure, or at retry())."""
        while not self._stop.is_set() and not self.ready:
            try:
                self.download()
            except Exception as e:  # noqa: BLE001 - shown in the UI, tried again later
                self.state, self.error = "failed", _plain(e)
                self.log("error", f"NVIDIA libraries: {self.error}")
                self._retry.wait(RETRY_ALL_S)
                self._retry.clear()
        if self.ready and not self._stop.is_set():
            on_ready()

    def start(self, on_ready: Callable[[], None]) -> None:
        threading.Thread(target=self.run, args=(on_ready,), name="gpu-libs", daemon=True).start()

    def retry(self) -> None:
        self._retry.set()

    def stop(self) -> None:
        self._stop.set()
        self._retry.set()

    def status(self) -> dict:
        return {"state": self.state, "done_mb": round(self.done / 1e6), "total_mb": round(self.total / 1e6),
                "file": self.current, "error": self.error, "folder": str(self.dir)}

    def problems(self) -> list[dict]:
        """For the problem lights."""
        if self.state == "downloading":
            pct = 100 * self.done / self.total if self.total else 0
            return [{"level": "warning", "source": "gpu",
                     "message": f"downloading NVIDIA's libraries: {pct:.0f} % of {self.total / 1e6:.0f} MB "
                                "(detection runs on the processor until then)"}]
        if self.state == "failed":
            return [{"level": "error", "source": "gpu", "message": f"NVIDIA libraries missing: {self.error}"}]
        return []


def _plain(e: Exception) -> str:
    """An error as a short sentence for the UI."""
    text = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
    if any(word in text for word in ("getaddrinfo", "ConnectError", "Name or service", "No address", "11001")):
        return "no internet connection: TrackXen needs to download them once"
    return text

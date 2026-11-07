"""Updates from GitHub (Settings → System → Updates).

The installed program asks the repository for its latest release, downloads the release's setup program,
checks it against the release's SHA-256 file, and runs it: the setup program replaces the program and
starts it again (packaging/trackxen.iss, /update=1).

Releases are made by .github/workflows/release.yml: every push to main that changes the program builds the
setup program on GitHub and publishes it as a release tagged build-<n>; that build's number is written into
the program (trackxen/_build.py), so a newer release is one with a higher number.

The repository is public, so its releases are read without a token. A GitHub access token (fine-grained,
read-only "Contents") is optional: for a private copy of the repository, or when GitHub limits the checks of
one network without a token (60 requests an hour). It is kept in update.json in the data folder, like the PIN
in access.json: never in settings.json, a show file, the log or an API answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from trackxen import __version__

REPO = "anccon343/TrackXen"
API = "https://api.github.com"
USER_AGENT = f"TrackXen/{__version__} (checks for updates)"
TAG = re.compile(r"^build-(\d+)$")
TOKEN = re.compile(r"^[A-Za-z0-9_]{20,255}$")  # ghp_..., github_pat_...
SETUP = re.compile(r"^TrackXen-Setup-[0-9][0-9A-Za-z.\-]*\.exe$")
# The setup program's own options: a progress window only, close what still runs, start TrackXen again
SETUP_ARGS = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS",
              "/update=1"]


def current() -> dict:
    """This program's version, and the GitHub build it came from (build 0: built by hand, or not installed)."""
    try:
        from trackxen import _build  # written by the release build

        return {"version": _build.VERSION, "build": int(_build.BUILD), "commit": _build.COMMIT}
    except (ImportError, AttributeError, ValueError):
        return {"version": __version__, "build": 0, "commit": ""}


def installed() -> bool:
    """The installed Windows program (not a development copy): only it can install a setup program."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


class UpdateError(Exception):
    pass


class Updater:
    def __init__(self, folder: Path, log: Callable[[str, str], None] | None = None, repo: str = REPO,
                 api: str | None = None, client_factory: Callable[[], object] | None = None):
        self.folder = folder  # the data folder: update.json, and updates/ for the download
        self.log = log or (lambda level, message: None)
        # TRACKXEN_UPDATE_API: a stand-in for GitHub's API, to try the whole update on a test PC
        self.repo, self.api = repo, (api or os.environ.get("TRACKXEN_UPDATE_API") or API).rstrip("/")
        self._client_factory = client_factory
        self.current = current()
        self.state = "idle"  # idle | checking | downloading | ready | error
        self.latest: dict | None = None  # the latest release, once checked
        self.error = ""
        self.received = 0
        self.file: Path | None = None  # the checked setup program, once downloaded
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- the token ---------------------------------------------------------------------------------

    @property
    def _token_file(self) -> Path:
        return self.folder / "update.json"

    def _token(self) -> str:
        try:
            return str(json.loads(self._token_file.read_text(encoding="utf-8")).get("token", ""))
        except (OSError, ValueError, AttributeError):
            return ""

    def set_token(self, token: str) -> None:
        """Keep the access token ("" forgets it)."""
        token = token.strip()
        if token and not TOKEN.match(token):
            raise UpdateError("That doesn't look like a GitHub access token (letters, digits and _ only).")
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self._token_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"token": token}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)  # (no effect on Windows, where %APPDATA% is the user's own)
        except OSError:
            pass
        tmp.replace(self._token_file)
        self.log("info", "update access token saved" if token else "update access token removed")

    # --- GitHub ------------------------------------------------------------------------------------

    def _client(self):
        if self._client_factory:
            return self._client_factory()
        import httpx

        # httpx drops the Authorization header when a download is redirected to another host
        return httpx.Client(timeout=httpx.Timeout(30.0, read=60.0), follow_redirects=True,
                            headers={"User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"})

    def _headers(self, accept: str = "application/vnd.github+json") -> dict:
        """The request headers: the access token only when one is saved (the repository itself is public)."""
        token = self._token()
        return {"Accept": accept, **({"Authorization": f"Bearer {token}"} if token else {})}

    def _refused(self, status: int) -> str:
        token = bool(self._token())
        if status == 401:
            return "GitHub refused the access token: it is wrong or has expired. Make a new one, or remove it."
        if status == 403:
            return ("GitHub refused: too many checks from this network for now (try again in an hour)"
                    + (", or the access token can't read the repository." if token else "."))
        if status == 404:
            return ("No release found. Releases appear after a push to main has been built on GitHub"
                    + ("; or the access token can't read the repository (it needs read access to its Contents)."
                       if token else "."))
        return f"GitHub answered {status}."

    def check(self) -> dict:
        """Ask for the latest release. Returns the status."""
        headers = self._headers()
        with self._lock:
            if self.state == "downloading":
                raise UpdateError("A download is under way.")
            self.state, self.error = "checking", ""
        try:
            with self._client() as client:
                r = client.get(f"{self.api}/repos/{self.repo}/releases/latest", headers=headers)
                if r.status_code != 200:
                    raise UpdateError(self._refused(r.status_code))
                self.latest = self._parse(r.json())
            self.state = "ready" if self._downloaded() else "idle"
        except UpdateError as e:
            self.state, self.error = "error", str(e)
        except Exception as e:  # noqa: BLE001 - no network, a proxy, a broken answer: shown to the operator
            self.state, self.error = "error", f"Couldn't reach GitHub: {_plain(e)}"
        return self.status()

    @staticmethod
    def _parse(release: dict) -> dict:
        m = TAG.match(str(release.get("tag_name", "")))
        if not m:
            raise UpdateError(f"The latest release ({release.get('tag_name')}) wasn't made by the release build.")
        assets = {a.get("name"): a for a in release.get("assets", []) if isinstance(a, dict)}
        setup = next((a for name, a in assets.items() if name and SETUP.match(name)), None)
        if not setup or f"{setup['name']}.sha256" not in assets:
            raise UpdateError("The latest release has no setup program with its .sha256 file.")
        sha = assets[f"{setup['name']}.sha256"]
        return {"tag": release["tag_name"], "build": int(m.group(1)), "name": release.get("name") or release["tag_name"],
                "published": release.get("published_at", ""), "notes": str(release.get("body") or "")[:2000],
                "file": setup["name"], "size": int(setup.get("size", 0)), "url": setup["url"], "sha_url": sha["url"]}

    def newer(self) -> bool:
        return bool(self.latest and self.latest["build"] > self.current["build"])

    # --- the download ------------------------------------------------------------------------------

    def _target(self) -> Path | None:
        return self.folder / "updates" / self.latest["file"] if self.latest else None

    def _downloaded(self) -> bool:
        target = self._target()
        ok = bool(target and self.file == target and target.exists())
        return ok

    def download(self) -> None:
        """Start downloading the latest release's setup program (in the background)."""
        headers = self._headers("application/octet-stream")
        with self._lock:
            if not self.latest:
                raise UpdateError("Check for updates first.")
            if self.state == "downloading":
                return
            self.state, self.error, self.received, self.file = "downloading", "", 0, None
            self._stop.clear()
            self._thread = threading.Thread(target=self._fetch, args=(dict(self.latest), headers),
                                            name="update-download", daemon=True)
            self._thread.start()

    def _fetch(self, latest: dict, headers: dict) -> None:
        folder = self.folder / "updates"
        part = folder / (latest["file"] + ".part")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.iterdir():  # one download kept: earlier setup programs go
                if old.name != latest["file"]:
                    old.unlink(missing_ok=True)
            with self._client() as client:
                r = client.get(latest["sha_url"], headers=headers)
                if r.status_code != 200:
                    raise UpdateError(self._refused(r.status_code))
                words = r.text.split()
                expected = words[0].lower() if words else ""
                if not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise UpdateError("The release's .sha256 file is not a SHA-256 checksum.")
                h = hashlib.sha256()
                with client.stream("GET", latest["url"], headers=headers) as r, open(part, "wb") as out:
                    if r.status_code != 200:
                        raise UpdateError(self._refused(r.status_code))
                    for chunk in r.iter_bytes(1 << 20):
                        if self._stop.is_set():
                            raise UpdateError("The download was stopped.")
                        out.write(chunk)
                        h.update(chunk)
                        self.received += len(chunk)
            if h.hexdigest() != expected:
                raise UpdateError("The downloaded setup program doesn't match the release's checksum: not used.")
            target = folder / latest["file"]
            part.replace(target)
            self.file, self.state = target, "ready"
            self.log("info", f"update {latest['name']} downloaded and checked")
        except UpdateError as e:
            self.state, self.error = "error", str(e)
        except Exception as e:  # noqa: BLE001 - network, disk: shown, the next try starts again
            self.state, self.error = "error", f"Download failed: {_plain(e)}"
        finally:
            if self.state != "ready":
                part.unlink(missing_ok=True)
                if self.error:
                    self.log("warning", f"update: {self.error}")

    # --- installing --------------------------------------------------------------------------------

    def setup_command(self) -> list[str]:
        """The command that installs the downloaded update (checked again just before)."""
        if not installed():
            raise UpdateError("Updates install only in the installed program (this is a development copy).")
        if self.state != "ready" or not self.file or not self.file.exists():
            raise UpdateError("Download the update first.")
        return [str(self.file), *SETUP_ARGS]

    def launch(self, command: list[str]) -> None:
        """Start the setup program on its own: it waits for TrackXen to close, then replaces it."""
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP (Windows)
        subprocess.Popen(command, creationflags=flags, close_fds=True, cwd=str(self.file.parent))  # noqa: S603
        self.log("info", f"installing update {self.latest['name'] if self.latest else ''}: TrackXen closes now")

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {"current": self.current, "installed": installed(), "token_set": bool(self._token()),
                "state": self.state, "error": self.error, "latest": _public(self.latest),
                "newer": self.newer(), "received": self.received}


def _public(latest: dict | None) -> dict | None:
    """The latest release as the UI sees it (no download URLs)."""
    if not latest:
        return None
    return {k: latest[k] for k in ("tag", "build", "name", "published", "notes", "file", "size")}


def _plain(e: Exception) -> str:
    return str(e) or type(e).__name__

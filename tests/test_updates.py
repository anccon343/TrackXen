"""Updates from GitHub (core/updates.py): the latest release is found, downloaded, checked and installed."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from trackxen.core import updates
from trackxen.core.app import App
from trackxen.core.updates import Updater, UpdateError

SETUP = b"MZ fake setup program" * 1000
NAME = "TrackXen-Setup-0.1.0.7.exe"


def release(build=7, sha=True, tag=None):
    assets = [{"name": NAME, "size": len(SETUP), "url": "https://api.github.com/repos/o/r/releases/assets/1"}]
    if sha:
        assets.append({"name": NAME + ".sha256", "size": 80, "url": "https://api.github.com/repos/o/r/releases/assets/2"})
    return {"tag_name": tag or f"build-{build}", "name": f"TrackXen 0.1.0.{build}", "published_at": "2026-11-06T10:00:00Z",
            "body": "Built from abc.", "assets": assets}


def updater(tmp_path, answers: dict, seen: list | None = None) -> Updater:
    """An Updater whose GitHub is `answers`: path -> (status, body bytes or JSON)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        status, body = answers.get(request.url.path, (404, b"{}"))
        return httpx.Response(status, content=body if isinstance(body, bytes) else json.dumps(body).encode())

    return Updater(tmp_path, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def github(rel=None, sha_text=None, setup=SETUP):
    return {"/repos/anccon343/TrackXen/releases/latest": (200, rel or release()),
            "/repos/o/r/releases/assets/2": (200, (sha_text or f"{hashlib.sha256(setup).hexdigest()}  {NAME}").encode()),
            "/repos/o/r/releases/assets/1": (200, setup)}


def wait(u: Updater):
    if u._thread:
        u._thread.join(10)


def test_the_token_is_needed_kept_apart_and_never_shown(tmp_path):
    u = updater(tmp_path, github())
    with pytest.raises(UpdateError, match="token"):
        u.check()
    with pytest.raises(UpdateError):
        u.set_token("not a token!")
    u.set_token("github_pat_" + "a" * 40)
    status = u.status()
    assert status["token_set"] is True and "github_pat" not in json.dumps(status)
    assert json.loads((tmp_path / "update.json").read_text())["token"].startswith("github_pat_")
    u.set_token("")
    assert u.status()["token_set"] is False


def test_the_latest_release_is_found_and_compared_by_build_number(tmp_path, monkeypatch):
    seen = []
    u = updater(tmp_path, github(), seen)
    u.set_token("ghp_" + "b" * 36)
    u.current = {"version": "0.1.0.5", "build": 5, "commit": ""}
    status = u.check()
    assert status["state"] == "idle" and status["newer"] is True
    assert status["latest"]["build"] == 7 and status["latest"]["file"] == NAME
    assert "url" not in status["latest"]  # (the page gets no download addresses)
    assert seen[0].headers["Authorization"] == "Bearer ghp_" + "b" * 36
    u.current = {"version": "0.1.0.7", "build": 7, "commit": ""}
    assert u.check()["newer"] is False


@pytest.mark.parametrize("answers, words", [
    ({"/repos/anccon343/TrackXen/releases/latest": (401, {})}, "refused the access token"),
    ({"/repos/anccon343/TrackXen/releases/latest": (404, {})}, "No release found"),
    ({"/repos/anccon343/TrackXen/releases/latest": (200, release(sha=False))}, "no setup program with its .sha256"),
    ({"/repos/anccon343/TrackXen/releases/latest": (200, release(tag="v1"))}, "wasn't made by the release build"),
])
def test_a_check_that_fails_says_why(tmp_path, answers, words):
    u = updater(tmp_path, answers)
    u.set_token("ghp_" + "c" * 36)
    status = u.check()
    assert status["state"] == "error" and words in status["error"]


def test_the_download_is_used_only_when_its_checksum_matches(tmp_path):
    u = updater(tmp_path, github())
    u.set_token("ghp_" + "d" * 36)
    u.check()
    u.download()
    wait(u)
    assert u.state == "ready" and u.file == tmp_path / "updates" / NAME and u.file.read_bytes() == SETUP
    assert not list((tmp_path / "updates").glob("*.part"))

    bad = updater(tmp_path / "bad", github(sha_text="0" * 64 + "  " + NAME))
    bad.set_token("ghp_" + "d" * 36)
    bad.check()
    bad.download()
    wait(bad)
    assert bad.state == "error" and "checksum" in bad.error
    assert not list((tmp_path / "bad" / "updates").iterdir())  # nothing half-checked is kept


def test_the_setup_program_runs_only_in_the_installed_program(tmp_path, monkeypatch):
    u = updater(tmp_path, github())
    u.set_token("ghp_" + "e" * 36)
    u.check()
    u.download()
    wait(u)
    monkeypatch.setattr(updates, "installed", lambda: False)
    with pytest.raises(UpdateError, match="installed program"):
        u.setup_command()
    monkeypatch.setattr(updates, "installed", lambda: True)
    command = u.setup_command()
    assert command[0] == str(tmp_path / "updates" / NAME)
    assert "/SILENT" in command and "/update=1" in command  # (the setup program then starts TrackXen again)


def app_like(on_air: bool, command=("setup.exe", "/update=1")):
    launched, exits = [], []
    fake = SimpleNamespace(
        sessions={"cam1": SimpleNamespace(on_air=on_air, cfg=SimpleNamespace(name="Pulpit"))},
        updater=SimpleNamespace(setup_command=lambda: list(command), launch=launched.append),
        on_exit=lambda: exits.append(True), log=SimpleNamespace(event=lambda *a, **k: None))
    return fake, launched, exits


def test_an_update_is_not_installed_while_a_camera_is_live():
    fake, launched, exits = app_like(on_air=True)
    reply = asyncio.run(App.install_update(fake))
    assert reply["ok"] is False and "Pulpit on air" in reply["error"] and not launched


def test_installing_starts_the_setup_program_then_closes_trackxen():
    fake, launched, exits = app_like(on_air=False)

    async def run():
        reply = await App.install_update(fake)
        await asyncio.sleep(0.7)  # (it closes just after answering)
        return reply

    assert asyncio.run(run()) == {"ok": True}
    assert launched == [["setup.exe", "/update=1"]] and exits == [True]

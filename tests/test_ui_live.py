"""The web UI's live connection store (ui/src/lib/live.svelte.ts), run in node with a fake WebSocket and fetch.

Needs node (22.13 or later) and the UI's packages (cd ui && npm install); skipped without them.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "ui"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None or not (UI / "node_modules" / "svelte").is_dir(),
                                reason="needs node and the UI's packages (cd ui && npm install)")

# Compiles the stores to plain modules (the svelte compiler, TypeScript types stripped by node), then runs SCENARIO.
BUILD = """
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire, stripTypeScriptTypes } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [ui, out] = process.argv.slice(2);
const require = createRequire(join(ui, "package.json"));
const { compileModule } = require("svelte/compiler");
for (const name of ["access", "live"]) {
  const ts = readFileSync(join(ui, "src", "lib", `${name}.svelte.ts`), "utf8");
  const js = compileModule(stripTypeScriptTypes(ts, { mode: "transform" }),
                           { filename: `${name}.svelte.js`, generate: "client" }).js.code
    .replace(/from (["'])(svelte\\/[^"']+)\\1/g, (_, q, mod) => `from ${q}${pathToFileURL(require.resolve(mod)).href}${q}`)
    .replace(/from (["'])\\.\\/(\\w+)\\.svelte\\1/g, (_, q, mod) => `from ${q}./${mod}.svelte.mjs${q}`);
  writeFileSync(join(out, `${name}.svelte.mjs`), js);
}
"""

# A browser stand-in: timers 50x faster (the 5 s answer timeout takes 0.1 s), a WebSocket the scenario drives,
# and a fetch that records POSTs. Prints {"reply", "frames", "posts", "ms"} for one requestReliably().
SCENARIO = """
const fast = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms = 0, ...args) => fast(fn, ms / 50, ...args);
globalThis.location = { protocol: "http:", host: "192.168.1.5:8090" };
class FakeSocket {
  static OPEN = 1;
  static made = [];
  constructor() { this.readyState = 0; this.sent = []; FakeSocket.made.push(this); }
  send(text) { this.sent.push(JSON.parse(text)); }
}
globalThis.WebSocket = FakeSocket;
const posts = [];
globalThis.fetch = async (url, init = {}) => {
  posts.push({ url, body: JSON.parse(init.body) });
  return { ok: true, status: 200, json: async () => ({ ok: true, via: "http" }) };
};
const { live } = await import("./live.svelte.mjs");
live.connect();
const ws = FakeSocket.made[0];
ws.readyState = 1;
ws.onopen();
const [what] = process.argv.slice(2);
const t0 = Date.now();
const asked = live.requestReliably({ type: "stop_all" });
if (what === "closes") {  // the connection drops right after the frame was written
  fast(() => { ws.readyState = 3; ws.onclose({ code: 1006 }); }, 2);
} else if (what === "refused") {  // the engine answers, and says no
  fast(() => ws.onmessage({ data: JSON.stringify({ type: "reply", request: ws.sent[0].id, ok: false, error: "no" }) }), 2);
} else if (what === "answered") {
  fast(() => ws.onmessage({ data: JSON.stringify({ type: "reply", request: ws.sent[0].id, ok: true }) }), 2);
}  // "silent": a half-open connection, the frame goes nowhere
const reply = await asked;
console.log(JSON.stringify({ reply, frames: ws.sent.length, posts: posts.map((p) => p.body), ms: Date.now() - t0 }));
process.exit(0);
"""


@pytest.fixture(scope="module")
def stores(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("ui_stores")
    (out / "build.mjs").write_text(BUILD, encoding="utf-8")
    (out / "scenario.mjs").write_text(SCENARIO, encoding="utf-8")
    run = subprocess.run([NODE, "--no-warnings", str(out / "build.mjs"), str(UI), str(out)],
                         capture_output=True, text=True, timeout=60)
    if run.returncode and "stripTypeScriptTypes" in run.stderr:
        pytest.skip("needs node 22.13 or later")
    assert run.returncode == 0, run.stderr
    return out


def stop_all(stores: Path, what: str) -> dict:
    run = subprocess.run([NODE, "--no-warnings", str(stores / "scenario.mjs"), what], capture_output=True, text=True,
                         timeout=60, cwd=stores)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_stop_all_answered_on_the_live_connection_is_sent_once(stores):
    result = stop_all(stores, "answered")
    assert result["reply"] == {"ok": True, "request": 1, "type": "reply"} and result["posts"] == []


def test_stop_all_refused_by_the_engine_is_not_sent_again(stores):
    result = stop_all(stores, "refused")
    assert result["reply"]["ok"] is False and result["posts"] == []


def test_stop_all_goes_over_http_when_the_connection_drops_meanwhile(stores):
    """Sent just as the connection dropped: the page knows at once, so it sends the stop again over HTTP
    instead of waiting 5 s for an answer that can't come (and then only saying "press it again")."""
    result = stop_all(stores, "closes")
    assert result["frames"] == 1 and result["posts"] == [{"type": "stop_all"}]
    assert result["reply"] == {"ok": True, "via": "http"}
    assert result["ms"] < 50  # (the answer timeout is 100 ms here: timers run 50x faster)


def test_stop_all_goes_over_http_when_the_connection_never_answers(stores):
    """A half-open connection (the network path gone, no close): every press went into it, never over HTTP."""
    result = stop_all(stores, "silent")
    assert result["frames"] == 1 and result["posts"] == [{"type": "stop_all"}]
    assert result["reply"] == {"ok": True, "via": "http"}

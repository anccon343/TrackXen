import asyncio
import socket
import sys

import pytest

from trackxen.devices import visca
from trackxen.devices.udp import disable_connection_reset
from trackxen.sim.fake_camera import FakeCamera

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only network behaviour")


def closed_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def receive_after_sending_to_nobody(fixed: bool) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if fixed:
        disable_connection_reset(s)
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.3)
    s.sendto(b"x", ("127.0.0.1", closed_port()))
    try:
        s.recvfrom(100)
        return "data"
    except TimeoutError:
        return "nothing"
    except ConnectionResetError:
        return "reset"
    finally:
        s.close()


@windows_only
def test_windows_reports_resets_unless_disabled():
    assert receive_after_sending_to_nobody(fixed=False) == "reset"  # the trap
    assert receive_after_sending_to_nobody(fixed=True) == "nothing"  # our sockets


def test_camera_keeps_answering_clients_that_quit_early():
    """Each client quits before the camera's delayed completion replies arrive (the reset trigger)."""
    async def run() -> list[str]:
        cam = FakeCamera(command_delay=0.03)
        port = await cam.start(port=0)
        results = []
        try:
            for _ in range(4):
                client = visca.ViscaClient("127.0.0.1", port, timeout=0.5)
                await client.connect()
                try:
                    await client.pan_tilt_position()
                    await client.stop_all()
                    results.append("ok")
                except visca.ViscaError:
                    results.append("no reply")
                finally:
                    client.close()
                await asyncio.sleep(0.15)
        finally:
            cam.stop()
        return results

    assert asyncio.run(run()) == ["ok"] * 4

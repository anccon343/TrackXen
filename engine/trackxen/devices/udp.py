"""UDP endpoints that survive Windows' "connection reset" reports.

When we send to a port nobody listens on (a tool that already quit, a rebooting camera or
mixer), Windows reports it as WinError 10054 on the socket's *next receive*, and Python
3.12's Windows event loop then stops reading that socket for good (asyncio
proactor_events: an OSError in _loop_reading never re-arms the read). Reproduced on
2026-10-31: a fake camera answered one VISCA client, then never again.

The fix is the Winsock option SIO_UDP_CONNRESET = FALSE. Python's socket module neither
defines that constant nor lets socket.ioctl() set it, so it is set with WSAIoctl via
ctypes, and failure raises instead of passing silently.

Sockets are left unconnected (asyncio can't send on a pre-connected socket it was handed),
so callers pass the destination to `sendto` and filter replies by sender address.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import sys

SIO_UDP_CONNRESET = 0x9800000C  # _WSAIOW(IOC_VENDOR, 12)


def disable_connection_reset(sock: socket.socket) -> None:
    """Stop Windows from reporting ICMP "port unreachable" as errors on this UDP socket."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    ws2 = ctypes.WinDLL("ws2_32", use_last_error=True)
    wsa_ioctl = ws2.WSAIoctl
    wsa_ioctl.argtypes = [ctypes.c_size_t, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p]
    wsa_ioctl.restype = ctypes.c_int
    value, returned = wintypes.BOOL(False), wintypes.DWORD(0)
    if wsa_ioctl(sock.fileno(), SIO_UDP_CONNRESET, ctypes.byref(value), ctypes.sizeof(value), None, 0,
                 ctypes.byref(returned), None, None) != 0:
        raise OSError(ctypes.get_last_error(), "WSAIoctl(SIO_UDP_CONNRESET) failed")


def udp_socket(local: tuple[str, int] = ("0.0.0.0", 0), broadcast: bool = False) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        disable_connection_reset(sock)
        if broadcast:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(local)
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


async def open_udp(protocol_factory, local: tuple[str, int] = ("0.0.0.0", 0), broadcast: bool = False):
    """Like loop.create_datagram_endpoint(local_addr=...), with the Windows fix applied."""
    loop = asyncio.get_running_loop()
    return await loop.create_datagram_endpoint(protocol_factory, sock=udp_socket(local, broadcast))


def resolve(host: str) -> str:
    """IPv4 address of a host name (replies are matched against it)."""
    return socket.gethostbyname(host)


async def resolve_async(host: str) -> str:
    """resolve() without blocking the event loop: a name that resolves slowly (or not at all) froze every
    camera loop and the UI for the resolver's timeout. An IPv4 address, the usual case, needs no lookup."""
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        return await asyncio.to_thread(resolve, host)

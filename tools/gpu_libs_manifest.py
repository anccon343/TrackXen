"""Build step: list the NVIDIA DLLs TrackXen downloads on its first start (core/gpu_libs.py).

  python tools/gpu_libs_manifest.py           # writes engine/trackxen/core/assets/gpu-libs.json
  python tools/gpu_libs_manifest.py --check   # (build step) does it match the installed packages?

For every DLL of the CUDA, cuDNN and TensorRT packages installed in this environment (the
versions TrackXen is built and tested with), it records where it lies inside NVIDIA's official
wheel: the wheel's URL (CUDA and cuDNN on PyPI, TensorRT on pypi.nvidia.com) and the byte range
of the compressed file (read from the wheel's zip directory with HTTP range requests, a few
kilobytes per wheel). The size and SHA-256 come from the installed file itself, after checking
that its size and CRC match the wheel's (the installed TensorRT package's RECORD lists sizes
from before NVIDIA signed the DLLs, so it can't be used). The app then fetches exactly those
bytes and checks each file against that SHA-256.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import re
import struct
import sys
import zipfile
import zlib
from io import BytesIO
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent.parent / "engine" / "trackxen" / "core" / "assets" / "gpu-libs.json"
PACKAGES = ["nvidia-cuda-runtime", "nvidia-cublas", "nvidia-cufft", "nvidia-curand", "nvidia-cuda-nvrtc",
            "nvidia-nvjitlink", "nvidia-cudnn-cu13", "tensorrt-cu13-libs"]
NVIDIA_INDEX = "https://pypi.nvidia.com"
SKIP = re.compile(r"(nvblas|cufftw)", re.I)  # not used by ONNX Runtime
BUILDER = re.compile(r"nvinfer_builder_resource_(sm\d+|ptx)_\d+\.dll$", re.I)  # one per GPU generation


def wheel_url(client: httpx.Client, name: str, version: str) -> tuple[str, str]:
    """(url, sha256) of the Windows wheel."""
    r = client.get(f"https://pypi.org/pypi/{name}/{version}/json")
    if r.status_code == 200:
        for f in r.json()["urls"]:
            if f["filename"].endswith("win_amd64.whl"):
                return f["url"], f["digests"]["sha256"]
    page = client.get(f"{NVIDIA_INDEX}/{name}/").text  # TensorRT: PyPI has only a placeholder
    for href in re.findall(r'href="([^"]+)"', page):
        if version in href and href.split("#")[0].endswith("win_amd64.whl"):
            url = str(httpx.URL(f"{NVIDIA_INDEX}/{name}/").join(href.split("#")[0]))
            return url, href.split("sha256=")[-1] if "sha256=" in href else ""
    raise LookupError(f"no Windows wheel for {name} {version}")


def get_range(client: httpx.Client, url: str, start: int, end: int) -> bytes:
    r = client.get(url, headers={"Range": f"bytes={start}-{end}"})
    if r.status_code != 206:
        raise OSError(f"{url}: no range support (HTTP {r.status_code})")
    return r.content


def zip_members(client: httpx.Client, url: str) -> tuple[int, dict[str, zipfile.ZipInfo]]:
    """The wheel's size and its zip directory, read from the end of the file."""
    size = int(client.head(url).headers["content-length"])
    tail = get_range(client, url, max(0, size - 65_536), size - 1)
    eocd = tail.rfind(b"PK\x05\x06")
    cd_size, cd_offset = struct.unpack("<II", tail[eocd + 12:eocd + 20])
    if cd_offset == 0xFFFFFFFF:
        raise NotImplementedError(f"{url}: a ZIP64 wheel (over 4 GB); not handled")
    directory = get_range(client, url, cd_offset, cd_offset + cd_size - 1)
    # zipfile reads the directory from a stand-in: the directory, then its end record
    fake = BytesIO(directory + tail[eocd:eocd + 12] + struct.pack("<II", cd_size, 0) + tail[eocd + 20:])
    members = {i.filename: i for i in zipfile.ZipFile(fake).infolist()}
    return size, members


def file_digest(path: Path) -> tuple[int, int, str]:
    """(size, CRC-32, SHA-256) of a file."""
    sha, crc, size = hashlib.sha256(), 0, 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            sha.update(chunk)
            crc = zlib.crc32(chunk, crc)
            size += len(chunk)
    return size, crc, sha.hexdigest()


def check() -> int:
    """No network: the list's package versions are the installed ones."""
    listed = json.loads(OUT.read_text(encoding="utf-8"))["versions"] if OUT.exists() else {}
    installed = {name: md.distribution(name).version for name in PACKAGES}
    if listed != installed:
        print(f"{OUT.name} is for {listed or 'nothing'}, installed are {installed}")
        return 1
    print(f"{OUT.name} matches the installed NVIDIA packages")
    return 0


def main() -> int:
    if "--check" in sys.argv:
        return check()
    files, versions = [], {}
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "TrackXen build"}) as client:
        for name in PACKAGES:
            dist = md.distribution(name)
            versions[name] = dist.version
            url, wheel_sha = wheel_url(client, name, dist.version)
            size, members = zip_members(client, url)
            print(f"{name} {dist.version}: {size / 1e6:.0f} MB wheel, {url.split('/')[2]}")
            for record in dist.files or []:
                path = str(record).replace("\\", "/")
                if not path.lower().endswith(".dll") or SKIP.search(path):
                    continue
                info = members[path]
                header = get_range(client, url, info.header_offset, info.header_offset + 29)
                name_len, extra_len = struct.unpack("<HH", header[26:30])
                size, crc, sha = file_digest(Path(dist.locate_file(record)))
                if (size, crc) != (info.file_size, info.CRC):
                    raise ValueError(f"{path}: the installed file ({size} bytes, CRC {crc:08x}) isn't the wheel's "
                                     f"({info.file_size} bytes, CRC {info.CRC:08x})")
                builder = BUILDER.search(path)
                files.append({"path": path, "size": info.file_size, "sha256": sha, "url": url,
                              "data_offset": info.header_offset + 30 + name_len + extra_len,
                              "compressed": info.compress_size, "method": info.compress_type, "crc": info.CRC,
                              "gpu": builder.group(1).lower() if builder else None, "package": name})
                print(f"  {info.compress_size / 1e6:7.1f} MB  {path}")
    v = versions
    manifest = {"id": f"cuda{v['nvidia-cuda-runtime'].rsplit('.', 1)[0]}-cudnn{v['nvidia-cudnn-cu13'].rsplit('.', 2)[0]}"
                      f"-trt{'.'.join(v['tensorrt-cu13-libs'].split('.')[:3])}",
                "versions": versions, "files": files}
    OUT.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    common = sum(f["compressed"] for f in files if not f["gpu"])
    print(f"{OUT.name}: {len(files)} files, {common / 1e9:.2f} GB for every PC plus one TensorRT builder file "
          f"per GPU generation ({', '.join(sorted(f['gpu'] for f in files if f['gpu']))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

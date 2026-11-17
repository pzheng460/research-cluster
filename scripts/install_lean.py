"""Install the pinned Linux toolchain locally; run with `uv run --with zstandard`."""
import hashlib
from pathlib import Path
import tarfile
import urllib.request

import zstandard

VERSION = "4.35.0"
DIGEST = "7289597a44543246c9227b8cae501767e095ce17f6d448b95fc4a8dc97268f6d"
root = Path(__file__).resolve().parents[1] / ".state" / "toolchains"
target = root / f"lean-{VERSION}-linux"
if (target / "bin" / "leanchecker").is_file():
    print(target)
else:
    root.mkdir(parents=True, exist_ok=True)
    archive = root / f"lean-{VERSION}-linux.tar.zst"
    if not archive.exists():
        url = f"https://github.com/leanprover/lean4/releases/download/v{VERSION}/{archive.name}"
        print(f"Downloading Lean {VERSION} to {root}", flush=True)
        with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    with archive.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != DIGEST:
        archive.unlink()
        raise SystemExit("Lean archive checksum mismatch; removed incomplete download")
    print("Checksum verified; extracting pinned Lean toolchain", flush=True)
    with archive.open("rb") as source, zstandard.ZstdDecompressor().stream_reader(source) as stream:
        with tarfile.open(fileobj=stream, mode="r|") as package:
            package.extractall(root, filter="data")
    print(target, flush=True)

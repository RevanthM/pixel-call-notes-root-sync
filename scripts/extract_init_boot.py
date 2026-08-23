#!/usr/bin/env python3
"""Extract init_boot.img from an official nested Pixel factory-image ZIP."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract(factory_zip: Path) -> bytes:
    with zipfile.ZipFile(factory_zip) as outer:
        direct = [name for name in outer.namelist() if Path(name).name == "init_boot.img"]
        candidates: list[tuple[str, bytes]] = []
        for name in direct:
            candidates.append((name, outer.read(name)))

        for nested_name in outer.namelist():
            if not nested_name.lower().endswith(".zip"):
                continue
            nested_path = None
            try:
                # Pixel image ZIPs are several gigabytes. Spill to disk instead of
                # loading the nested archive into memory.
                with tempfile.NamedTemporaryFile(
                    suffix=".zip", delete=False
                ) as nested_file:
                    nested_path = Path(nested_file.name)
                    with outer.open(nested_name) as source:
                        shutil.copyfileobj(source, nested_file, length=1024 * 1024)
                with zipfile.ZipFile(nested_path) as nested:
                    for name in nested.namelist():
                        if Path(name).name == "init_boot.img":
                            candidates.append(
                                (f"{nested_name}:{name}", nested.read(name))
                            )
            except zipfile.BadZipFile:
                continue
            finally:
                if nested_path is not None:
                    nested_path.unlink(missing_ok=True)

    if not candidates:
        raise SystemExit("No init_boot.img found in the factory archive.")
    unique = {sha256(data): (name, data) for name, data in candidates}
    if len(unique) != 1:
        names = ", ".join(name for name, _ in candidates)
        raise SystemExit(f"Several different init_boot.img files found: {names}")
    return next(iter(unique.values()))[1]


def write_private_temp(directory: Path, data: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(dir=directory, prefix=".init-boot-", suffix=".tmp")
    path = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("factory_zip", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if not args.factory_zip.is_file():
        raise SystemExit(f"Factory ZIP not found: {args.factory_zip}")

    data = extract(args.factory_zip)
    if args.output_dir.is_symlink():
        raise SystemExit("Refusing a symlinked output directory.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "init_boot.img"
    checksum = args.output_dir / "init_boot.img.sha256"
    if output.is_symlink() or checksum.is_symlink():
        raise SystemExit("Refusing symlinked boot-image output paths.")
    if output.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite {output}; pass --force after reviewing it.")
    digest = sha256(data)
    image_temp = write_private_temp(args.output_dir, data)
    checksum_temp = write_private_temp(
        args.output_dir, f"{digest}  init_boot.img\n".encode()
    )
    try:
        # Remove an old checksum first so an interruption cannot make stale
        # provenance appear to describe a newly replaced image.
        checksum.unlink(missing_ok=True)
        os.replace(image_temp, output)
        os.replace(checksum_temp, checksum)
        directory_fd = os.open(args.output_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        image_temp.unlink(missing_ok=True)
        checksum_temp.unlink(missing_ok=True)
    print(f"wrote {output} ({len(data)} bytes)")
    print(f"sha256 {digest}")
    print("Compare this build and hash with your own recovery records before patching.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

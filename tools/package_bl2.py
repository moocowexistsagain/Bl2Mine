#!/usr/bin/env python3
"""Build installable BorderCraft packages for the current and legacy willow2 SDKs.

The current SDK accepts the .sdkmod archive directly in <game>/sdk_mods/. The legacy ZIP contains
one BorderCraft folder; extract that folder into sdk_mods/.

Usage: python3 tools/package_bl2.py [--output-dir release]
"""
from __future__ import annotations

import argparse
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_SOURCE = ROOT / "bl2sdk" / "BorderCraft"
PROTOCOL_SOURCE = ROOT / "protocol" / "python" / "bordercraft_protocol.py"
MOD_NAME = "BorderCraft"
# ZIP's portable timestamp floor. Fixed metadata makes release archives byte-for-byte reproducible.
ARCHIVE_DATE = (1980, 1, 1, 0, 0, 0)
REQUIRED_FILES = (
    "__init__.py",
    "host.py",
    "render.py",
    "pyproject.toml",
    "bordercraft_protocol.py",
)


def project_version() -> str:
    pyproject = (MOD_SOURCE / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([0-9]+(?:\.[0-9A-Za-z_-]+)*)"\s*$', pyproject, re.M)
    if match is None:
        raise ValueError("Could not find a simple [project].version in the BorderCraft pyproject.toml")
    return match.group(1)


def stage_package(stage: Path) -> None:
    stage.mkdir(parents=True, exist_ok=True)
    for source in MOD_SOURCE.iterdir():
        if source.is_file() and source.name != "bordercraft_protocol.py":
            shutil.copy2(source, stage / source.name)
    # Keep protocol/bordercraft_protocol.py as the single canonical source. The installed mod gets
    # a sibling copy so it is self-contained, including when imported from inside a .sdkmod zip.
    shutil.copy2(PROTOCOL_SOURCE, stage / "bordercraft_protocol.py")

    missing = [name for name in REQUIRED_FILES if not (stage / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Incomplete BL2 mod package; missing: {', '.join(missing)}")

    for source in stage.glob("*.py"):
        compile(source.read_bytes(), str(source), "exec")


def _archive_info(name: str, directory: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ARCHIVE_DATE)
    info.create_system = 3  # Unix permissions, even when packaging on Windows.
    info.compress_type = zipfile.ZIP_STORED if directory else zipfile.ZIP_DEFLATED
    info.external_attr = ((0o40755 if directory else 0o100644) << 16) | (0x10 if directory else 0)
    return info


def write_archive(output: Path, stage: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        # The current willow2 SDK requires a .sdkmod archive to have exactly one root folder,
        # named the same as the archive stem. The same layout also makes a conventional ZIP easy
        # to extract into sdk_mods/. Use fixed timestamps and permissions so two builds are equal.
        archive.writestr(_archive_info(f"{MOD_NAME}/", directory=True), b"")
        for source in sorted(stage.iterdir()):
            if source.is_file():
                archive.writestr(
                    _archive_info(f"{MOD_NAME}/{source.name}"),
                    source.read_bytes(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )

    validate_archive(output)


def validate_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        roots = {name.split("/", 1)[0] for name in names if name}
        if roots != {path.stem if path.suffix == ".sdkmod" else MOD_NAME}:
            raise ValueError(f"Unexpected archive root in {path}: {sorted(roots)}")
        prefix = f"{MOD_NAME}/"
        files = {name[len(prefix):] for name in names if name.startswith(prefix) and not name.endswith("/")}
        missing = set(REQUIRED_FILES) - files
        if missing:
            raise ValueError(f"{path} is missing required mod files: {', '.join(sorted(missing))}")
        if archive.read(prefix + "bordercraft_protocol.py") != PROTOCOL_SOURCE.read_bytes():
            raise ValueError(f"{path} contains a stale protocol copy")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "release",
        help="where to write BorderCraft.sdkmod and the legacy ZIP (default: release/)",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    version = project_version()

    with tempfile.TemporaryDirectory(prefix="bordercraft-sdk-") as temporary:
        stage = Path(temporary) / MOD_NAME
        stage_package(stage)

        sdkmod = output_dir / f"{MOD_NAME}.sdkmod"
        legacy_zip = output_dir / f"{MOD_NAME}-{version}-legacy.zip"
        write_archive(sdkmod, stage)
        write_archive(legacy_zip, stage)

    print(f"Built {sdkmod}")
    print(f"Built {legacy_zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

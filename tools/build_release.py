"""Build the Windows x64 portable ZIP from a clean source checkout."""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.0.2"
PROGRAM = "VRC语伴"


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True,
                                   encoding="utf-8").strip()


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_distribution_file(package: str, suffix: str, destination: Path) -> None:
    distribution = metadata.distribution(package)
    matches = [item for item in distribution.files or []
               if str(item).replace("\\", "/").endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {suffix} in {package}, got {len(matches)}")
    shutil.copyfile(distribution.locate_file(matches[0]), destination)


def main() -> None:
    if (sys.platform != "win32" or struct.calcsize("P") != 8
            or platform.machine().upper() not in {"AMD64", "X86_64"}):
        raise SystemExit("Build with 64-bit Python on Windows x64.")
    if git("status", "--porcelain"):
        raise SystemExit("Commit source changes before building a release.")
    for directory in (ROOT / "dist", ROOT / "build"):
        if not directory.resolve().is_relative_to(ROOT):
            raise SystemExit("Build output must stay inside the source checkout.")
    commit = git("rev-parse", "HEAD")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir", "--windowed", "--name", PROGRAM,
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        "--version-file", str(ROOT / "packaging" / "windows-version.txt"),
        str(ROOT / "vrchat_linguabar.py"),
    ], cwd=ROOT, check=True)

    bundle = ROOT / "dist" / PROGRAM
    licenses = bundle / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "LICENSE", bundle / "LICENSE.txt")
    shutil.copyfile(ROOT / "packaging" / "使用说明.txt", bundle / "使用说明.txt")
    shutil.copytree(ROOT / "docs", bundle / "docs", dirs_exist_ok=True)
    for package, suffix, filename in (
        ("PyQt5-Qt5", ".dist-info/LICENSE", "Qt.txt"),
        ("PyQt5-sip", ".dist-info/licenses/LICENSE", "PyQt5-sip.txt"),
        ("pywin32", ".dist-info/licenses/win32/License.txt", "pywin32.txt"),
        ("pyinstaller", ".dist-info/licenses/COPYING.txt", "PyInstaller.txt"),
    ):
        copy_distribution_file(package, suffix, licenses / filename)
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python.txt")
    (licenses / "python-osc.txt").write_text(
        metadata.metadata("python-osc")["License"], encoding="utf-8")
    (licenses / "PyQt5.txt").write_text(
        "PyQt5 5.15.11 — Copyright Riverbank Computing Limited.\n"
        "License: GPL v3. See ../LICENSE.txt.\n"
        "https://www.riverbankcomputing.com/software/pyqt/\n", encoding="utf-8")
    (bundle / "SOURCE.txt").write_text(
        f"VRC 语伴 {VERSION}\nCommit: {commit}\n"
        f"Source: https://github.com/sunzhaocn/VRC-LinguaBar/archive/refs/tags/{VERSION}.zip\n"
        "Build: install requirements-build.txt, then run python tools/build_release.py.\n",
        encoding="utf-8")
    manifest = {
        "application": "VRC 语伴", "version": VERSION, "source_commit": commit,
        "platform": "Windows x64", "python": platform.python_version(),
        "entrypoint": f"{PROGRAM}.exe", "layout": "onedir",
        "packages": {name: metadata.version(name) for name in (
            "PyQt5", "PyQt5-Qt5", "PyQt5-sip", "python-osc", "pywin32",
            "PyInstaller", "pyinstaller-hooks-contrib")},
        "executable_sha256": sha256(bundle / f"{PROGRAM}.exe"),
    }
    (bundle / "build-info.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    archive = ROOT / "dist" / f"VRC-Yuban-{VERSION}-Windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(bundle.parent))
    checksum = sha256(archive)
    (ROOT / "dist" / "SHA256SUMS.txt").write_text(
        f"{checksum}  {archive.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size,
                      "sha256": checksum, "source_commit": commit}, ensure_ascii=False))


if __name__ == "__main__":
    main()

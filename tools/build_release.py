"""Build a source runner ZIP from public files only; never include local input/secrets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib
import zipfile

from build_docs_site import PUBLIC_DOCS

ROOT_FILES = {
    "README.md",
    "CONTRIBUTING.md",
    ".gitattributes",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "compose.yaml",
    ".env.example",
    ".dockerignore",
    ".gitignore",
    "LICENSE",
    "NOTICE",
    "compose.offline.yaml",
    "compose.gpu.yaml",
    "pipeline.example.toml",
}
FOLDERS = {"src", "tests", "examples", "docs", "deploy", "tools", ".github"}


def public_file(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if (
        relative.parts[0] == "docs"
        and relative.as_posix().removeprefix("docs/") not in PUBLIC_DOCS
    ):
        return False
    return (
        not any(
            part in {"__pycache__", ".venv", "local", "output", "dist", "build", ".git"}
            for part in relative.parts
        )
        and not (path.name.startswith(".env") and path.name != ".env.example")
        and path.suffix not in {".pyc", ".log", ".sqlite", ".duckdb"}
        and path.name != ".DS_Store"
    )


def build_runner(root: Path, destination: Path) -> Path:
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    destination.mkdir(parents=True, exist_ok=True)
    package = destination / f"semantic-plausibility-runner-{version}.zip"
    files = [root / name for name in sorted(ROOT_FILES) if (root / name).is_file()]
    for folder in sorted(FOLDERS):
        files.extend(
            path for path in sorted((root / folder).rglob("*")) if path.is_file() and public_file(path, root)
        )
    if any(path.is_symlink() for path in files):
        raise ValueError("Runner distribution must contain regular files, not symlinks")
    prefix = f"semantic-plausibility-{version}/"
    digests = {}
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            archive.writestr(prefix + relative, data)
            digests[relative] = hashlib.sha256(data).hexdigest()
        wheel = destination / f"semantic_plausibility-{version}-py3-none-any.whl"
        if wheel.is_file():
            data = wheel.read_bytes()
            relative = "wheels/" + wheel.name
            archive.writestr(prefix + relative, data)
            digests[relative] = hashlib.sha256(data).hexdigest()
        archive.writestr(
            prefix + "runner-manifest.json",
            json.dumps({"version": version, "files": digests}, ensure_ascii=False, indent=2) + "\n",
        )
    print(package.resolve())
    return package


if __name__ == "__main__":
    build_runner(Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[1] / "dist")

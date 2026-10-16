"""Copy only the current public manual/examples into a standalone GitHub Pages site."""

from pathlib import Path
import shutil

PUBLIC_DOCS = {
    "index.html",
    "shipped-stack.md",
    "data-contract.md",
    "workflow.md",
    "configuration.md",
    "mii-quality.md",
    "studies.md",
    "implementation.md",
    "validation.md",
    "research.md",
    "lookups.md",
    "architecture.svg",
    "architecture.mmd",
    "decision-flow.svg",
    "decision-flow.mmd",
}


def build_site(root, output):
    if output.exists():
        raise ValueError("Choose a new site output directory")
    (output / "docs").mkdir(parents=True)
    for name in sorted(PUBLIC_DOCS):
        shutil.copyfile(root / "docs" / name, output / "docs" / name)
    shutil.copytree(root / "examples" / "studies", output / "examples" / "studies")
    for name in ("laboratory.csv", "laboratory.bundle.json", "laboratory-research.csv", "laboratory-fhir.csv", "laboratory-fhir-invalid.csv",
                 "medication-request.csv", "medication-administration.csv", "medication-statement.csv", "medication-product.csv", "README.md"):
        shutil.copyfile(root / "examples" / name, output / "examples" / name)
    shutil.copytree(root / "examples" / "lab", output / "examples" / "lab")
    shutil.copytree(root / "examples" / "fhir", output / "examples" / "fhir")
    shutil.copytree(root / "examples" / "medication", output / "examples" / "medication")
    for name in ("pipeline.example.toml", "catalog.csv", "README.md", "LICENSE"):
        shutil.copyfile(root / name, output / name)
    (output / "tools").mkdir()
    for name in ("run-podman-examples.sh", "container-cache.sh"):
        shutil.copyfile(root / "tools" / name, output / "tools" / name)
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="0;url=docs/index.html"><title>Laboratory Semantic Review</title>'
        '<h1>Laboratory Semantic Review</h1><p><a href="docs/index.html">Open the English manual</a></p></html>'
    )
    (output / ".nojekyll").write_text("")
    return output


if __name__ == "__main__":
    print(build_site(Path(__file__).resolve().parents[1], Path("build/site")).resolve())

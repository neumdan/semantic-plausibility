"""Direct Python entry point; install the locked project dependencies first."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from semantic_plausibility.cli import run_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_main())

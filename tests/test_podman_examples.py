"""Exercise the real shell plan and public packaging; no fabricated service replies."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/run-podman-examples.sh"


@unittest.skipUnless(shutil.which("bash"), "Example launcher requires Bash")
class PodmanExampleTests(unittest.TestCase):
    def test_plan_needs_no_engine_and_does_not_create_configuration_or_reports(self):
        with tempfile.TemporaryDirectory(prefix="example checkout with spaces ") as name:
            root = Path(name)
            (root / "tools").mkdir()
            script = root / "tools/run-podman-examples.sh"
            shutil.copyfile(SCRIPT, script)
            shutil.copyfile(ROOT / "tools/container-cache.sh", root / "tools/container-cache.sh")
            before = sorted(p.relative_to(root) for p in root.rglob("*"))
            result = subprocess.run(
                [shutil.which("bash"), str(script), "--dry-run"], cwd=root,
                env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(p.relative_to(root) for p in root.rglob("*")), before)
            commands = [line for line in result.stdout.splitlines() if " --out " in line]
            self.assertEqual(len(commands), 6)
            self.assertEqual(len({line.split(" --out ")[1].strip() for line in commands}), 6)
            for line in commands[2:4]:
                self.assertIn("/01-lab-lookup/report.csv", line)
            self.assertIn("--scope lookup", commands[2])
            self.assertIn("--scope studies", commands[3])
            self.assertIn("/examples/lab/fhir-csv.toml", commands[1])
            self.assertIn("/examples/medication/request.toml", commands[4])
            self.assertIn("/examples/medication/administration.toml", commands[5])
            self.assertLess(result.stdout.index("pc stop matchbox blaze"), result.stdout.index("pc up -d ollama"))
            self.assertIn("Compose project: semantic-plausibility-examples", result.stdout)
            self.assertNotIn("--build matchbox", result.stdout)

    def test_bad_arguments_fail_before_changing_a_checkout(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "tools").mkdir()
            script = root / "tools/run-podman-examples.sh"
            shutil.copyfile(SCRIPT, script)
            before = sorted(p.relative_to(root) for p in root.rglob("*"))
            for arguments in (["--unknown"], ["--dry-run", "unexpected"]):
                result = subprocess.run(
                    [shutil.which("bash"), str(script), *arguments], cwd=root,
                    capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(sorted(p.relative_to(root) for p in root.rglob("*")), before)

    def test_script_ships_with_public_runner_and_readme_site(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        from build_docs_site import build_site
        from build_release import public_file

        self.assertTrue(public_file(SCRIPT, ROOT))
        with tempfile.TemporaryDirectory() as name:
            site = build_site(ROOT, Path(name) / "site")
            self.assertEqual((site / "tools/run-podman-examples.sh").read_bytes(), SCRIPT.read_bytes())
            self.assertEqual((site / "tools/container-cache.sh").read_bytes(), (ROOT / "tools/container-cache.sh").read_bytes())

    def test_cache_fingerprint_tracks_content_and_names_not_checkout_location_or_mtime(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            first, second = root / "first", root / "second"
            for folder in (first, second):
                (folder / "src").mkdir(parents=True)
                (folder / "src/data.txt").write_text("original")
            def fingerprint(folder):
                return subprocess.check_output([
                    shutil.which("bash"), "-euo", "pipefail", "-c",
                    'source "$1"; sqc_fingerprint "$2" src', "bash",
                    str(ROOT / "tools/container-cache.sh"), str(folder),
                ], text=True, timeout=15).strip()
            original = fingerprint(first)
            (second / "src/data.txt").touch()
            self.assertEqual(original, fingerprint(second))
            (second / "src/__pycache__").mkdir()
            (second / "src/__pycache__/ignored.pyc").write_bytes(b"regenerated")
            self.assertEqual(original, fingerprint(second))
            (second / "src/data.txt").write_text("changed")
            self.assertNotEqual(original, fingerprint(second))
            (second / "src/data.txt").write_text("original")
            (second / "src/data.txt").rename(second / "src/renamed.txt")
            self.assertNotEqual(original, fingerprint(second))


if __name__ == "__main__":
    unittest.main()

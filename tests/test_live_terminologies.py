"""Actual public sources; licensed/authenticated tests need explicit opt-in."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility.name_checks import check_name
from semantic_plausibility.terminology_apis import OlsNameSource, WhoIcdSource, ICD11_MMS
from semantic_plausibility.terminology_packages import install_package, package_metadata
from semantic_plausibility.terminology_router import FhirArtifactSource, TerminologyRouter


@unittest.skipUnless(os.getenv("SQC_TEST_BFARM_DIR"), "Set SQC_TEST_BFARM_DIR to your already-downloaded real BfArM lab JSONs")
class DownloadedTerminologyTests(unittest.TestCase):
    def test_downloaded_labels_keep_publisher_hash_and_edition_guard(self):
        source = FhirArtifactSource(Path(os.environ["SQC_TEST_BFARM_DIR"]))
        labels = source.labels("http://loinc.org", "718-7", "2.83")
        self.assertTrue(labels)
        for label in labels:
            self.assertTrue(label["source_url"].startswith("https://terminologien.bfarm.de/"))
            self.assertEqual(len(label["source_sha256"]), 64)
            self.assertEqual(label["version"], "2.83")
        self.assertEqual(source.labels("http://loinc.org", "718-7", "2.82"), [])
        self.assertEqual(source.check("http://loinc.org", "718-7")["state"], "unknown")
        found = source.search_names(labels[0]["value"])
        self.assertTrue(any(row["code"] == "718-7" and row["source_sha256"] == labels[0]["source_sha256"] for row in found))


@unittest.skipUnless(os.getenv("SQC_LIVE_TESTS") == "1", "Set SQC_LIVE_TESTS=1 for real public services")
class PublicTerminologyTests(unittest.TestCase):
    def test_bfarm_metadata_and_explicit_download_conditions(self):
        for name, version in [("bfarm.terminologien.loinc", "2.82.0"), ("bfarm.terminologien.ucum", "1.5.0"), ("bfarm.terminologien.atcgm", "2026.0.0")]:
            metadata = package_metadata(name, version)
            self.assertEqual(metadata["version"], version)
            self.assertTrue(metadata["protected"])
            self.assertTrue(metadata["download-conditions"].startswith("https://terminologien.bfarm.de/"))
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "package"
                with self.assertRaisesRegex(ValueError, "accept_download_conditions"):
                    install_package(name, version, target)
                self.assertFalse(target.exists())

    def test_real_public_package_install_and_corruption_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "hddt"
            receipt = install_package("de.gematik.hddt.terminology", "1.0.0", target)
            self.assertTrue(receipt["downloaded"])
            self.assertEqual(len(receipt["archive_sha256"]), 64)
            source = FhirArtifactSource(target)
            system = "https://gematik.de/fhir/hddt/CodeSystem/hddt-lung-function-reference-value-method-codes"
            result = source.check(system, "personal-best", "1.0.2")
            self.assertEqual(result["state"], "valid")
            self.assertEqual(result["metadata"]["display"], "Personal Best")
            candidates = source.search_names("Personal Best")
            self.assertTrue(
                any(row["code"] == "personal-best" and row["state"] == "candidate" for row in candidates)
            )
            self.assertEqual(source.check(system, "this-code-does-not-exist", "1.0.2")["state"], "invalid")
            self.assertEqual(source.check(system, "personal-best", "not-this-edition")["state"], "unknown")
            router = TerminologyRouter([{"kind": "fhir_artifacts", "path": str(target), "systems": [system]}])
            self.assertEqual(
                check_name("Personal Best", system, "personal-best", "1.0.2", router=router)["state"],
                "exact_alias",
            )
            entries = [{'kind': 'fhir_artifacts', 'path': str(target), 'systems': [system], 'alias': alias}
                       for alias in ('first', 'second')]
            for strategy, expected in [('fallback', 1), ('all', 2)]:
                routed = TerminologyRouter(entries, strategy=strategy)
                try:
                    result = routed.check(system, 'personal-best', '1.0.2')
                    self.assertEqual(len(result['attempts']), expected)
                    self.assertTrue(all(a['metadata']['artifact']['source_sha256'] for a in result['attempts']))
                    self.assertEqual(routed.check('http://loinc.org', '718-7')['reason'], 'no_enabled_source_for_system')
                finally:
                    routed.close()
            repeated = install_package("de.gematik.hddt.terminology", "1.0.0", target)
            self.assertFalse(repeated["downloaded"])
            artifact = Path(result["source"])
            artifact.write_text(artifact.read_text() + "\n")  # Same real content; changed bytes.
            with self.assertRaisesRegex(ValueError, "content changed"):
                install_package("de.gematik.hddt.terminology", "1.0.0", target)

    def test_real_semlook_name_candidates_are_not_validation(self):
        source = OlsNameSource("https://semanticlookup.zbmed.de/ols/api", ontology="loinc", limit=5)
        candidates = source.search_names("hemoglobin")
        self.assertTrue(candidates)
        self.assertTrue(
            all(
                row["state"] == "candidate" and row["requires_code_and_context_validation"]
                for row in candidates
            )
        )
        self.assertTrue(all(row["iri"].startswith("https://loinc.org/rdf/") for row in candidates))
        self.assertTrue(
            all(
                row["system"] == "http://loinc.org" and len(row["response_sha256"]) == 64
                for row in candidates
            )
        )
        self.assertEqual(source.check("http://loinc.org", "718-7")["state"], "unknown")


@unittest.skipUnless(
    os.getenv("SQC_LIVE_TESTS") == "1" and os.getenv("SQC_BFARM_ACCEPT_DOWNLOAD_CONDITIONS") == "1",
    "Protected BfArM downloads require explicit acceptance of the linked conditions",
)
class ProtectedTranslationTests(unittest.TestCase):
    def test_real_ucum_supplement_is_label_evidence_only(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "ucum-de"
            install_package("bfarm.terminologien.ucum", "1.5.0", target, accept_download_conditions=True)
            source = FhirArtifactSource(target)
            base = "http://unitsofmeasure.org"
            self.assertTrue(source.supplements)
            self.assertEqual(source.check(base, "g/dL")["state"], "unknown")
            self.assertTrue(source.labels(base, "g/dL"))
            label = source.labels(base, "g/dL")[0]["value"]
            self.assertTrue(
                any(
                    row["code"] == "g/dL" and row["state"] == "candidate"
                    for row in source.search_names(label)
                )
            )
            self.assertTrue(any(row.get("supplement_version") for row in source.labels(base, "g/dL")))
            receipt = json.loads((target / "installation.json").read_text())
            self.assertNotIn("token", receipt)


@unittest.skipUnless(
    os.getenv("SQC_WHO_LIVE_TESTS") == "1"
    and os.getenv("WHO_ICD_CLIENT_ID")
    and os.getenv("WHO_ICD_CLIENT_SECRET"),
    "WHO live lookup requires explicit opt-in and real OAuth credentials",
)
class WhoTerminologyTests(unittest.TestCase):
    def test_exact_who_mms_stem_and_version(self):
        source = WhoIcdSource(
            "https://id.who.int",
            release="2026-01",
            language="en",
            token_endpoint="https://icdaccessmanagement.who.int/connect/token",
            client_id=os.environ["WHO_ICD_CLIENT_ID"],
            client_secret=os.environ["WHO_ICD_CLIENT_SECRET"],
        )
        result = source.check(ICD11_MMS, "1A00", "2026-01")
        self.assertEqual(result["state"], "valid")
        self.assertEqual(result["metadata"]["codeinfo"]["code"], "1A00")
        self.assertEqual(result["metadata"]["version"], "2026-01")
        self.assertEqual(source.check(ICD11_MMS, "1A00", "2025-01")["state"], "unknown")
        self.assertEqual(source.check(ICD11_MMS, "1A00&XH8JXX")["state"], "unknown")

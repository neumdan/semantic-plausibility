"""Console progress contains phase names/counts, never records or credentials."""

import logging
import sys
import time
from contextlib import contextmanager
from threading import Event, Thread

LOGGER = logging.getLogger("semantic_plausibility")
LOGGER.addHandler(logging.NullHandler())
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@contextmanager
def activity(label):
    """Explain slow calls without streaming prompts, measurements or credentials."""
    started, done = time.monotonic(), Event()
    LOGGER.info("%s: started", label)
    def heartbeat():
        while not done.wait(15):
            LOGGER.info("%s: still working (%.0f s elapsed)", label, time.monotonic() - started)
    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()
        LOGGER.info("%s: finished in %.1f s; acceptance is recorded separately", label, time.monotonic() - started)


def configure(level="INFO"):
    if level not in LEVELS:
        raise ValueError("log level must be DEBUG, INFO, WARNING or ERROR")
    for handler in list(LOGGER.handlers):
        if getattr(handler, "_plausibility_console", False):
            LOGGER.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler._plausibility_console = True
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(level)
    LOGGER.propagate = False


def phase_event(phase, details):
    # All other details remain in the protected audit, regardless of log level.
    counts = {
        k: v
        for k, v in details.items()
        if k
        in {
            "input_records",
            "individual_records",
            "distinct_records",
            "semantic_items_seen",
            "known_source_occurrences",
            "unknown_frequency_records",
            "output_rows",
            "groups",
            "assessed_groups",
            "skipped_resources",
        }
        and type(v) is int
    }
    level = logging.ERROR if phase == "failed" else logging.INFO
    labels = {
        "started": "Run started; reading configuration and selected source",
        "reference_artifacts": "Target profile and terminology artifacts loaded",
        "input_preflight": "Input columns and source contract checked",
        "fhir_resources_validated": "Full FHIR validation completed; outcomes retained in the report",
        "grouped": "Input read and semantic grouping completed",
        "check_progress": "Terminology and plausibility checks in progress",
        "completed": "Reports written; audit and artifact hashes recorded",
        "failed": "Run failed; inspect the protected audit log and the reported setup error",
    }
    count_text = "; ".join(f"{key.replace('_', ' ')}={value}" for key, value in counts.items())
    LOGGER.log(level, "%s%s", labels.get(phase, phase.replace("_", " ")),
               "; " + count_text if count_text else "")

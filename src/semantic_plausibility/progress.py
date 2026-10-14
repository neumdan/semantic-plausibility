"""Console progress contains phase names/counts, never records or credentials."""

import logging
import sys

LOGGER = logging.getLogger("semantic_plausibility")
LOGGER.addHandler(logging.NullHandler())
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


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
    LOGGER.log(level, "%s%s", phase, " " + str(counts) if counts else "")

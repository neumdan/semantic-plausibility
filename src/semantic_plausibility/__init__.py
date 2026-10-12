"""Local laboratory export review: read-only inputs and auditable proposals."""
from ._version import __version__
from .batch import BatchResult, run_batch, run_plausibility_check
from .study_compare import compare_reports, verify_audit
from .terminology_lookup import lookup_terminology


def research_evidence(config_path, report=None, output=None):
    from .evidence_research import research_evidence as run
    return run(config_path, report, output)


def research_plan(config_path, report=None):
    from .evidence_research import research_plan as plan
    return plan(config_path, report)


def import_reviews(report, reviewed, registry, config_path):
    from .review_feedback import import_reviews as run
    return run(report, reviewed, registry, config_path)

__all__ = ["BatchResult", "run_batch", "run_plausibility_check", "research_evidence", "research_plan",
           "import_reviews", "compare_reports", "verify_audit", "lookup_terminology", "__version__"]

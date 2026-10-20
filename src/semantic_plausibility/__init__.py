"""Local laboratory and medication export review: read-only inputs and auditable proposals."""
from ._version import __version__
from .batch import BatchResult, run_batch, run_plausibility_check
from .study_compare import compare_reports, verify_audit
from .terminology_lookup import lookup_terminology
from .fhir_validation import validate_fhir
from .laboratory_assessment import assessment_layers
from .validation_study import prepare_study_review, evaluate_study_review


def research_evidence(config_path, report=None, output=None, *, scope=None):
    from .evidence_research import research_evidence as run
    return run(config_path, report, output, scope=scope)


def research_plan(config_path, report=None, *, scope=None):
    from .evidence_research import research_plan as plan
    return plan(config_path, report, scope=scope)


def import_reviews(report, reviewed, registry, config_path):
    from .review_feedback import import_reviews as run
    return run(report, reviewed, registry, config_path)

__all__ = ["BatchResult", "run_batch", "run_plausibility_check", "research_evidence", "research_plan",
           "import_reviews", "compare_reports", "verify_audit", "lookup_terminology", "validate_fhir", "assessment_layers",
           "prepare_study_review", "evaluate_study_review", "__version__"]

"""Historical entry point: fixed counterexamples now run as ordinary regressions."""
from tests.test_audit_regressions import (
    test_worker_survives_transient_claim_failure,
    test_bad_clock_does_not_suppress_new_boot_anomalies,
    test_worker_rejects_unsupported_workflow_version,
)

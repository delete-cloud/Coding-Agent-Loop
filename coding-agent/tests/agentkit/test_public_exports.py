"""Tests for stable agentkit package-level exports."""

import agentkit
from agentkit.storage import ArtifactStore


def test_agentkit_exports_result_models_and_reducers() -> None:
    assert agentkit.ArtifactRef.__name__ == "ArtifactRef"
    assert agentkit.ResultRef.__name__ == "ResultRef"
    assert agentkit.TurnResult.__name__ == "TurnResult"
    assert agentkit.SessionResult.__name__ == "SessionResult"
    assert agentkit.VerificationSummary.__name__ == "VerificationSummary"
    assert agentkit.FailureSummary.__name__ == "FailureSummary"
    assert callable(agentkit.result_from_turn_trace)
    assert callable(agentkit.verification_summary_from_turn_trace)


def test_agentkit_storage_exports_artifact_store_protocol() -> None:
    assert ArtifactStore.__name__ == "ArtifactStore"

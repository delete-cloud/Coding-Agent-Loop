"""Tests for stable agentkit package-level exports."""

import agentkit
import agentkit.runtime as runtime
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


def test_agentkit_runtime_exports_exclude_pipeline_and_pipeline_context() -> None:
    assert not hasattr(runtime, "Pipeline")
    assert not hasattr(runtime, "PipelineContext")
    assert "Pipeline" not in runtime.__all__
    assert "PipelineContext" not in runtime.__all__


def test_agentkit_public_exports_exclude_pipeline_and_pipeline_context() -> None:
    assert not hasattr(agentkit, "Pipeline")
    assert not hasattr(agentkit, "PipelineContext")
    assert "Pipeline" not in agentkit.__all__
    assert "PipelineContext" not in agentkit.__all__


def test_phase_c_adapter_hides_pipeline_context_from_public_api() -> None:
    from agentkit.runtime.pipeline import Pipeline, PipelineContext

    assert Pipeline.__name__ == "Pipeline"
    assert PipelineContext.__name__ == "PipelineContext"
    assert not hasattr(runtime, "PipelineContext")
    assert not hasattr(agentkit, "PipelineContext")


def test_runtime_exports_every_transitive_phase_c_protocol_type() -> None:
    required = {
        "AgentEngine",
        "SegmentCoordinator",
        "EngineStepRequest",
        "EngineStepInput",
        "Initial",
        "RuntimeCommand",
        "ModelGenerationCompleted",
        "EffectSettled",
        "ApprovalResolved",
        "TransitionProposal",
        "NextAction",
        "ModelGenerationAction",
        "PreparedEffectAction",
        "TerminalAction",
        "BlockedAction",
        "SafeYieldAction",
        "RunSegmentRequest",
        "SegmentOutcome",
        "CompletedOutcome",
        "BlockedOutcome",
        "SafeYieldOutcome",
        "CancelledOutcome",
        "RoundLimitOutcome",
        "FailedOutcome",
        "ModelRequest",
        "ModelGenerationResult",
        "ModelToolCall",
        "ModelUsage",
        "ProviderStopMetadata",
        "StreamFrame",
        "CancellationToken",
        "OperationStateVersion",
        "EffectPlan",
        "EffectSettlement",
        "ApprovalSettlement",
        "DispatchPermit",
        "ReconciliationRecord",
        "CommandDisposition",
        "CommitTransitionRequest",
        "CommitTransitionResult",
        "DispatchAuthorizationRequest",
        "DispatchAuthorizationResult",
        "CommitSettlementRequest",
        "CommitSettlementResult",
        "CommitReconciliationRequest",
        "CommitReconciliationResult",
        "ControlSnapshot",
        "ControlGeneration",
        "CommittedFactSink",
        "CommittedFactNotice",
        "ModelAdapter",
        "CommitPort",
        "EffectExecutor",
        "ControlProbe",
        "FrameSink",
    }

    assert required <= set(runtime.__all__)
    assert all(hasattr(runtime, name) for name in required)

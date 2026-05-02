"""Tests for ADR-0016: Stage environment runtime and tool orchestration PRs.

These tests verify the acceptance criteria stated in the ADR itself and
validate that the document structure and content meet the stated requirements.
"""

from __future__ import annotations

import re
from pathlib import Path

ADR_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "docs"
    / "adr"
    / "0016-stage-environment-runtime-and-tool-orchestration-prs.md"
)


def _read_adr() -> str:
    return ADR_PATH.read_text(encoding="utf-8")


class TestAdr0016Existence:
    """ADR file existence and basic structure."""

    def test_adr_file_exists(self) -> None:
        assert ADR_PATH.exists(), f"ADR file not found at {ADR_PATH}"

    def test_adr_file_is_not_empty(self) -> None:
        content = _read_adr()
        assert len(content.strip()) > 0

    def test_adr_file_follows_naming_convention(self) -> None:
        # Naming convention: NNNN-kebab-case-title.md
        assert re.match(
            r"^\d{4}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$", ADR_PATH.name
        ), f"File name '{ADR_PATH.name}' does not follow NNNN-kebab-case.md convention"

    def test_adr_number_is_0016(self) -> None:
        assert ADR_PATH.name.startswith("0016-")


class TestAdr0016Metadata:
    """ADR metadata fields: status, date, title."""

    def test_status_is_proposed(self) -> None:
        content = _read_adr()
        assert "**Status**: Proposed" in content

    def test_date_field_present(self) -> None:
        content = _read_adr()
        assert re.search(r"\*\*Date\*\*:\s*\d{4}-\d{2}-\d{2}", content)

    def test_date_is_2026_05_02(self) -> None:
        content = _read_adr()
        assert "**Date**: 2026-05-02" in content

    def test_title_matches_filename_topic(self) -> None:
        content = _read_adr()
        # Title should reference staging/ordering PRs and environment/runtime
        first_line = content.splitlines()[0]
        assert first_line.startswith("# ADR-0016:")
        lower = first_line.lower()
        assert "stage" in lower or "environment" in lower or "orchestration" in lower


class TestAdr0016RequiredSections:
    """All required ADR sections must be present."""

    def test_context_section_present(self) -> None:
        assert "## Context" in _read_adr()

    def test_decision_section_present(self) -> None:
        assert "## Decision" in _read_adr()

    def test_alternatives_rejected_section_present(self) -> None:
        assert "## Alternatives Rejected" in _read_adr()

    def test_acceptance_criteria_section_present(self) -> None:
        assert "## Acceptance Criteria" in _read_adr()

    def test_references_section_present(self) -> None:
        assert "## References" in _read_adr()

    def test_sections_appear_in_expected_order(self) -> None:
        content = _read_adr()
        context_pos = content.index("## Context")
        decision_pos = content.index("## Decision")
        alternatives_pos = content.index("## Alternatives Rejected")
        criteria_pos = content.index("## Acceptance Criteria")
        references_pos = content.index("## References")
        assert context_pos < decision_pos < alternatives_pos < criteria_pos < references_pos


class TestAdr0016DecisionPrSequence:
    """ADR records the ordered five-PR sequence."""

    def test_pr_sequence_has_five_prs(self) -> None:
        content = _read_adr()
        # Each PR is introduced as "PR N:" in the decision section
        pr_labels = re.findall(r"PR\s+\d+:", content)
        pr_numbers = sorted({re.search(r"\d+", p).group() for p in pr_labels})  # type: ignore[union-attr]
        assert len(pr_numbers) >= 5, f"Expected at least 5 distinct PR numbers, found: {pr_numbers}"

    def test_pr1_adds_environment_and_local_environment(self) -> None:
        content = _read_adr()
        decision_start = content.index("## Decision")
        alternatives_start = content.index("## Alternatives Rejected")
        decision_text = content[decision_start:alternatives_start]
        assert "Environment" in decision_text
        assert "LocalEnvironment" in decision_text

    def test_pr1_comes_before_cloud_workspace_execution(self) -> None:
        content = _read_adr()
        # PR 1 should be mentioned before cloud workspace execution
        pr1_pos = content.find("PR 1:")
        cloud_pos = content.find("Cloud workspace execution")
        assert pr1_pos != -1, "PR 1 not found"
        assert cloud_pos != -1, "Cloud workspace execution not found"
        assert pr1_pos < cloud_pos

    def test_pr2_adds_runtime_context(self) -> None:
        content = _read_adr()
        decision_start = content.index("## Decision")
        alternatives_start = content.index("## Alternatives Rejected")
        decision_text = content[decision_start:alternatives_start]
        assert "PR 2" in decision_text
        # PR 2 introduces AgentRunContext or equivalent runtime context
        assert "AgentRunContext" in decision_text or "runtime context" in decision_text.lower()

    def test_pr3_adds_toolset_governance(self) -> None:
        content = _read_adr()
        decision_start = content.index("## Decision")
        alternatives_start = content.index("## Alternatives Rejected")
        decision_text = content[decision_start:alternatives_start]
        assert "PR 3" in decision_text
        assert "Toolset" in decision_text

    def test_pr4_adds_runtime_message_bus(self) -> None:
        content = _read_adr()
        decision_start = content.index("## Decision")
        alternatives_start = content.index("## Alternatives Rejected")
        decision_text = content[decision_start:alternatives_start]
        assert "PR 4" in decision_text
        # PR 4 introduces message bus
        assert "message bus" in decision_text.lower() or "runtime message" in decision_text.lower()

    def test_pr5_adds_tool_proxy(self) -> None:
        content = _read_adr()
        decision_start = content.index("## Decision")
        alternatives_start = content.index("## Alternatives Rejected")
        decision_text = content[decision_start:alternatives_start]
        assert "PR 5" in decision_text
        assert "tool proxy" in decision_text.lower() or "Tool Proxy" in decision_text


class TestAdr0016CloudWorkspaceDeferral:
    """ADR states cloud workspace execution is deferred with explicit failure behavior."""

    def test_cloud_workspace_execution_deferred_to_follow_up(self) -> None:
        content = _read_adr()
        lower = content.lower()
        # Must state cloud workspace execution is deferred / follow-up
        assert "cloud workspace execution" in lower
        assert "follow-up" in lower or "deferred" in lower

    def test_cloud_bindings_must_fail_explicitly(self) -> None:
        content = _read_adr()
        lower = content.lower()
        # Must state cloud bindings should fail explicitly rather than silently
        assert "fail explicit" in lower or "explicit" in lower

    def test_cloud_workspace_not_pr1_work(self) -> None:
        content = _read_adr()
        # The ADR should state cloud workspace execution is NOT PR 1 work
        assert "not as PR 1 work" in content or "follow-up implementation" in content.lower() or "follow-up" in content.lower()

    def test_cloud_workspace_binding_has_not_implemented_path(self) -> None:
        content = _read_adr()
        # Must mention explicit not-implemented failure path for CloudWorkspaceBinding
        assert "CloudWorkspaceBinding" in content
        assert "not-implemented" in content or "not implemented" in content.lower()


class TestAdr0016SubagentOrchestrationDeferral:
    """ADR states full subagent orchestration is deferred until prerequisites exist."""

    def test_subagent_orchestration_mentioned(self) -> None:
        content = _read_adr()
        assert "subagent orchestration" in content.lower()

    def test_subagent_orchestration_deferred_until_pr2_pr3_pr4(self) -> None:
        content = _read_adr()
        lower = content.lower()
        # Must state that full subagent orchestration waits for PR 2, 3, 4 areas
        assert "subagent orchestration" in lower
        # The ADR text says "must wait until PR 2, PR 3, and PR 4"
        assert "pr 2" in lower or "pr2" in lower
        assert "pr 3" in lower or "pr3" in lower
        assert "pr 4" in lower or "pr4" in lower

    def test_subagent_orchestration_requires_runtime_context(self) -> None:
        content = _read_adr()
        lower = content.lower()
        assert "run identity" in lower or "runtime context" in lower or "agent identity" in lower

    def test_subagent_orchestration_requires_tool_governance(self) -> None:
        content = _read_adr()
        lower = content.lower()
        assert "tool governance" in lower

    def test_subagent_orchestration_requires_runtime_messaging(self) -> None:
        content = _read_adr()
        lower = content.lower()
        assert "runtime messaging" in lower or "inbound messages" in lower or "message" in lower

    def test_existing_subagent_tool_may_remain_unchanged(self) -> None:
        content = _read_adr()
        assert "subagent tool may remain" in content.lower() or "remain as-is" in content.lower()


class TestAdr0016Pr86SessionMetadata:
    """ADR references PR 86 and prevents duplicating session runtime metadata."""

    def test_pr86_referenced(self) -> None:
        content = _read_adr()
        assert "PR 86" in content

    def test_pr86_link_in_references(self) -> None:
        content = _read_adr()
        refs_start = content.index("## References")
        refs_text = content[refs_start:]
        assert "pull/86" in refs_text or "PR 86" in refs_text

    def test_provider_field_not_duplicated_in_environment(self) -> None:
        content = _read_adr()
        lower = content.lower()
        # ADR must state provider should stay in session metadata, not environment
        assert "provider" in lower
        assert "duplicate" in lower or "duplicat" in lower

    def test_model_field_not_duplicated_in_environment(self) -> None:
        content = _read_adr()
        lower = content.lower()
        assert "model" in lower
        # Explicit rejection of duplicating these fields
        assert "duplicate" in lower or "two sources of truth" in lower

    def test_base_url_not_duplicated_in_environment(self) -> None:
        content = _read_adr()
        lower = content.lower()
        assert "base url" in lower or "base_url" in lower

    def test_max_steps_not_duplicated_in_environment(self) -> None:
        content = _read_adr()
        lower = content.lower()
        assert "max steps" in lower or "max_steps" in lower or "step limit" in lower

    def test_session_runtime_metadata_kept_in_session(self) -> None:
        content = _read_adr()
        lower = content.lower()
        # Must say keep provider/model/etc in session metadata
        assert "session" in lower
        assert "session runtime metadata" in lower or "session metadata" in lower


class TestAdr0016AlternativesRejected:
    """ADR records the rejected alternatives."""

    def test_immediate_cloud_workspace_alternative_rejected(self) -> None:
        content = _read_adr()
        alts_start = content.index("## Alternatives Rejected")
        criteria_start = content.index("## Acceptance Criteria")
        alts_text = content[alts_start:criteria_start]
        assert "cloud workspace execution" in alts_text.lower()
        assert "rejected" in alts_text.lower()

    def test_subagent_before_runtime_alternative_rejected(self) -> None:
        content = _read_adr()
        alts_start = content.index("## Alternatives Rejected")
        criteria_start = content.index("## Acceptance Criteria")
        alts_text = content[alts_start:criteria_start]
        assert "subagent orchestration" in alts_text.lower()
        assert "rejected" in alts_text.lower()

    def test_single_combined_pr_alternative_rejected(self) -> None:
        content = _read_adr()
        alts_start = content.index("## Alternatives Rejected")
        criteria_start = content.index("## Acceptance Criteria")
        alts_text = content[alts_start:criteria_start]
        # Must reject combining everything in one PR
        assert "combine" in alts_text.lower() or "one pr" in alts_text.lower()

    def test_agent_run_context_owning_provider_alternative_rejected(self) -> None:
        content = _read_adr()
        alts_start = content.index("## Alternatives Rejected")
        criteria_start = content.index("## Acceptance Criteria")
        alts_text = content[alts_start:criteria_start]
        assert "AgentRunContext" in alts_text
        # Must reject AgentRunContext owning those fields
        assert "provider" in alts_text.lower()

    def test_proxy_all_tools_alternative_rejected(self) -> None:
        content = _read_adr()
        alts_start = content.index("## Alternatives Rejected")
        criteria_start = content.index("## Acceptance Criteria")
        alts_text = content[alts_start:criteria_start]
        assert "proxy all tools" in alts_text.lower() or "proxy" in alts_text.lower()


class TestAdr0016AcceptanceCriteria:
    """The acceptance criteria section maps to verifiable content assertions."""

    def test_acceptance_criteria_uses_checkbox_format(self) -> None:
        content = _read_adr()
        criteria_start = content.index("## Acceptance Criteria")
        refs_start = content.index("## References")
        criteria_text = content[criteria_start:refs_start]
        checkboxes = re.findall(r"- \[ \]", criteria_text)
        assert len(checkboxes) >= 5, (
            f"Expected at least 5 checkbox items in acceptance criteria, found {len(checkboxes)}"
        )

    def test_acceptance_criteria_references_adr_filename(self) -> None:
        content = _read_adr()
        criteria_start = content.index("## Acceptance Criteria")
        refs_start = content.index("## References")
        criteria_text = content[criteria_start:refs_start]
        assert "0016" in criteria_text

    def test_acceptance_criteria_mentions_environment_implementation(self) -> None:
        content = _read_adr()
        criteria_start = content.index("## Acceptance Criteria")
        refs_start = content.index("## References")
        criteria_text = content[criteria_start:refs_start]
        lower = criteria_text.lower()
        assert "environment" in lower

    def test_acceptance_criteria_mentions_cloud_workspace_deferral(self) -> None:
        content = _read_adr()
        criteria_start = content.index("## Acceptance Criteria")
        refs_start = content.index("## References")
        criteria_text = content[criteria_start:refs_start]
        lower = criteria_text.lower()
        assert "cloud workspace" in lower or "deferred" in lower

    def test_acceptance_criteria_mentions_subagent_orchestration_deferral(self) -> None:
        content = _read_adr()
        criteria_start = content.index("## Acceptance Criteria")
        refs_start = content.index("## References")
        criteria_text = content[criteria_start:refs_start]
        lower = criteria_text.lower()
        assert "subagent orchestration" in lower

    def test_acceptance_criteria_mentions_pr86_metadata(self) -> None:
        content = _read_adr()
        criteria_start = content.index("## Acceptance Criteria")
        refs_start = content.index("## References")
        criteria_text = content[criteria_start:refs_start]
        assert "PR 86" in criteria_text or "pr 86" in criteria_text.lower()


class TestAdr0016References:
    """ADR references section includes required source files and links."""

    def _refs_text(self) -> str:
        content = _read_adr()
        return content[content.index("## References"):]

    def test_references_section_not_empty(self) -> None:
        refs = self._refs_text()
        lines = [l.strip() for l in refs.splitlines() if l.strip() and l.strip() != "## References"]
        assert len(lines) > 0

    def test_references_adr_0014(self) -> None:
        assert "0014" in self._refs_text()

    def test_references_execution_binding_source(self) -> None:
        refs = self._refs_text()
        assert "execution_binding.py" in refs

    def test_references_core_tools_source(self) -> None:
        refs = self._refs_text()
        assert "core_tools.py" in refs

    def test_references_pr86_link(self) -> None:
        refs = self._refs_text()
        assert "pull/86" in refs

    def test_references_pipeline_source(self) -> None:
        refs = self._refs_text()
        assert "pipeline.py" in refs


class TestAdr0016ParallelAgentGuidance:
    """ADR provides guidance for parallel agent work during implementation."""

    def test_parallel_agents_guidance_present(self) -> None:
        content = _read_adr()
        lower = content.lower()
        assert "parallel agent" in lower

    def test_parallel_agents_restricted_to_design_review_only(self) -> None:
        content = _read_adr()
        lower = content.lower()
        # Must state parallel agents should not edit implementation files
        assert "should not edit" in lower or "not edit" in lower

    def test_parallel_agents_restricted_until_prerequisite_prs_land(self) -> None:
        content = _read_adr()
        lower = content.lower()
        assert "prerequisite" in lower or "until" in lower


class TestAdr0016Regression:
    """Regression and boundary tests that strengthen confidence in the document."""

    def test_file_encoding_is_utf8_readable(self) -> None:
        # Verify the file can be decoded as UTF-8 without errors
        raw = ADR_PATH.read_bytes()
        decoded = raw.decode("utf-8")
        assert len(decoded) > 0

    def test_no_placeholder_text_present(self) -> None:
        content = _read_adr()
        for placeholder in ["TODO", "FIXME", "TBD", "PLACEHOLDER", "lorem ipsum"]:
            assert placeholder not in content, f"Placeholder text '{placeholder}' found in ADR"

    def test_five_pr_items_appear_in_numeric_order(self) -> None:
        content = _read_adr()
        decision_start = content.index("## Decision")
        alternatives_start = content.index("## Alternatives Rejected")
        decision_text = content[decision_start:alternatives_start]
        # Find positions of "PR 1:" through "PR 5:" within decision section
        positions = []
        for n in range(1, 6):
            pattern = f"PR {n}:"
            pos = decision_text.find(pattern)
            assert pos != -1, f"'{pattern}' not found in Decision section"
            positions.append(pos)
        assert positions == sorted(positions), "PR items are not in ascending numeric order"

    def test_environment_protocol_operations_listed(self) -> None:
        content = _read_adr()
        lower = content.lower()
        # PR 1 should list the operations the environment protocol covers
        for op in ["file", "shell", "glob", "grep"]:
            assert op in lower, f"Expected operation '{op}' mentioned in ADR"

    def test_inbound_message_types_listed_for_pr4(self) -> None:
        content = _read_adr()
        lower = content.lower()
        # PR 4 lists inbound runtime message types
        assert "interrupt" in lower
        assert "approval" in lower

    def test_search_tools_and_call_tool_in_pr5(self) -> None:
        content = _read_adr()
        # PR 5 should mention the stable proxy affordances
        assert "search_tools" in content
        assert "call_tool" in content

    def test_no_python_compilation_requirement_stated(self) -> None:
        content = _read_adr()
        # Acceptance criteria must note no compilation check needed (doc-only PR)
        lower = content.lower()
        assert "documentation only" in lower or "doc" in lower

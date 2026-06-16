from __future__ import annotations

import pytest
from pydantic import ValidationError

from coding_agent.server.schemas import RuntimeConfigUpdateRequest


def test_omitted_fields_leave_model_fields_set_empty() -> None:
    request = RuntimeConfigUpdateRequest.model_validate({})
    assert request.model_fields_set == set()


def test_explicit_null_base_url_means_reset() -> None:
    request = RuntimeConfigUpdateRequest.model_validate({"base_url": None})
    assert request.base_url is None
    assert request.model_fields_set == {"base_url"}


@pytest.mark.parametrize("field", ["model", "provider", "thinking", "approval"])
def test_explicit_null_rejected_for_non_resettable_fields(field: str) -> None:
    with pytest.raises(ValidationError, match=f"{field} may not be null"):
        RuntimeConfigUpdateRequest.model_validate({field: None})


def test_value_fields_still_accepted() -> None:
    request = RuntimeConfigUpdateRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "provider": "anthropic",
            "base_url": "https://api.example.com",
            "thinking": {"enabled": True, "effort": "high"},
            "approval": "interactive",
        }
    )
    assert request.model == "claude-sonnet-4-6"
    assert request.provider == "anthropic"
    assert request.base_url == "https://api.example.com"
    assert request.thinking is not None
    assert request.thinking.effort == "high"
    assert request.approval == "interactive"
    assert request.model_fields_set == {
        "model",
        "provider",
        "base_url",
        "thinking",
        "approval",
    }

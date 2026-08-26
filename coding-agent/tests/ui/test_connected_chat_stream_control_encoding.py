from __future__ import annotations

import json

from coding_agent.events.connected_chat import CONNECTED_CHAT_CONTRACT_VERSION
from coding_agent.server.http.events import StreamControl, _stream_control_sse_frame


def test_stream_control_sse_frame_includes_contract_version() -> None:
    frame = _stream_control_sse_frame(
        StreamControl(
            kind="replay_required",
            reason="sequence_loss",
            cursor="safe-cursor",
        )
    )

    assert frame["event"] == "stream_control"
    assert json.loads(frame["data"]) == {
        "contract_version": CONNECTED_CHAT_CONTRACT_VERSION,
        "kind": "replay_required",
        "reason": "sequence_loss",
        "cursor": "safe-cursor",
    }

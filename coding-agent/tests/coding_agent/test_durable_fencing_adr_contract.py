from __future__ import annotations

from pathlib import Path


ADR_DIR = Path(__file__).resolve().parents[2] / "docs" / "adr"
ADR_0067 = ADR_DIR / "0067-local-sqlite-durable-tape-runtime.md"
ADR_0068 = ADR_DIR / "0068-local-sqlite-transactional-durable-fencing.md"


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing ADR file: {path}")
    return path.read_text(encoding="utf-8")


def _contains(text: str, phrase: str) -> bool:
    return phrase.lower() in text.lower()


def _section(text: str, heading: str) -> str:
    heading_line = next(
        candidate
        for candidate in (f"### {heading}", f"## {heading}")
        if candidate in text
    )
    start = text.index(heading_line)
    heading_level = heading_line.split(" ", 1)[0]
    next_heading = text.find(f"\n{heading_level} ", start + len(heading_line))
    if next_heading == -1:
        next_heading = len(text)
    return " ".join(text[start:next_heading].split())


def test_adr_0068_supersedes_adr_0067_without_conflicting_target_architecture() -> None:
    old = _read(ADR_0067)
    new = _read(ADR_0068)

    assert "**Status**: Superseded" in old
    assert "Superseded by ADR-0068" in old
    assert "**Status**: Proposed" in new
    assert "Supersedes ADR-0067" in new

    stale_target_phrases = [
        "filesystem session metadata for local session records unless a later ADR",
        "Mutation paths that already call `SessionManager._assert_owner` continue",
    ]
    for phrase in stale_target_phrases:
        assert phrase not in new


def test_adr_0068_defines_single_db_epoch_and_transactional_fencing_contract() -> None:
    text = _read(ADR_0068)
    normalized = " ".join(text.split())
    decision = _section(text, "Local layout")
    owner_epoch = _section(text, "Owner epoch")
    context = _section(text, "Context")

    required_phrases = [
        "single local SQLite database",
        "`local.sqlite3`",
        "DB-managed owner epoch",
        "renew does not advance the epoch",
        "takeover advances the epoch",
        "owner epoch check",
        "target ownership check",
        "protected mutation",
        "same transaction",
        "best-effort preflight",
        "does not provide durable fencing",
    ]
    for phrase in required_phrases:
        assert _contains(normalized, phrase)

    assert _contains(decision, "single local SQLite database")
    assert _contains(owner_epoch, "Renew does not advance the epoch")
    assert _contains(owner_epoch, "Takeover advances the epoch")
    assert _contains(
        normalized,
        "owner epoch check, target ownership check, and protected mutation "
        "happen in the same transaction",
    )
    assert _contains(
        context,
        "cross-database owner preflight check can reject many stale operations, "
        "but it has a time-of-check/time-of-use gap",
    )
    assert _contains(
        normalized,
        "Best-effort preflight does not provide durable fencing and must not be "
        "documented as preventing stale writes after owner loss",
    )


def test_adr_0068_rejects_attach_as_the_durable_fencing_foundation() -> None:
    alternatives = _section(_read(ADR_0068), "Alternatives Rejected")

    assert _contains(
        alternatives,
        "Use SQLite `ATTACH` as the durable fencing foundation",
    )
    assert _contains(alternatives, "rejected")
    assert _contains(alternatives, "journal mode is not WAL")
    assert _contains(alternatives, "Current stores use independent connections")
    assert _contains(
        alternatives,
        "Single local database is simpler and gives clearer atomicity",
    )


def test_adr_0068_requires_same_session_target_ownership_for_protected_writes() -> None:
    text = _read(ADR_0068)
    target_ownership = _section(text, "Target ownership")

    required_phrases = [
        "target being mutated belongs to the same session",
        "`agent_runs.run_id -> session_id`",
        "interaction",
        "snapshot",
        "event",
        "stable tape",
        "checkpoint_id",
        "must not rebind",
        "session A cannot mutate",
        "session B",
    ]
    for phrase in required_phrases:
        assert _contains(target_ownership, phrase)

    mismatch_contract = (
        "owner of session A cannot mutate a run, checkpoint, tape, interaction, "
        "snapshot, or event target that belongs to session B"
    )
    assert _contains(target_ownership, mismatch_contract)


def test_adr_0068_covers_worker_maintenance_and_cleanup_authority() -> None:
    text = _read(ADR_0068)
    authority_modes = _section(text, "Authority modes")
    cleanup = _section(text, "Error and cleanup semantics")
    alternatives = _section(text, "Alternatives Rejected")

    authority_phrases = [
        "worker authority",
        "claim token",
        "subordinate to session takeover",
        "heartbeat",
        "finalize",
        "maintenance authority",
        "maintenance lock",
        "repair/audit event",
        "offline migration",
        "exclusive file lock",
    ]
    for phrase in authority_phrases:
        assert _contains(authority_modes, phrase)

    cleanup_phrases = [
        "old owner",
        "cannot write cancelled",
        "cannot write failed",
        "cannot write cancelled, failed, result, or session metadata terminal state",
    ]
    for phrase in cleanup_phrases:
        assert _contains(cleanup, phrase)

    assert _contains(
        alternatives,
        "Let workers bypass session ownership with claim tokens alone",
    )
    assert _contains(
        alternatives,
        "Add `skip_fencing=True` for repair",
    )


def test_adr_0068_records_full_write_path_inventory_and_future_pr_sequence() -> None:
    text = _read(ADR_0068)
    inventory = _section(text, "Write-path inventory")
    plan = " ".join(text[text.index("## Implementation Plan") :].split())

    inventory_phrases = [
        "tape save",
        "tape truncate",
        "memory append",
        "memory replace",
        "checkpoint save",
        "checkpoint delete",
        "checkpoint restore",
        "runtime create",
        "runtime update",
        "claim attached worker",
        "claim external worker",
        "worker heartbeat",
        "worker finalize",
        "worker cancel",
        "worker recovery",
        "runtime append event",
        "save message snapshot",
        "create interaction",
        "resolve interaction",
        "session metadata save",
        "session metadata delete",
        "turn state updates",
        "runtime config updates",
        "MCP servers",
        "additional directories",
        "session close",
        "session shutdown",
    ]
    for phrase in inventory_phrases:
        assert _contains(inventory, phrase)

    plan_phrases = [
        "PR 1 is this ADR, the write-path inventory, and contract/design tests only",
        "does not change product defaults, store APIs, or runtime write behavior",
        "PR 2",
        "PR 3",
        "PR 4",
        "PG transaction",
    ]
    for phrase in plan_phrases:
        assert _contains(plan, phrase)

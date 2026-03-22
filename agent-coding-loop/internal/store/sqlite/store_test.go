package sqlite

import (
	"context"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/kina/agent-coding-loop/internal/model"
)

func TestStoreRunLifecycle(t *testing.T) {
	ctx := context.Background()
	dbPath := filepath.Join(t.TempDir(), "state.db")
	s, err := New(dbPath)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := s.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	runID, err := s.CreateRun(ctx, model.RunSpec{Goal: "demo", Repo: "/tmp/repo", PRMode: model.PRModeAuto}, model.RunStatusQueued)
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	if runID == "" {
		t.Fatal("expected run id")
	}

	if err := s.UpdateRunStatus(ctx, runID, model.RunStatusRunning, ""); err != nil {
		t.Fatalf("UpdateRunStatus: %v", err)
	}

	if err := s.InsertStep(ctx, StepRecord{RunID: runID, Iteration: 1, Agent: "coder", Decision: string(model.LoopDecisionContinue), Status: string(model.RunStatusRunning), StartedAt: time.Now().UnixMilli(), EndedAt: time.Now().UnixMilli()}); err != nil {
		t.Fatalf("InsertStep: %v", err)
	}
	if err := s.InsertToolCall(ctx, ToolCallRecord{RunID: runID, Iteration: 1, Tool: "run_command", Input: "go test ./...", Output: "ok", Status: "completed", CreatedAt: time.Now().UnixMilli()}); err != nil {
		t.Fatalf("InsertToolCall: %v", err)
	}
	if err := s.InsertReview(ctx, ReviewRecord{RunID: runID, Iteration: 1, Decision: string(model.ReviewDecisionApprove), Summary: "clean", FindingsJSON: "[]", CreatedAt: time.Now().UnixMilli()}); err != nil {
		t.Fatalf("InsertReview: %v", err)
	}
	if err := s.InsertArtifact(ctx, ArtifactRecord{RunID: runID, Kind: "diff", Path: "a.diff", Content: "patch", CreatedAt: time.Now().UnixMilli()}); err != nil {
		t.Fatalf("InsertArtifact: %v", err)
	}

	run, err := s.GetRun(ctx, runID)
	if err != nil {
		t.Fatalf("GetRun: %v", err)
	}
	if run.Status != string(model.RunStatusRunning) {
		t.Fatalf("expected running, got %s", run.Status)
	}

	events, err := s.GetRunEvents(ctx, runID)
	if err != nil {
		t.Fatalf("GetRunEvents: %v", err)
	}
	if len(events) < 4 {
		t.Fatalf("expected >= 4 events, got %d", len(events))
	}
}

func TestMigrateConfiguresWALAndBusyTimeout(t *testing.T) {
	ctx := context.Background()
	dbPath := filepath.Join(t.TempDir(), "state.db")
	s, err := New(dbPath)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := s.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	journalRows, err := s.query(ctx, "PRAGMA journal_mode;")
	if err != nil {
		t.Fatalf("journal_mode query: %v", err)
	}
	if len(journalRows) == 0 || len(journalRows[0]) == 0 || journalRows[0][0] != "wal" {
		t.Fatalf("expected wal journal mode, got %v", journalRows)
	}
	busyRows, err := s.query(ctx, "PRAGMA busy_timeout;")
	if err != nil {
		t.Fatalf("busy_timeout query: %v", err)
	}
	if len(busyRows) == 0 || len(busyRows[0]) == 0 || parseInt64(busyRows[0][0]) <= 0 {
		t.Fatalf("expected busy_timeout > 0, got %v", busyRows)
	}
}

func TestUpdateRunStatusDerivesFailureReason(t *testing.T) {
	ctx := context.Background()
	dbPath := filepath.Join(t.TempDir(), "state.db")
	s, err := New(dbPath)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := s.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	runID, err := s.CreateRun(ctx, model.RunSpec{Goal: "demo", Repo: "/tmp/repo", PRMode: model.PRModeAuto}, model.RunStatusQueued)
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}

	cases := []struct {
		name    string
		status  model.RunStatus
		summary string
		want    string
	}{
		{name: "patch apply", status: model.RunStatusNeedsChange, summary: "Patch apply failed: conflict", want: "patch_apply"},
		{name: "json parse", status: model.RunStatusFailed, summary: "coder failed: parse llm json failed: invalid character", want: "json_parse"},
		{name: "doom loop", status: model.RunStatusBlocked, summary: "doom-loop detected on run_command", want: "doom_loop"},
		{name: "max iterations", status: model.RunStatusFailed, summary: "max iterations reached before approval", want: "max_iterations"},
		{name: "coder error", status: model.RunStatusFailed, summary: "coder failed: transport offline", want: "coder_error"},
		{name: "reviewer error", status: model.RunStatusFailed, summary: "reviewer failed after refresh: timeout", want: "reviewer_error"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := s.UpdateRunStatus(ctx, runID, tc.status, tc.summary); err != nil {
				t.Fatalf("UpdateRunStatus: %v", err)
			}
			run, err := s.GetRun(ctx, runID)
			if err != nil {
				t.Fatalf("GetRun: %v", err)
			}
			if got := strings.TrimSpace(run.FailureReason); got != tc.want {
				t.Fatalf("failure_reason = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestMigrateAddsFailureReasonColumnToExistingRunsTable(t *testing.T) {
	ctx := context.Background()
	dbPath := filepath.Join(t.TempDir(), "state.db")
	s, err := New(dbPath)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	oldSchema := `
CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  spec_json TEXT NOT NULL,
  status TEXT NOT NULL,
  branch TEXT NOT NULL DEFAULT '',
  commit_hash TEXT NOT NULL DEFAULT '',
  pr_url TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);`
	if _, _, err := s.run(ctx, oldSchema); err != nil {
		t.Fatalf("create old runs table: %v", err)
	}

	if err := s.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	rows, err := s.query(ctx, "PRAGMA table_info(runs);")
	if err != nil {
		t.Fatalf("table_info: %v", err)
	}
	found := false
	for _, row := range rows {
		if len(row) > 1 && row[1] == "failure_reason" {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected failure_reason column after migrate, got %v", rows)
	}
}

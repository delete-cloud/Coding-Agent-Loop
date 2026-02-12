package sqlite

import (
	"context"
	"path/filepath"
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

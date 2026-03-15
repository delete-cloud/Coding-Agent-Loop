package sqlite

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/kina/agent-coding-loop/internal/model"
)

func newProgressTestStore(t *testing.T) (*Store, context.Context, string) {
	t.Helper()

	ctx := context.Background()
	dbPath := filepath.Join(t.TempDir(), "state.db")
	s, err := New(dbPath)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := s.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	runID, err := s.CreateRun(ctx, model.RunSpec{Goal: "demo", Repo: "/tmp/repo", PRMode: model.PRModeDryRun}, model.RunStatusQueued)
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}

	return s, ctx, runID
}

func TestStoreProgressEventsRoundTrip(t *testing.T) {
	s, ctx, runID := newProgressTestStore(t)

	err := s.InsertProgressEvent(ctx, model.ProgressEvent{
		RunID:     runID,
		Iteration: 0,
		EventType: model.ProgressEventRunStarted,
		Status:    model.ProgressStatusStarted,
		Summary:   "run started",
		Detail:    map[string]any{"reason": "fresh_run"},
		CreatedAt: 2000,
	})
	if err != nil {
		t.Fatalf("InsertProgressEvent(first): %v", err)
	}
	err = s.InsertProgressEvent(ctx, model.ProgressEvent{
		RunID:     runID,
		Iteration: 1,
		EventType: model.ProgressEventIterationStarted,
		Status:    model.ProgressStatusStarted,
		Summary:   "iteration started",
		Detail:    map[string]any{"reason": "initial"},
		CreatedAt: 1000,
	})
	if err != nil {
		t.Fatalf("InsertProgressEvent(second): %v", err)
	}

	events, err := s.ListProgressEventsAfter(ctx, runID, 0, 10)
	if err != nil {
		t.Fatalf("ListProgressEventsAfter: %v", err)
	}
	if len(events) != 2 {
		t.Fatalf("expected 2 progress events, got %d", len(events))
	}
	if events[0].Summary != "run started" || events[1].Summary != "iteration started" {
		t.Fatalf("unexpected event order: %#v", events)
	}
	if events[0].ID == 0 || events[1].ID == 0 {
		t.Fatalf("expected database-assigned IDs, got %#v", events)
	}
}

func TestStoreListProgressEventsAfterUsesGlobalIDCursor(t *testing.T) {
	s, ctx, runID := newProgressTestStore(t)

	if err := s.InsertProgressEvent(ctx, model.ProgressEvent{
		RunID:     runID,
		Iteration: 1,
		EventType: model.ProgressEventIterationStarted,
		Status:    model.ProgressStatusStarted,
		Summary:   "first insert",
		CreatedAt: 3000,
	}); err != nil {
		t.Fatalf("InsertProgressEvent(first): %v", err)
	}
	if err := s.InsertProgressEvent(ctx, model.ProgressEvent{
		RunID:     runID,
		Iteration: 1,
		EventType: model.ProgressEventCoderGenerating,
		Status:    model.ProgressStatusStarted,
		Summary:   "second insert older timestamp",
		CreatedAt: 1000,
	}); err != nil {
		t.Fatalf("InsertProgressEvent(second): %v", err)
	}

	all, err := s.ListProgressEventsAfter(ctx, runID, 0, 10)
	if err != nil {
		t.Fatalf("ListProgressEventsAfter(all): %v", err)
	}
	if len(all) != 2 {
		t.Fatalf("expected 2 progress events, got %d", len(all))
	}

	filtered, err := s.ListProgressEventsAfter(ctx, runID, all[0].ID, 10)
	if err != nil {
		t.Fatalf("ListProgressEventsAfter(filtered): %v", err)
	}
	if len(filtered) != 1 {
		t.Fatalf("expected 1 filtered event, got %d", len(filtered))
	}
	if filtered[0].Summary != "second insert older timestamp" {
		t.Fatalf("expected ID cursor semantics, got %#v", filtered)
	}
}

func TestStoreProgressEventsKeepRunLevelIterationAtZero(t *testing.T) {
	s, ctx, runID := newProgressTestStore(t)

	if err := s.InsertProgressEvent(ctx, model.ProgressEvent{
		RunID:     runID,
		Iteration: 0,
		EventType: model.ProgressEventRunStarted,
		Status:    model.ProgressStatusStarted,
		Summary:   "run started",
		CreatedAt: 1000,
	}); err != nil {
		t.Fatalf("InsertProgressEvent: %v", err)
	}

	events, err := s.ListProgressEventsAfter(ctx, runID, 0, 10)
	if err != nil {
		t.Fatalf("ListProgressEventsAfter: %v", err)
	}
	if len(events) != 1 {
		t.Fatalf("expected 1 progress event, got %d", len(events))
	}
	if events[0].Iteration != 0 {
		t.Fatalf("expected run-level event iteration=0, got %d", events[0].Iteration)
	}
}

func TestStoreProgressEventsDefaultDetailJSONToEmptyObject(t *testing.T) {
	s, ctx, runID := newProgressTestStore(t)

	if err := s.InsertProgressEvent(ctx, model.ProgressEvent{
		RunID:     runID,
		Iteration: 0,
		EventType: model.ProgressEventRunStarted,
		Status:    model.ProgressStatusStarted,
		Summary:   "run started",
		CreatedAt: 1000,
	}); err != nil {
		t.Fatalf("InsertProgressEvent: %v", err)
	}

	rows, err := s.query(ctx, "SELECT detail_json FROM progress_events WHERE run_id="+q(runID)+" LIMIT 1;")
	if err != nil {
		t.Fatalf("query progress_events: %v", err)
	}
	if len(rows) != 1 || len(rows[0]) != 1 {
		t.Fatalf("expected one detail_json row, got %#v", rows)
	}
	if rows[0][0] != "{}" {
		t.Fatalf("expected detail_json to default to '{}', got %q", rows[0][0])
	}
}

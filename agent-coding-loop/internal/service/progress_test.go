package service

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/kina/agent-coding-loop/internal/config"
	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/tools"
)

func TestRunWithProgressReturnsRunIDBeforeCompletion(t *testing.T) {
	ctx := context.Background()
	repo := newServiceTestRepo(t)
	svc := newServiceForProgressTests(t)

	runID, resultCh, err := svc.RunWithProgress(ctx, model.RunSpec{
		Goal:          "validate repo",
		Repo:          repo,
		PRMode:        model.PRModeDryRun,
		MaxIterations: 1,
		Commands: model.CommandSet{
			Test: []string{"sleep 1; echo PASS"},
		},
	})
	if err != nil {
		t.Fatalf("RunWithProgress: %v", err)
	}
	if runID == "" {
		t.Fatal("expected run id")
	}

	select {
	case result := <-resultCh:
		t.Fatalf("expected early return before completion, got result %+v", result)
	case <-time.After(150 * time.Millisecond):
	}

	run, err := svc.GetRun(ctx, runID)
	if err != nil {
		t.Fatalf("GetRun: %v", err)
	}
	if run.Status == string(model.RunStatusCompleted) {
		t.Fatalf("expected run to still be in progress, got %s", run.Status)
	}

	select {
	case result := <-resultCh:
		if result.RunID != runID {
			t.Fatalf("expected result run id %q, got %q", runID, result.RunID)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("timed out waiting for run result")
	}
}

func TestServiceListProgressEventsAfterPassesThroughStore(t *testing.T) {
	ctx := context.Background()
	svc := newServiceForProgressTests(t)

	runID, err := svc.store.CreateRun(ctx, model.RunSpec{
		Goal:          "demo",
		Repo:          "/tmp/repo",
		PRMode:        model.PRModeDryRun,
		MaxIterations: 1,
	}, model.RunStatusQueued)
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	if err := svc.store.InsertProgressEvent(ctx, model.ProgressEvent{
		RunID:     runID,
		Iteration: 0,
		EventType: model.ProgressEventRunStarted,
		Status:    model.ProgressStatusStarted,
		Summary:   "run started",
		CreatedAt: 1000,
	}); err != nil {
		t.Fatalf("InsertProgressEvent(first): %v", err)
	}
	if err := svc.store.InsertProgressEvent(ctx, model.ProgressEvent{
		RunID:     runID,
		Iteration: 1,
		EventType: model.ProgressEventIterationStarted,
		Status:    model.ProgressStatusStarted,
		Summary:   "iteration 1 started",
		CreatedAt: 2000,
	}); err != nil {
		t.Fatalf("InsertProgressEvent(second): %v", err)
	}

	all, err := svc.GetProgressEventsAfter(ctx, runID, 0, 10)
	if err != nil {
		t.Fatalf("GetProgressEventsAfter(all): %v", err)
	}
	if len(all) != 2 {
		t.Fatalf("expected 2 events, got %d", len(all))
	}

	filtered, err := svc.GetProgressEventsAfter(ctx, runID, all[0].ID, 1)
	if err != nil {
		t.Fatalf("GetProgressEventsAfter(filtered): %v", err)
	}
	if len(filtered) != 1 {
		t.Fatalf("expected 1 filtered event, got %d", len(filtered))
	}
	if filtered[0].ID != all[1].ID {
		t.Fatalf("expected event id %d, got %d", all[1].ID, filtered[0].ID)
	}
}

func newServiceForProgressTests(t *testing.T) *Service {
	t.Helper()

	root := t.TempDir()
	cfg := &config.Config{
		DBPath:     filepath.Join(root, "state.db"),
		Artifacts:  filepath.Join(root, "artifacts"),
		ListenAddr: "127.0.0.1:0",
	}
	svc, err := New(cfg)
	if err != nil {
		t.Fatalf("service.New: %v", err)
	}
	return svc
}

func newServiceTestRepo(t *testing.T) string {
	t.Helper()

	repo := t.TempDir()
	runner := tools.NewRunner()
	mustRunServiceTest(t, runner, repo, "git init")
	mustRunServiceTest(t, runner, repo, "git config user.email test@example.com")
	mustRunServiceTest(t, runner, repo, "git config user.name tester")
	mustRunServiceTest(t, runner, repo, "sh -lc 'printf \"demo\\n\" > README.md'")
	mustRunServiceTest(t, runner, repo, "git add README.md")
	mustRunServiceTest(t, runner, repo, "git commit -m init")
	return repo
}

func mustRunServiceTest(t *testing.T, runner *tools.Runner, repo, cmd string) {
	t.Helper()
	if _, _, err := runner.Run(context.Background(), cmd, repo); err != nil {
		t.Fatalf("runner.Run(%q): %v", cmd, err)
	}
}

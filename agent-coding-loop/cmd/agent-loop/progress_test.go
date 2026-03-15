package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/kina/agent-coding-loop/internal/model"
)

func TestTailProgressStopsOnRunCompleted(t *testing.T) {
	var stderr bytes.Buffer
	err := tailProgress(context.Background(), func(context.Context, string, int64, int) ([]model.ProgressEvent, error) {
		return []model.ProgressEvent{
			{ID: 1, RunID: "run_123", Iteration: 0, EventType: model.ProgressEventRunStarted, Status: model.ProgressStatusStarted, Summary: "run started"},
			{ID: 2, RunID: "run_123", Iteration: 0, EventType: model.ProgressEventRunCompleted, Status: model.ProgressStatusCompleted, Summary: "run completed"},
		}, nil
	}, "run_123", &stderr, 100*time.Millisecond)
	if err != nil {
		t.Fatalf("tailProgress: %v", err)
	}
	if !strings.Contains(stderr.String(), "[run] started") {
		t.Fatalf("expected run started on stderr, got %q", stderr.String())
	}
	if !strings.Contains(stderr.String(), "[run] completed") {
		t.Fatalf("expected run completed on stderr, got %q", stderr.String())
	}
}

func TestTailProgressStopsOnRunBlocked(t *testing.T) {
	var stderr bytes.Buffer
	err := tailProgress(context.Background(), func(context.Context, string, int64, int) ([]model.ProgressEvent, error) {
		return []model.ProgressEvent{
			{ID: 1, RunID: "run_123", Iteration: 0, EventType: model.ProgressEventRunBlocked, Status: model.ProgressStatusError, Summary: "doom-loop detected on git_apply"},
		}, nil
	}, "run_123", &stderr, 100*time.Millisecond)
	if err != nil {
		t.Fatalf("tailProgress: %v", err)
	}
	if !strings.Contains(stderr.String(), "[run] blocked") {
		t.Fatalf("expected blocked stderr output, got %q", stderr.String())
	}
}

func TestTailProgressTimesOutWithoutTerminalEvent(t *testing.T) {
	var stderr bytes.Buffer
	err := tailProgress(context.Background(), func(context.Context, string, int64, int) ([]model.ProgressEvent, error) {
		return []model.ProgressEvent{
			{ID: 1, RunID: "run_123", Iteration: 1, EventType: model.ProgressEventCoderGenerating, Status: model.ProgressStatusStarted, Summary: "coder generating"},
		}, nil
	}, "run_123", &stderr, 20*time.Millisecond)
	if err == nil {
		t.Fatal("expected timeout error")
	}
}

func TestRunCommandWritesProgressToStderrAndResultToStdout(t *testing.T) {
	svc := newFakeCLIService()
	svc.runResult = model.RunResult{RunID: "run_123", Status: model.RunStatusCompleted, Summary: "done"}
	svc.progressBatches = [][]model.ProgressEvent{
		{
			{ID: 1, RunID: "run_123", Iteration: 0, EventType: model.ProgressEventRunStarted, Status: model.ProgressStatusStarted, Summary: "run started"},
			{ID: 2, RunID: "run_123", Iteration: 0, EventType: model.ProgressEventRunCompleted, Status: model.ProgressStatusCompleted, Summary: "run completed"},
		},
	}

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	err := runWithProgressCmd(context.Background(), svc, model.RunSpec{Goal: "demo", PRMode: model.PRModeDryRun, MaxIterations: 1}, &stdout, &stderr, 100*time.Millisecond)
	if err != nil {
		t.Fatalf("runWithProgressCmd: %v", err)
	}
	if svc.runWithProgressCalls != 1 {
		t.Fatalf("expected RunWithProgress to be called once, got %d", svc.runWithProgressCalls)
	}
	if !strings.Contains(stderr.String(), "[run] started") {
		t.Fatalf("expected stderr progress output, got %q", stderr.String())
	}
	var result model.RunResult
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal stdout result: %v body=%s", err, stdout.String())
	}
	if result.RunID != "run_123" {
		t.Fatalf("expected run id run_123, got %q", result.RunID)
	}
}

func TestResumeCommandUsesKnownRunIDWithoutResumeWithProgress(t *testing.T) {
	svc := newFakeCLIService()
	svc.resumeResult = model.RunResult{RunID: "run_123", Status: model.RunStatusCompleted, Summary: "done"}
	svc.progressBatches = [][]model.ProgressEvent{
		{
			{ID: 1, RunID: "run_123", Iteration: 1, EventType: model.ProgressEventIterationStarted, Status: model.ProgressStatusStarted, Summary: "iteration 1 started"},
			{ID: 2, RunID: "run_123", Iteration: 0, EventType: model.ProgressEventRunCompleted, Status: model.ProgressStatusCompleted, Summary: "run completed"},
		},
	}

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	err := resumeWithProgressCmd(context.Background(), svc, "run_123", &stdout, &stderr, 100*time.Millisecond)
	if err != nil {
		t.Fatalf("resumeWithProgressCmd: %v", err)
	}
	if svc.resumeCalls != 1 {
		t.Fatalf("expected Resume to be called once, got %d", svc.resumeCalls)
	}
	if svc.runWithProgressCalls != 0 {
		t.Fatalf("did not expect RunWithProgress in resume path, got %d calls", svc.runWithProgressCalls)
	}
	if !strings.Contains(stderr.String(), "[iter 1] iteration 1 started") {
		t.Fatalf("expected stderr progress output, got %q", stderr.String())
	}
	var result model.RunResult
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal stdout result: %v body=%s", err, stdout.String())
	}
	if result.RunID != "run_123" {
		t.Fatalf("expected run id run_123, got %q", result.RunID)
	}
}

type fakeCLIService struct {
	mu                   sync.Mutex
	progressBatches      [][]model.ProgressEvent
	runResult            model.RunResult
	resumeResult         model.RunResult
	runWithProgressCalls int
	resumeCalls          int
}

func newFakeCLIService() *fakeCLIService {
	return &fakeCLIService{}
}

func (f *fakeCLIService) RunWithProgress(_ context.Context, _ model.RunSpec) (string, <-chan model.RunResult, error) {
	f.mu.Lock()
	f.runWithProgressCalls++
	result := f.runResult
	f.mu.Unlock()

	ch := make(chan model.RunResult, 1)
	ch <- result
	close(ch)
	return result.RunID, ch, nil
}

func (f *fakeCLIService) Resume(_ context.Context, runID string) (model.RunResult, error) {
	f.mu.Lock()
	f.resumeCalls++
	result := f.resumeResult
	f.mu.Unlock()
	if result.RunID == "" {
		result.RunID = runID
	}
	return result, nil
}

func (f *fakeCLIService) GetProgressEventsAfter(_ context.Context, runID string, afterID int64, limit int) ([]model.ProgressEvent, error) {
	f.mu.Lock()
	defer f.mu.Unlock()

	if limit <= 0 {
		limit = 100
	}
	out := make([]model.ProgressEvent, 0, limit)
	for _, batch := range f.progressBatches {
		for _, event := range batch {
			if event.RunID != runID || event.ID <= afterID {
				continue
			}
			out = append(out, event)
			if len(out) == limit {
				return out, nil
			}
		}
	}
	return out, nil
}

func (f *fakeCLIService) String() string {
	return fmt.Sprintf("run_calls=%d resume_calls=%d", f.runWithProgressCalls, f.resumeCalls)
}

package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/kina/agent-coding-loop/internal/config"
	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/service"
	"github.com/kina/agent-coding-loop/internal/tools"
)

func TestProgressEndpointReturnsIncrementalEvents(t *testing.T) {
	ctx := context.Background()
	svc, runID := newHTTPProgressFixture(t)
	server := NewServer(svc)

	all, err := svc.GetProgressEventsAfter(ctx, runID, 0, 100)
	if err != nil {
		t.Fatalf("GetProgressEventsAfter(all): %v", err)
	}
	if len(all) < 2 {
		t.Fatalf("expected at least 2 progress events, got %d", len(all))
	}

	req := httptest.NewRequest(http.MethodGet, fmt.Sprintf("/v1/runs/%s/progress?after_id=%d&limit=1", runID, all[0].ID), nil)
	rec := httptest.NewRecorder()
	server.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body=%s", rec.Code, rec.Body.String())
	}

	var resp struct {
		RunID       string                `json:"run_id"`
		Events      []model.ProgressEvent `json:"events"`
		NextAfterID int64                 `json:"next_after_id"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal progress response: %v body=%s", err, rec.Body.String())
	}
	if resp.RunID != runID {
		t.Fatalf("expected run id %q, got %q", runID, resp.RunID)
	}
	if len(resp.Events) != 1 {
		t.Fatalf("expected 1 incremental event, got %d", len(resp.Events))
	}
	if resp.Events[0].ID != all[1].ID {
		t.Fatalf("expected event id %d, got %d", all[1].ID, resp.Events[0].ID)
	}
	if resp.NextAfterID != all[1].ID {
		t.Fatalf("expected next_after_id %d, got %d", all[1].ID, resp.NextAfterID)
	}
}

func TestStreamEndpointReplaysFromLastEventID(t *testing.T) {
	ctx := context.Background()
	svc, runID := newHTTPProgressFixture(t)
	server := NewServer(svc)

	all, err := svc.GetProgressEventsAfter(ctx, runID, 0, 100)
	if err != nil {
		t.Fatalf("GetProgressEventsAfter(all): %v", err)
	}
	if len(all) < 2 {
		t.Fatalf("expected at least 2 progress events, got %d", len(all))
	}

	req := httptest.NewRequest(http.MethodGet, fmt.Sprintf("/v1/runs/%s/stream", runID), nil)
	req.Header.Set("Last-Event-ID", strconv.FormatInt(all[0].ID, 10))
	rec := httptest.NewRecorder()
	server.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body=%s", rec.Code, rec.Body.String())
	}

	body := rec.Body.String()
	if !strings.Contains(body, "event: progress") {
		t.Fatalf("expected SSE progress event, got %q", body)
	}
	ids := extractSSEEventIDs(body)
	if len(ids) == 0 {
		t.Fatalf("expected at least one SSE event id, got %q", body)
	}
	if ids[0] != all[1].ID {
		t.Fatalf("expected replay to start from id %d, got ids=%v body=%q", all[1].ID, ids, body)
	}
	for _, id := range ids {
		if id == all[0].ID {
			t.Fatalf("did not expect replay to include last seen id %d, got ids=%v body=%q", all[0].ID, ids, body)
		}
	}
}

func TestStreamEndpointClosesAfterTerminalEvent(t *testing.T) {
	ctx := context.Background()
	svc, runID := newHTTPProgressFixture(t)
	server := NewServer(svc)

	all, err := svc.GetProgressEventsAfter(ctx, runID, 0, 100)
	if err != nil {
		t.Fatalf("GetProgressEventsAfter(all): %v", err)
	}
	if len(all) == 0 {
		t.Fatal("expected progress events")
	}

	req := httptest.NewRequest(http.MethodGet, fmt.Sprintf("/v1/runs/%s/stream", runID), nil)
	rec := httptest.NewRecorder()
	server.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body=%s", rec.Code, rec.Body.String())
	}

	terminal := all[len(all)-1]
	if terminal.EventType != model.ProgressEventRunCompleted && terminal.EventType != model.ProgressEventRunFailed && terminal.EventType != model.ProgressEventRunBlocked {
		t.Fatalf("expected terminal progress event, got %#v", terminal)
	}
	if !strings.Contains(rec.Body.String(), fmt.Sprintf("id: %d", terminal.ID)) {
		t.Fatalf("expected SSE body to include terminal id %d, got %q", terminal.ID, rec.Body.String())
	}
}

func TestLegacyEventsEndpointRemainsUnchanged(t *testing.T) {
	svc, runID := newHTTPProgressFixture(t)
	server := NewServer(svc)

	req := httptest.NewRequest(http.MethodGet, fmt.Sprintf("/v1/runs/%s/events", runID), nil)
	rec := httptest.NewRecorder()
	server.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body=%s", rec.Code, rec.Body.String())
	}

	var resp map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal legacy events response: %v body=%s", err, rec.Body.String())
	}
	events, ok := resp["events"].([]any)
	if !ok || len(events) == 0 {
		t.Fatalf("expected non-empty legacy events array, got %#v", resp["events"])
	}
	first, ok := events[0].(map[string]any)
	if !ok {
		t.Fatalf("expected legacy event object, got %#v", events[0])
	}
	if _, ok := first["type"]; !ok {
		t.Fatalf("expected legacy event type field, got %#v", first)
	}
	if _, ok := first["event_type"]; ok {
		t.Fatalf("did not expect progress event_type in legacy endpoint, got %#v", first)
	}
}

func newHTTPProgressFixture(t *testing.T) (*service.Service, string) {
	t.Helper()

	svc := newHTTPTestService(t)
	repo := newHTTPTestRepo(t)
	ctx := context.Background()

	runID, resultCh, err := svc.RunWithProgress(ctx, model.RunSpec{
		Goal:          "validate repo",
		Repo:          repo,
		PRMode:        model.PRModeDryRun,
		MaxIterations: 1,
		Commands: model.CommandSet{
			Test: []string{"echo PASS"},
		},
	})
	if err != nil {
		t.Fatalf("RunWithProgress: %v", err)
	}
	select {
	case result := <-resultCh:
		if result.RunID != runID {
			t.Fatalf("expected result run id %q, got %q", runID, result.RunID)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("timed out waiting for run result")
	}
	return svc, runID
}

func newHTTPTestService(t *testing.T) *service.Service {
	t.Helper()

	root := t.TempDir()
	cfg := &config.Config{
		DBPath:     filepath.Join(root, "state.db"),
		Artifacts:  filepath.Join(root, "artifacts"),
		ListenAddr: "127.0.0.1:0",
	}
	svc, err := service.New(cfg)
	if err != nil {
		t.Fatalf("service.New: %v", err)
	}
	return svc
}

func newHTTPTestRepo(t *testing.T) string {
	t.Helper()

	repo := t.TempDir()
	runner := tools.NewRunner()
	mustRunHTTPTest(t, runner, repo, "git init")
	mustRunHTTPTest(t, runner, repo, "git config user.email test@example.com")
	mustRunHTTPTest(t, runner, repo, "git config user.name tester")
	mustRunHTTPTest(t, runner, repo, "sh -lc 'printf \"demo\\n\" > README.md'")
	mustRunHTTPTest(t, runner, repo, "git add README.md")
	mustRunHTTPTest(t, runner, repo, "git commit -m init")
	return repo
}

func mustRunHTTPTest(t *testing.T, runner *tools.Runner, repo, cmd string) {
	t.Helper()
	if _, _, err := runner.Run(context.Background(), cmd, repo); err != nil {
		t.Fatalf("runner.Run(%q): %v", cmd, err)
	}
}

func extractSSEEventIDs(body string) []int64 {
	lines := strings.Split(body, "\n")
	ids := make([]int64, 0, len(lines))
	for _, line := range lines {
		if !strings.HasPrefix(line, "id: ") {
			continue
		}
		value, err := strconv.ParseInt(strings.TrimSpace(strings.TrimPrefix(line, "id: ")), 10, 64)
		if err != nil {
			continue
		}
		ids = append(ids, value)
	}
	return ids
}

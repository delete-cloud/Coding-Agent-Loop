package github

import (
	"context"
	"errors"
	"testing"

	"github.com/kina/agent-coding-loop/internal/model"
)

type fakeRunner struct {
	results map[string]error
}

func (f fakeRunner) Run(_ context.Context, cmd string, _ string) (string, string, error) {
	if err, ok := f.results[cmd]; ok {
		return "", "", err
	}
	return "", "", nil
}

func TestResolvePRModeAutoDryRunWhenGhUnavailable(t *testing.T) {
	c := NewClient(fakeRunner{results: map[string]error{"gh auth status": errors.New("missing")}})
	mode := c.ResolvePRMode(context.Background(), model.PRModeAuto, "https://github.com/org/repo.git")
	if mode != model.PRModeDryRun {
		t.Fatalf("expected dry_run, got %s", mode)
	}
}

func TestResolvePRModeAutoLiveWhenReady(t *testing.T) {
	c := NewClient(fakeRunner{results: map[string]error{}})
	mode := c.ResolvePRMode(context.Background(), model.PRModeAuto, "https://github.com/org/repo.git")
	if mode != model.PRModeLive {
		t.Fatalf("expected live, got %s", mode)
	}
}

func TestResolvePRModeAutoDryRunWhenNotGithubRemote(t *testing.T) {
	c := NewClient(fakeRunner{results: map[string]error{}})
	mode := c.ResolvePRMode(context.Background(), model.PRModeAuto, "https://example.com/scm/repo.git")
	if mode != model.PRModeDryRun {
		t.Fatalf("expected dry_run, got %s", mode)
	}
}

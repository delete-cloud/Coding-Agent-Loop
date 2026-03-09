package tools

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestIsDangerous(t *testing.T) {
	if !IsDangerousCommand("rm -rf .") {
		t.Fatal("expected dangerous")
	}
	if !IsDangerousCommand("git reset --hard") {
		t.Fatal("expected dangerous")
	}
	if IsDangerousCommand("go test ./...") {
		t.Fatal("unexpected dangerous")
	}
}

func TestRunnerRejectsDangerous(t *testing.T) {
	r := NewRunner()
	_, _, err := r.Run(context.Background(), "rm -rf /tmp/x", t.TempDir())
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestRunnerRejectsWriteInReadonlyMode(t *testing.T) {
	r := NewRunner(WithReadOnly(true))
	_, _, err := r.Run(context.Background(), "git commit -m x", t.TempDir())
	if err == nil {
		t.Fatal("expected readonly error")
	}
}

func TestRunnerExecutesSafeCommand(t *testing.T) {
	r := NewRunner()
	out, _, err := r.Run(context.Background(), "echo hello", t.TempDir())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if out == "" {
		t.Fatal("expected stdout")
	}
}

func TestRunnerFiltersAgentOrchestrationEnv(t *testing.T) {
	t.Setenv("AGENT_LOOP_DB_PATH", "/tmp/state.db")
	t.Setenv("OPENAI_MODEL", "claude-haiku-4-5")
	t.Setenv("ANTHROPIC_AUTH_TOKEN", "secret")
	t.Setenv("KB_DB_PATH", "/tmp/kb")
	t.Setenv("KEEP_ME", "visible")

	r := NewRunner()
	out, _, err := r.Run(context.Background(), "env | sort", t.TempDir())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if strings.Contains(out, "AGENT_LOOP_DB_PATH=") {
		t.Fatalf("expected AGENT_LOOP_DB_PATH to be filtered, got:\n%s", out)
	}
	if strings.Contains(out, "OPENAI_MODEL=") {
		t.Fatalf("expected OPENAI_MODEL to be filtered, got:\n%s", out)
	}
	if strings.Contains(out, "ANTHROPIC_AUTH_TOKEN=") {
		t.Fatalf("expected ANTHROPIC_AUTH_TOKEN to be filtered, got:\n%s", out)
	}
	if strings.Contains(out, "KB_DB_PATH=") {
		t.Fatalf("expected KB_DB_PATH to be filtered, got:\n%s", out)
	}
	if !strings.Contains(out, "KEEP_ME=visible") {
		t.Fatalf("expected regular env to be preserved, got:\n%s", out)
	}
	if !strings.Contains(out, "PATH=") {
		t.Fatalf("expected PATH to be preserved, got:\n%s", out)
	}
}

func TestRepoTools(t *testing.T) {
	repo := t.TempDir()
	if err := os.MkdirAll(filepath.Join(repo, "sub"), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(repo, "sub", "a.txt"), []byte("hello world"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	list, err := RepoList(repo, ".")
	if err != nil {
		t.Fatalf("RepoList: %v", err)
	}
	if len(list) == 0 {
		t.Fatal("expected files")
	}
	content, err := RepoRead(repo, "sub/a.txt", 1024)
	if err != nil {
		t.Fatalf("RepoRead: %v", err)
	}
	if content == "" {
		t.Fatal("expected content")
	}
	matches, err := RepoSearch(repo, "hello")
	if err != nil {
		t.Fatalf("RepoSearch: %v", err)
	}
	if len(matches) != 1 {
		t.Fatalf("expected 1 match, got %d", len(matches))
	}
}

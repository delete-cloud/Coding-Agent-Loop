package tools

import (
	"context"
	"os"
	"path/filepath"
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

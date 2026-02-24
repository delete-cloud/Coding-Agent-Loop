package tools

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRepoReadAcceptsLeadingSlash(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "internal", "config"), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	path := filepath.Join(root, "internal", "config", "config.go")
	if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	out, err := RepoRead(root, "/internal/config/config.go", 1024)
	if err != nil {
		t.Fatalf("RepoRead: %v", err)
	}
	if out != "x" {
		t.Fatalf("expected x, got %q", out)
	}
}

func TestSecurePathBlocksEscape(t *testing.T) {
	root := t.TempDir()
	if _, err := securePath(root, "../etc/passwd"); err == nil {
		t.Fatalf("expected error")
	}
}

func TestSecurePathBlocksPrefixBypass(t *testing.T) {
	root := filepath.Join(t.TempDir(), "repo")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if _, err := securePath(root, "../repo-bad/file"); err == nil {
		t.Fatalf("expected error")
	}
}

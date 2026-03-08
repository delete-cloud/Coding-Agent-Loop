package tools

import (
	"os"
	"path/filepath"
	"strings"
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

func TestRepoReadDirectoryReturnsEntries(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "a.txt"), []byte("a"), 0o644); err != nil {
		t.Fatalf("write a.txt: %v", err)
	}
	if err := os.MkdirAll(filepath.Join(root, "sub"), 0o755); err != nil {
		t.Fatalf("mkdir sub: %v", err)
	}
	if err := os.WriteFile(filepath.Join(root, "sub", "b.txt"), []byte("b"), 0o644); err != nil {
		t.Fatalf("write b.txt: %v", err)
	}

	out, err := RepoRead(root, ".", 1024)
	if err != nil {
		t.Fatalf("RepoRead directory: %v", err)
	}
	if out == "" {
		t.Fatalf("expected directory listing output")
	}
	if !strings.Contains(out, "a.txt") {
		t.Fatalf("expected a.txt in output, got %q", out)
	}
}

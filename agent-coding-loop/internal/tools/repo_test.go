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

func TestNormalizeRelPathStripsForwardSlashOnWindowsStyleInputs(t *testing.T) {
	root := filepath.Join(`C:\Users\kina`, "agent-coding-loop")
	got := normalizeRelPath(root, "/internal/config/config.go")
	if got != filepath.Join("internal", "config", "config.go") {
		t.Fatalf("unexpected normalized path: %q", got)
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

func TestRepoListSanitizesModelPollutedDotPath(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "a.txt"), []byte("a"), 0o644); err != nil {
		t.Fatalf("write a.txt: %v", err)
	}

	got, err := RepoList(root, ".}")
	if err != nil {
		t.Fatalf("RepoList(.}): %v", err)
	}
	if len(got) != 1 || got[0] != "a.txt" {
		t.Fatalf("unexpected RepoList result: %v", got)
	}
}

func TestRepoReadSanitizesWrappedRelativePath(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "internal", "config"), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	path := filepath.Join(root, "internal", "config", "config.go")
	if err := os.WriteFile(path, []byte("package config"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	got, err := RepoRead(root, "`./internal/config/config.go`}", 1024)
	if err != nil {
		t.Fatalf("RepoRead wrapped path: %v", err)
	}
	if got != "package config" {
		t.Fatalf("unexpected RepoRead content: %q", got)
	}
}

func TestRepoReadStripsRepoNamePrefixFromModelPath(t *testing.T) {
	root := filepath.Join(t.TempDir(), "agent-coding-loop")
	if err := os.MkdirAll(filepath.Join(root, "internal", "config"), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	path := filepath.Join(root, "internal", "config", "config.go")
	if err := os.WriteFile(path, []byte("package config"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	got, err := RepoRead(root, "agent-coding-loop/internal/config/config.go", 1024)
	if err != nil {
		t.Fatalf("RepoRead repo-prefixed path: %v", err)
	}
	if got != "package config" {
		t.Fatalf("unexpected RepoRead content: %q", got)
	}
}

func TestRepoListStripsRepoNamePrefixFromModelPath(t *testing.T) {
	root := filepath.Join(t.TempDir(), "agent-coding-loop")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(root, "README.md"), []byte("hello"), 0o644); err != nil {
		t.Fatalf("write README.md: %v", err)
	}

	got, err := RepoList(root, "agent-coding-loop/.}")
	if err != nil {
		t.Fatalf("RepoList repo-prefixed polluted path: %v", err)
	}
	if len(got) != 1 || got[0] != "README.md" {
		t.Fatalf("unexpected RepoList result: %v", got)
	}
}

func TestRepoReadStripsEmbeddedAbsoluteRepoPrefix(t *testing.T) {
	root := filepath.Join(t.TempDir(), "agent-coding-loop")
	if err := os.MkdirAll(filepath.Join(root, "internal", "config"), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	path := filepath.Join(root, "internal", "config", "config.go")
	if err := os.WriteFile(path, []byte("package config"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	got, err := RepoRead(root, path+"}", 1024)
	if err != nil {
		t.Fatalf("RepoRead embedded absolute path: %v", err)
	}
	if got != "package config" {
		t.Fatalf("unexpected RepoRead content: %q", got)
	}
}

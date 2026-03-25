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

func TestIsWriteCommandCoversCommonBypasses(t *testing.T) {
	shouldBlock := []string{
		"git commit -m x",
		"git apply patch.diff",
		"echo hello > file.go",
		"cat > file.go",
		"sed -i 's/a/b/' file.go",
		"tee file.go",
		"mv a.go b.go",
		"cp a.go b.go",
		"touch new.go",
		`printf 'package main' > file.go`,
		"patch -p1 < fix.patch",
		"bash -c 'echo hi'",
		"sh -c 'echo hi'",
		"python3 -c 'open(\"f\",\"w\")'",
		"python -c 'import os'",
		"node -e 'fs.writeFileSync()'",
		"chmod +x script.sh",
		"ln -s a b",
		"mkdir -p new/dir",
		"cat file >> other.go",
		"go test ./... > output.txt",
		"echo data >> append.log",
		// Shell launcher bypasses (P1 fix)
		"sh -lc 'echo bad > file'",
		"bash --login -c 'tee file'",
		"env FOO=1 sh -c 'cat >> file'",
		"/usr/bin/bash -c 'echo hi'",
		"env -i bash -c 'rm x'",
		"dash -c 'echo test'",
		"go test ./... | bash -c 'cat > out'",
		"true && sh -c 'echo bad'",
		"true & sh -c 'echo bad > file'",
		"printf ok\nsh -c 'echo bad > file'",
	}
	for _, cmd := range shouldBlock {
		if !IsWriteCommand(cmd) {
			t.Errorf("expected IsWriteCommand(%q) = true", cmd)
		}
	}

	shouldAllow := []string{
		"go test ./...",
		"go build ./...",
		"go vet ./...",
		"go doc fmt.Println",
		"grep -r handleHealthz .",
		"cat file.go",
		"head -20 file.go",
		"tail -10 file.go",
		"wc -l file.go",
		"git diff -- .",
		"git log --oneline -5",
		"git status",
		"ls -la",
		"find . -name '*.go'",
		// Must not false-positive on these (P1 fix)
		"ssh host ls",
		"go test -shuffle on",
		"git show HEAD:file.go",
	}
	for _, cmd := range shouldAllow {
		if IsWriteCommand(cmd) {
			t.Errorf("expected IsWriteCommand(%q) = false", cmd)
		}
	}
}

func TestReadOnlyRunnerBlocksFileWrite(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "victim.go")
	if err := os.WriteFile(target, []byte("package main\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	r := NewRunner(WithReadOnly(true))

	writeAttempts := []string{
		"echo 'func Bad(){}' >> victim.go",
		"printf 'func Bad(){}' > victim.go",
		"cat > victim.go <<'EOF'\nfunc Bad(){}\nEOF",
		"python3 -c \"open('victim.go','a').write('func Bad(){}')\"",
		"bash -c 'echo bad > victim.go'",
	}
	for _, cmd := range writeAttempts {
		_, _, err := r.Run(context.Background(), cmd, dir)
		if err == nil {
			t.Errorf("read-only runner should block: %s", cmd)
		}
	}

	content, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	if string(content) != "package main\n" {
		t.Fatalf("file was modified despite read-only runner: %q", string(content))
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

func TestRunnerRejectsPatchLikeValidationCommands(t *testing.T) {
	dir := t.TempDir()
	r := NewRunner()

	cases := []struct {
		name string
		cmd  string
	}{
		{
			name: "heredoc diff command",
			cmd: "git apply --check <<'PATCH'\ndiff --git a/Makefile b/Makefile\n--- a/Makefile\n+++ b/Makefile\n@@ -1 +1,2 @@\n+# comment\n build:\nPATCH",
		},
		{
			name: "placeholder patch file",
			cmd:  "git apply --check <patch-file>",
		},
		{
			name: "placeholder your patch file",
			cmd:  "git apply --check <your-patch-file>",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, _, err := r.Run(context.Background(), tc.cmd, dir)
			if err == nil {
				t.Fatal("expected protocol validation error")
			}
			if !strings.Contains(err.Error(), "patch content must be placed in the patch field") {
				t.Fatalf("expected stable patch-field guidance, got %v", err)
			}
			if !strings.Contains(err.Error(), "commands may only contain validation commands") {
				t.Fatalf("expected stable validation-command guidance, got %v", err)
			}
		})
	}
}

func TestRunnerAllowsNormalValidationCommands(t *testing.T) {
	r := NewRunner()
	dir := t.TempDir()

	allowed := []string{
		"go build ./...",
		"make -n test",
	}

	for _, cmd := range allowed {
		t.Run(cmd, func(t *testing.T) {
			_, _, err := r.Run(context.Background(), cmd, dir)
			if err == nil {
				return
			}
			if strings.Contains(err.Error(), "patch content must be placed in the patch field") {
				t.Fatalf("expected normal validation command to bypass patch-like protocol guard, got %v", err)
			}
		})
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

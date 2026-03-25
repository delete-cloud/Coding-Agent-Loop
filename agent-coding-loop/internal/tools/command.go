package tools

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type Runner struct {
	readOnly bool
}

const patchLikeCommandError = "patch-like command blocked: patch content must be placed in the patch field; commands may only contain validation commands"

type RunnerOption func(*Runner)

func WithReadOnly(v bool) RunnerOption {
	return func(r *Runner) {
		r.readOnly = v
	}
}

func NewRunner(opts ...RunnerOption) *Runner {
	r := &Runner{}
	for _, opt := range opts {
		opt(r)
	}
	return r
}

func (r *Runner) Run(ctx context.Context, cmd string, dir string) (string, string, error) {
	cleanDir, err := filepath.Abs(dir)
	if err != nil {
		return "", "", err
	}
	if isPatchLikeValidationCommand(cmd) {
		return "", "", fmt.Errorf(patchLikeCommandError)
	}
	if IsDangerousCommand(cmd) {
		return "", "", fmt.Errorf("dangerous command blocked: %s", cmd)
	}
	if r.readOnly && IsWriteCommand(cmd) {
		return "", "", fmt.Errorf("read-only mode blocks write command: %s", cmd)
	}
	execCmd := exec.CommandContext(ctx, "sh", "-lc", cmd)
	execCmd.Dir = cleanDir
	execCmd.Env = filteredCommandEnv(os.Environ())
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	execCmd.Stdout = &stdout
	execCmd.Stderr = &stderr
	err = execCmd.Run()
	if err != nil {
		return stdout.String(), stderr.String(), fmt.Errorf("run command failed: %w", err)
	}
	return stdout.String(), stderr.String(), nil
}

func filteredCommandEnv(env []string) []string {
	if len(env) == 0 {
		return nil
	}
	blockedPrefixes := []string{
		"AGENT_LOOP_",
		"OPENAI_",
		"ANTHROPIC_",
		"KB_",
	}
	out := make([]string, 0, len(env))
	for _, entry := range env {
		blocked := false
		for _, prefix := range blockedPrefixes {
			if strings.HasPrefix(entry, prefix) {
				blocked = true
				break
			}
		}
		if !blocked {
			out = append(out, entry)
		}
	}
	return out
}

func isPatchLikeValidationCommand(cmd string) bool {
	v := strings.ToLower(strings.TrimSpace(cmd))
	if v == "" {
		return false
	}
	if strings.Contains(v, "git apply --check") {
		if strings.Contains(v, "<patch-file>") || strings.Contains(v, "<your-patch-file>") {
			return true
		}
		if containsShellHeredoc(v) && containsUnifiedDiffStructure(v) {
			return true
		}
	}
	return containsShellHeredoc(v) && containsUnifiedDiffStructure(v)
}

func containsShellHeredoc(cmd string) bool {
	return strings.Contains(cmd, "<<")
}

func containsUnifiedDiffStructure(cmd string) bool {
	if strings.Contains(cmd, "diff --git") {
		return true
	}
	if strings.Contains(cmd, "\n--- ") && strings.Contains(cmd, "\n+++ ") {
		return true
	}
	return strings.Contains(cmd, "\n@@ ")
}

func IsDangerousCommand(cmd string) bool {
	v := strings.ToLower(strings.TrimSpace(cmd))
	blocked := []string{
		"rm -rf",
		"git reset --hard",
		"git checkout --",
		":(){:|:&};:",
		"mkfs",
		"dd if=",
	}
	for _, item := range blocked {
		if strings.Contains(v, item) {
			return true
		}
	}
	return false
}

func IsWriteCommand(cmd string) bool {
	v := strings.ToLower(strings.TrimSpace(cmd))
	// Check for shell output redirections (>, >>).
	if containsShellRedirect(v) {
		return true
	}
	// Check for nested shell launchers (sh -lc, bash --login -c, env sh -c, etc).
	if containsShellLauncher(v) {
		return true
	}
	writePatterns := []string{
		"git commit",
		"git push",
		"git add",
		"git apply",
		"git stash",
		"git revert",
		"git cherry-pick",
		"echo ",
		"cat >",
		"sed -i",
		"tee ",
		"mv ",
		"cp ",
		"touch ",
		"printf ",
		"patch ",
		"patch -",
		"install ",
		"chmod ",
		"chown ",
		"ln ",
		"mkdir ",
		"rmdir ",
		"truncate ",
		"python ",
		"python3 ",
		"ruby ",
		"perl ",
		"node -e",
		"node --eval",
	}
	for _, p := range writePatterns {
		if strings.Contains(v, p) {
			return true
		}
	}
	return false
}

// shellLaunchers are executables that spawn a nested shell and can execute
// arbitrary commands, bypassing the read-only write-command check.
var shellLaunchers = map[string]struct{}{
	"sh": {}, "bash": {}, "zsh": {}, "dash": {}, "ksh": {}, "fish": {},
}

// containsShellLauncher detects nested shell invocations like "sh -lc ...",
// "bash --login -c ...", "env FOO=1 sh -c ...", "/usr/bin/bash -c ...", etc.
// It splits the command into segments on unquoted |, &&, ||, ;, &, and \n,
// then checks whether the first real executable token in each segment is a
// known shell.
func containsShellLauncher(cmd string) bool {
	// Process each segment separated by unquoted pipe/logic operators.
	segStart := 0
	inSingle := false
	inDouble := false
	escaped := false
	for i := 0; i < len(cmd); i++ {
		if escaped {
			escaped = false
			continue
		}
		ch := cmd[i]
		if ch == '\\' {
			escaped = true
			continue
		}
		if ch == '\'' && !inDouble {
			inSingle = !inSingle
			continue
		}
		if ch == '"' && !inSingle {
			inDouble = !inDouble
			continue
		}
		if inSingle || inDouble {
			continue
		}
		isSep := false
		skip := 0
		switch {
		case ch == '|' && i+1 < len(cmd) && cmd[i+1] == '|': // ||
			isSep = true
			skip = 2
		case ch == '&' && i+1 < len(cmd) && cmd[i+1] == '&': // &&
			isSep = true
			skip = 2
		case ch == '|': // pipe
			isSep = true
			skip = 1
		case ch == '&': // background
			isSep = true
			skip = 1
		case ch == ';':
			isSep = true
			skip = 1
		case ch == '\n':
			isSep = true
			skip = 1
		}
		if isSep {
			if segmentStartsWithShell(cmd[segStart:i]) {
				return true
			}
			segStart = i + skip
			i += skip - 1
		}
	}
	return segmentStartsWithShell(cmd[segStart:])
}

// segmentStartsWithShell extracts the first real executable token from a
// command segment, skipping "env", env flags (-i, -u, etc), and KEY=VALUE
// assignments. Returns true if that token's basename is a known shell.
func segmentStartsWithShell(seg string) bool {
	tokens := quoteAwareTokenize(strings.TrimSpace(seg))
	skippedEnv := false
	for _, tok := range tokens {
		if !skippedEnv && tok == "env" {
			skippedEnv = true
			continue
		}
		// After "env", skip flags like -i, -u, --ignore-environment.
		if skippedEnv && len(tok) > 0 && tok[0] == '-' {
			continue
		}
		// Skip KEY=VALUE assignments (valid shell: ^[A-Za-z_][A-Za-z0-9_]*=).
		if looksLikeEnvAssignment(tok) {
			skippedEnv = true // env implied
			continue
		}
		base := filepath.Base(tok)
		_, isShell := shellLaunchers[base]
		return isShell
	}
	return false
}

// looksLikeEnvAssignment returns true for tokens like "FOO=bar", "PATH=/usr/bin".
// Only accepts valid shell variable names before the '='.
func looksLikeEnvAssignment(tok string) bool {
	eq := strings.IndexByte(tok, '=')
	if eq <= 0 {
		return false
	}
	name := tok[:eq]
	for i, ch := range name {
		if i == 0 {
			if !((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || ch == '_') {
				return false
			}
		} else {
			if !((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_') {
				return false
			}
		}
	}
	return true
}

// quoteAwareTokenize splits a string on unquoted whitespace, handling single
// quotes, double quotes, and backslash escapes. Quotes are stripped from
// the returned tokens.
func quoteAwareTokenize(s string) []string {
	var tokens []string
	var cur []byte
	inSingle := false
	inDouble := false
	escaped := false
	for i := 0; i < len(s); i++ {
		if escaped {
			cur = append(cur, s[i])
			escaped = false
			continue
		}
		ch := s[i]
		if ch == '\\' && !inSingle {
			escaped = true
			continue
		}
		if ch == '\'' && !inDouble {
			inSingle = !inSingle
			continue
		}
		if ch == '"' && !inSingle {
			inDouble = !inDouble
			continue
		}
		if (ch == ' ' || ch == '\t') && !inSingle && !inDouble {
			if len(cur) > 0 {
				tokens = append(tokens, string(cur))
				cur = cur[:0]
			}
			continue
		}
		cur = append(cur, ch)
	}
	if len(cur) > 0 {
		tokens = append(tokens, string(cur))
	}
	return tokens
}

// containsShellRedirect detects >, >> output redirections that are not
// inside quotes or part of comparison operators (e.g. 2>&1 is allowed
// since it redirects stderr to stdout, not to a file).
func containsShellRedirect(cmd string) bool {
	inSingle := false
	inDouble := false
	for i := 0; i < len(cmd); i++ {
		ch := cmd[i]
		if ch == '\'' && !inDouble {
			inSingle = !inSingle
			continue
		}
		if ch == '"' && !inSingle {
			inDouble = !inDouble
			continue
		}
		if inSingle || inDouble {
			continue
		}
		if ch == '>' {
			// Allow 2>&1, >&2, etc. (fd-to-fd redirections).
			rest := cmd[i+1:]
			trimmed := strings.TrimLeft(rest, "> ")
			if len(trimmed) > 0 && trimmed[0] == '&' {
				continue
			}
			return true
		}
	}
	return false
}

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
	writePrefixes := []string{
		"git commit",
		"git push",
		"git add",
		"git apply",
		"echo ",
		"cat >",
		"sed -i",
		"tee ",
		"mv ",
		"cp ",
		"touch ",
	}
	for _, p := range writePrefixes {
		if strings.Contains(v, p) {
			return true
		}
	}
	return false
}

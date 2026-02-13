//go:build eino

package tools

import (
	"context"
	"strings"
	"testing"

	"github.com/cloudwego/eino/components/tool"
)

func TestBuildCoderToolsIncludesRunCommand(t *testing.T) {
	got, err := BuildCoderTools(t.TempDir(), nil, NewRunner())
	if err != nil {
		t.Fatalf("BuildCoderTools: %v", err)
	}
	names := toolNames(t, got)
	if !containsName(names, "run_command") {
		t.Fatalf("expected run_command in coder tools, got %v", names)
	}
}

func TestBuildReviewerToolsReadOnlySurface(t *testing.T) {
	got, err := BuildReviewerTools(t.TempDir(), nil, NewRunner())
	if err != nil {
		t.Fatalf("BuildReviewerTools: %v", err)
	}
	names := toolNames(t, got)
	if containsName(names, "run_command") {
		t.Fatalf("reviewer tools must not include run_command, got %v", names)
	}
	if !containsName(names, "repo_read") {
		t.Fatalf("expected repo_read in reviewer tools, got %v", names)
	}
}

func toolNames(t *testing.T, items []tool.BaseTool) []string {
	t.Helper()
	out := make([]string, 0, len(items))
	for _, item := range items {
		info, err := item.Info(context.Background())
		if err != nil {
			t.Fatalf("tool info: %v", err)
		}
		out = append(out, info.Name)
	}
	return out
}

func containsName(items []string, name string) bool {
	target := strings.TrimSpace(name)
	for _, item := range items {
		if item == target {
			return true
		}
	}
	return false
}

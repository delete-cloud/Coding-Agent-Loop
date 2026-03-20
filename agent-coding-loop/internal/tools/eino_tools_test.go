package tools

import (
	"context"
	"strings"
	"testing"

	"github.com/cloudwego/eino/components/tool"
)

func TestBuildCoderToolsIncludesRunCommand(t *testing.T) {
	got, err := BuildCoderTools(t.TempDir(), nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildCoderTools: %v", err)
	}
	names := toolNames(t, got)
	if !containsName(names, "run_command") {
		t.Fatalf("expected run_command in coder tools, got %v", names)
	}
	if containsName(names, "list_skills") || containsName(names, "view_skill") {
		t.Fatalf("expected coder tools to exclude skill tools, got %v", names)
	}
}

func TestBuildReviewerToolsReadOnlySurface(t *testing.T) {
	got, err := BuildReviewerTools(t.TempDir(), nil, NewRunner(), nil)
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
	if containsName(names, "list_skills") || containsName(names, "view_skill") {
		t.Fatalf("expected reviewer tools to exclude skill tools, got %v", names)
	}
}

func TestBuildPlannerToolsReadOnlySurface(t *testing.T) {
	got, err := BuildPlannerTools(t.TempDir(), nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildPlannerTools: %v", err)
	}
	names := toolNames(t, got)
	if containsName(names, "run_command") {
		t.Fatalf("planner tools must not include run_command, got %v", names)
	}
	if !containsName(names, "kb_search") {
		t.Fatalf("expected kb_search in planner tools, got %v", names)
	}
}

func TestBuildToolsForModeCodeIncludesRunCommand(t *testing.T) {
	got, err := BuildToolsForMode(t.TempDir(), ToolModeCode, nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildToolsForMode(code): %v", err)
	}
	names := toolNames(t, got)
	if !containsName(names, "run_command") {
		t.Fatalf("expected run_command in code mode tools, got %v", names)
	}
}

func TestBuildToolsForModePlanExcludesRunCommand(t *testing.T) {
	got, err := BuildToolsForMode(t.TempDir(), ToolModePlan, nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildToolsForMode(plan): %v", err)
	}
	names := toolNames(t, got)
	if containsName(names, "run_command") {
		t.Fatalf("plan mode tools must not include run_command, got %v", names)
	}
}

func TestBuildToolsForModeRepairExcludesRunCommand(t *testing.T) {
	got, err := BuildToolsForMode(t.TempDir(), ToolModeRepair, nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildToolsForMode(repair): %v", err)
	}
	names := toolNames(t, got)
	if containsName(names, "run_command") {
		t.Fatalf("repair mode tools must not include run_command, got %v", names)
	}
	if !containsName(names, "repo_read") {
		t.Fatalf("expected repo_read in repair mode tools, got %v", names)
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

func TestKBSearchEmptyQueryDoesNotHardFail(t *testing.T) {
	got, err := BuildReviewerTools(t.TempDir(), nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildReviewerTools: %v", err)
	}
	var kbTool tool.InvokableTool
	for _, item := range got {
		info, infoErr := item.Info(context.Background())
		if infoErr != nil || info == nil {
			continue
		}
		if info.Name != "kb_search" {
			continue
		}
		inv, ok := item.(tool.InvokableTool)
		if !ok {
			t.Fatalf("kb_search is not invokable")
		}
		kbTool = inv
		break
	}
	if kbTool == nil {
		t.Fatalf("kb_search not found")
	}
	out, err := kbTool.InvokableRun(context.Background(), `{"query":""}`)
	if err != nil {
		t.Fatalf("kb_search empty query should not return error: %v", err)
	}
	if !strings.Contains(strings.ToLower(out), "query") {
		t.Fatalf("expected guidance in output, got %q", out)
	}
}

func TestRepoSearchEmptyQueryDoesNotHardFail(t *testing.T) {
	got, err := BuildReviewerTools(t.TempDir(), nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildReviewerTools: %v", err)
	}
	var repoSearchTool tool.InvokableTool
	for _, item := range got {
		info, infoErr := item.Info(context.Background())
		if infoErr != nil || info == nil {
			continue
		}
		if info.Name != "repo_search" {
			continue
		}
		inv, ok := item.(tool.InvokableTool)
		if !ok {
			t.Fatalf("repo_search is not invokable")
		}
		repoSearchTool = inv
		break
	}
	if repoSearchTool == nil {
		t.Fatalf("repo_search not found")
	}
	out, err := repoSearchTool.InvokableRun(context.Background(), `{"query":""}`)
	if err != nil {
		t.Fatalf("repo_search empty query should not return error: %v", err)
	}
	if !strings.Contains(strings.ToLower(out), "query") {
		t.Fatalf("expected guidance in output, got %q", out)
	}
}

func TestRepoListMissingPathDoesNotHardFail(t *testing.T) {
	repo := t.TempDir()
	got, err := BuildReviewerTools(repo, nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildReviewerTools: %v", err)
	}
	var repoListTool tool.InvokableTool
	for _, item := range got {
		info, infoErr := item.Info(context.Background())
		if infoErr != nil || info == nil {
			continue
		}
		if info.Name != "repo_list" {
			continue
		}
		inv, ok := item.(tool.InvokableTool)
		if !ok {
			t.Fatalf("repo_list is not invokable")
		}
		repoListTool = inv
		break
	}
	if repoListTool == nil {
		t.Fatalf("repo_list not found")
	}
	out, err := repoListTool.InvokableRun(context.Background(), `{"path":".agent-loop-artifacts"}`)
	if err != nil {
		t.Fatalf("repo_list missing path should not return error: %v", err)
	}
	if !strings.Contains(strings.ToLower(out), "not found") {
		t.Fatalf("expected not-found guidance, got %q", out)
	}
}

func TestRepoReadMissingPathDoesNotHardFail(t *testing.T) {
	repo := t.TempDir()
	got, err := BuildReviewerTools(repo, nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildReviewerTools: %v", err)
	}
	var repoReadTool tool.InvokableTool
	for _, item := range got {
		info, infoErr := item.Info(context.Background())
		if infoErr != nil || info == nil {
			continue
		}
		if info.Name != "repo_read" {
			continue
		}
		inv, ok := item.(tool.InvokableTool)
		if !ok {
			t.Fatalf("repo_read is not invokable")
		}
		repoReadTool = inv
		break
	}
	if repoReadTool == nil {
		t.Fatalf("repo_read not found")
	}
	out, err := repoReadTool.InvokableRun(context.Background(), `{"path":"missing/file.go"}`)
	if err != nil {
		t.Fatalf("repo_read missing path should not return error: %v", err)
	}
	if !strings.Contains(strings.ToLower(out), "not found") {
		t.Fatalf("expected not-found guidance, got %q", out)
	}
}

func TestRepoListEscapePathReturnsStructuredError(t *testing.T) {
	repo := t.TempDir()
	got, err := BuildReviewerTools(repo, nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildReviewerTools: %v", err)
	}
	var repoListTool tool.InvokableTool
	for _, item := range got {
		info, infoErr := item.Info(context.Background())
		if infoErr != nil || info == nil {
			continue
		}
		if info.Name != "repo_list" {
			continue
		}
		inv, ok := item.(tool.InvokableTool)
		if !ok {
			t.Fatalf("repo_list is not invokable")
		}
		repoListTool = inv
		break
	}
	if repoListTool == nil {
		t.Fatalf("repo_list not found")
	}
	out, err := repoListTool.InvokableRun(context.Background(), `{"path":"../etc/passwd"}`)
	if err != nil {
		t.Fatalf("repo_list escape path should not return error: %v", err)
	}
	if !strings.Contains(out, "repo_list error") || !strings.Contains(out, "path escapes repo root") {
		t.Fatalf("expected structured repo_list error, got %q", out)
	}
}

func TestRepoReadEscapePathReturnsStructuredError(t *testing.T) {
	repo := t.TempDir()
	got, err := BuildReviewerTools(repo, nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildReviewerTools: %v", err)
	}
	var repoReadTool tool.InvokableTool
	for _, item := range got {
		info, infoErr := item.Info(context.Background())
		if infoErr != nil || info == nil {
			continue
		}
		if info.Name != "repo_read" {
			continue
		}
		inv, ok := item.(tool.InvokableTool)
		if !ok {
			t.Fatalf("repo_read is not invokable")
		}
		repoReadTool = inv
		break
	}
	if repoReadTool == nil {
		t.Fatalf("repo_read not found")
	}
	out, err := repoReadTool.InvokableRun(context.Background(), `{"path":"../etc/passwd"}`)
	if err != nil {
		t.Fatalf("repo_read escape path should not return error: %v", err)
	}
	if !strings.Contains(out, "repo_read error") || !strings.Contains(out, "path escapes repo root") {
		t.Fatalf("expected structured repo_read error, got %q", out)
	}
}

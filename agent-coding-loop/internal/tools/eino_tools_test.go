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

func TestReadOnlyToolDescriptionsIncludeUsageBoundaries(t *testing.T) {
	got, err := BuildReviewerTools(t.TempDir(), nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildReviewerTools: %v", err)
	}

	descs := toolDescriptions(t, got)
	cases := []struct {
		name     string
		requires []string
	}{
		{
			name: "repo_list",
			requires: []string{
				"use when you need directory structure",
				"do not use when you already know the exact file path",
				`{"path":"internal"}`,
				"if a path is missing, fix the path or switch to repo_read",
			},
		},
		{
			name: "repo_read",
			requires: []string{
				"use when you already know the file path",
				"do not use to search for an unknown symbol",
				`{"path":"internal/tools/eino_tools.go","max_bytes":4096}`,
				"if the file is missing, confirm with repo_list or use repo_search",
			},
		},
		{
			name: "repo_search",
			requires: []string{
				"use when you know the symbol or string but not its location",
				"do not use when you already know which file to read",
				`{"query":"buildReadOnlyTools"}`,
				"if there are too many or no matches, refine the query or switch to repo_read",
			},
		},
		{
			name: "git_diff",
			requires: []string{
				"use when you need the current modified diff",
				"do not use it to understand untouched repository state",
				`{}`,
				"if the diff is empty, use repo_list, repo_read, or repo_search",
			},
		},
		{
			name: "kb_search",
			requires: []string{
				"use when you need external or KB context",
				"do not use it instead of inspecting repository code",
				`{"query":"rag pipeline glossary","top_k":5}`,
				"if kb_search has no hits or is unavailable, inspect the repo directly",
			},
		},
	}

	for _, tc := range cases {
		desc, ok := descs[tc.name]
		if !ok {
			t.Fatalf("missing description for tool %q", tc.name)
		}
		lower := strings.ToLower(desc)
		for _, want := range tc.requires {
			if !strings.Contains(lower, strings.ToLower(want)) {
				t.Fatalf("description for %s missing %q: %q", tc.name, want, desc)
			}
		}
	}
}

func TestRunCommandDescriptionIncludesUsageBoundaries(t *testing.T) {
	got, err := BuildCoderTools(t.TempDir(), nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildCoderTools: %v", err)
	}

	descs := toolDescriptions(t, got)
	desc, ok := descs["run_command"]
	if !ok {
		t.Fatalf("missing description for tool %q", "run_command")
	}

	lower := strings.ToLower(desc)
	requires := []string{
		"use when you need to inspect repository state with a safe command",
		"do not use it to read a known file or search for a known symbol",
		`{"command":"git status --short"}`,
		"if a command fails, read the output and then narrow or correct the command",
	}
	for _, want := range requires {
		if !strings.Contains(lower, strings.ToLower(want)) {
			t.Fatalf("description for run_command missing %q: %q", want, desc)
		}
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

func toolDescriptions(t *testing.T, items []tool.BaseTool) map[string]string {
	t.Helper()
	out := make(map[string]string, len(items))
	for _, item := range items {
		info, err := item.Info(context.Background())
		if err != nil {
			t.Fatalf("tool info: %v", err)
		}
		out[info.Name] = info.Desc
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

func TestRunCommandEmptyCommandDoesNotHardFail(t *testing.T) {
	got, err := BuildCoderTools(t.TempDir(), nil, NewRunner(), nil)
	if err != nil {
		t.Fatalf("BuildCoderTools: %v", err)
	}
	var runCommandTool tool.InvokableTool
	for _, item := range got {
		info, infoErr := item.Info(context.Background())
		if infoErr != nil || info == nil {
			continue
		}
		if info.Name != "run_command" {
			continue
		}
		inv, ok := item.(tool.InvokableTool)
		if !ok {
			t.Fatalf("run_command is not invokable")
		}
		runCommandTool = inv
		break
	}
	if runCommandTool == nil {
		t.Fatalf("run_command not found")
	}
	out, err := runCommandTool.InvokableRun(context.Background(), `{"command":""}`)
	if err != nil {
		t.Fatalf("run_command empty command should not return error: %v", err)
	}
	if !strings.Contains(strings.ToLower(out), "command is required") {
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

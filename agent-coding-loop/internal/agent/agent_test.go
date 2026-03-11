package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	kbpkg "github.com/kina/agent-coding-loop/internal/kb"
	"github.com/kina/agent-coding-loop/internal/model"
)

func containsString(items []string, want string) bool {
	for _, item := range items {
		if item == want {
			return true
		}
	}
	return false
}

func TestReviewerFallbackRequestsChangesOnFail(t *testing.T) {
	r := NewReviewer(ClientConfig{})
	out, err := r.Review(context.Background(), ReviewInput{Diff: "", CommandOutput: "FAIL\n---"})
	if err != nil {
		t.Fatalf("Review: %v", err)
	}
	if out.Decision != "request_changes" {
		t.Fatalf("expected request_changes, got %s", out.Decision)
	}
	if !out.UsedFallback {
		t.Fatal("expected UsedFallback=true for offline reviewer fallback")
	}
	if out.FallbackSource == "" {
		t.Fatal("expected non-empty FallbackSource")
	}
}

func TestReviewerFallbackApprovesOnPass(t *testing.T) {
	r := NewReviewer(ClientConfig{})
	out, err := r.Review(context.Background(), ReviewInput{Diff: "x", CommandOutput: "PASS"})
	if err != nil {
		t.Fatalf("Review: %v", err)
	}
	if out.Decision != "comment" {
		t.Fatalf("expected comment (fallback approve downgraded), got %s", out.Decision)
	}
	if !out.UsedFallback {
		t.Fatal("expected UsedFallback=true for offline reviewer fallback")
	}
	if out.FallbackSource == "" {
		t.Fatal("expected non-empty FallbackSource")
	}
	if !strings.Contains(strings.ToLower(out.Summary), "fallback reviewer cannot approve") {
		t.Fatalf("expected fallback approve guard note, got summary=%q", out.Summary)
	}
}

func TestCoderFallback(t *testing.T) {
	c := NewCoder(ClientConfig{BaseURL: "http://example.com", Model: "test-model"})
	out, err := c.Generate(context.Background(), CoderInput{Goal: "demo", PreviousReview: "fix"})
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if out.Summary == "" {
		t.Fatal("expected summary")
	}
	if !out.UsedFallback {
		t.Fatal("expected UsedFallback=true for offline coder fallback")
	}
	if out.FallbackSource == "" {
		t.Fatal("expected non-empty FallbackSource")
	}
	if len(out.Citations) != 0 {
		t.Fatalf("expected empty citations for fallback coder, got %v", out.Citations)
	}
}

func TestShouldBackfillCitations(t *testing.T) {
	if !shouldBackfillCitations("你必须先调用 kb_search 获取上下文，再修改代码。") {
		t.Fatalf("expected kb-required goal to enable citation backfill")
	}
	if shouldBackfillCitations("本轮为 No-RAG 基线，禁止调用 kb_search，只能基于仓库内容完成任务。") {
		t.Fatalf("expected no-rag goal to disable citation backfill")
	}
}

func TestFallbackCitationPaths(t *testing.T) {
	root := t.TempDir()
	kbDir := filepath.Join(root, "eval", "ab", "kb")
	if err := os.MkdirAll(kbDir, 0o755); err != nil {
		t.Fatalf("mkdir kb dir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(kbDir, "a.md"), []byte("a"), 0o644); err != nil {
		t.Fatalf("write a.md: %v", err)
	}
	if err := os.WriteFile(filepath.Join(kbDir, "b.md"), []byte("b"), 0o644); err != nil {
		t.Fatalf("write b.md: %v", err)
	}
	if err := os.WriteFile(filepath.Join(kbDir, "note.txt"), []byte("x"), 0o644); err != nil {
		t.Fatalf("write note.txt: %v", err)
	}

	paths := fallbackCitationPaths(root)
	if len(paths) != 2 {
		t.Fatalf("expected 2 markdown citations, got %d (%v)", len(paths), paths)
	}
	if paths[0] != "eval/ab/kb/a.md" || paths[1] != "eval/ab/kb/b.md" {
		t.Fatalf("unexpected citation paths: %v", paths)
	}
}

func TestEnsureCitationsUsesRetrievedContextBeforeKBSearch(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		http.NotFound(w, r)
	}))
	defer srv.Close()

	c := NewCoder(ClientConfig{}, WithKB(kbpkg.NewClient(srv.URL)))
	out := CoderOutput{}
	c.ensureCitations(context.Background(), CoderInput{
		Goal: "你必须先调用 kb_search 获取上下文，再修改代码。",
		RetrievedContext: []kbpkg.SearchHit{
			{ID: "h1", Path: "eval/ab/kb/rag_pipeline.md", Start: 1, End: 8, Text: "chunk"},
		},
	}, &out)

	if got := atomic.LoadInt32(&calls); got != 0 {
		t.Fatalf("expected no kb search when retrieved context exists, got %d calls", got)
	}
	if len(out.Citations) != 1 || out.Citations[0] != "eval/ab/kb/rag_pipeline.md" {
		t.Fatalf("expected citations from retrieved context, got %v", out.Citations)
	}
}

func TestEnsureCitationsFallsBackToKBSearchThenRepoSummary(t *testing.T) {
	t.Run("kb search", func(t *testing.T) {
		var calls int32
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/search" || r.Method != http.MethodPost {
				http.NotFound(w, r)
				return
			}
			atomic.AddInt32(&calls, 1)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"hits": []map[string]any{{"id": "h1", "path": "eval/ab/kb/api_conventions.md", "start": 10, "end": 20, "text": "structured errors"}},
			})
		}))
		defer srv.Close()

		c := NewCoder(ClientConfig{}, WithKB(kbpkg.NewClient(srv.URL)))
		out := CoderOutput{}
		c.ensureCitations(context.Background(), CoderInput{
			Goal: "你必须先调用 kb_search 获取上下文，再修改代码。",
		}, &out)

		if got := atomic.LoadInt32(&calls); got != 1 {
			t.Fatalf("expected one kb search call, got %d", got)
		}
		if len(out.Citations) != 1 || out.Citations[0] != "eval/ab/kb/api_conventions.md" {
			t.Fatalf("expected citations from kb search, got %v", out.Citations)
		}
	})

	t.Run("repo summary fallback", func(t *testing.T) {
		root := t.TempDir()
		kbDir := filepath.Join(root, "eval", "ab", "kb")
		if err := os.MkdirAll(kbDir, 0o755); err != nil {
			t.Fatalf("mkdir kb dir: %v", err)
		}
		if err := os.WriteFile(filepath.Join(kbDir, "fallback.md"), []byte("fallback"), 0o644); err != nil {
			t.Fatalf("write fallback.md: %v", err)
		}

		c := NewCoder(ClientConfig{})
		out := CoderOutput{}
		c.ensureCitations(context.Background(), CoderInput{
			Goal:        "你必须先调用 kb_search 获取上下文，再修改代码。",
			RepoSummary: root,
		}, &out)

		if len(out.Citations) != 1 || out.Citations[0] != "eval/ab/kb/fallback.md" {
			t.Fatalf("expected repo summary fallback citations, got %v", out.Citations)
		}
	})
}

func TestReviewerGoalTargetCoverage(t *testing.T) {
	r := NewReviewer(ClientConfig{})
	out, err := r.Review(context.Background(), ReviewInput{
		Goal:          "在 README.md 增加一行功能描述",
		Diff:          "diff --git a/docs/eino-agent-loop.md b/docs/eino-agent-loop.md\n--- a/docs/eino-agent-loop.md\n+++ b/docs/eino-agent-loop.md\n@@ -1 +1 @@",
		CommandOutput: "PASS",
	})
	if err != nil {
		t.Fatalf("Review: %v", err)
	}
	if out.Decision != "request_changes" {
		t.Fatalf("expected request_changes when goal target file is untouched, got %s", out.Decision)
	}
	if !strings.Contains(out.Summary, "README.md") {
		t.Fatalf("expected summary to mention missing README.md, got %q", out.Summary)
	}
}

func TestEnsureActionableFindingsAddsSyntheticFinding(t *testing.T) {
	out := ReviewOutput{
		Decision: "request_changes",
		Summary:  "Tests passed, but the implementation is incomplete.",
	}

	ensureActionableFindings(&out)

	if len(out.Findings) != 1 {
		t.Fatalf("expected synthetic finding, got %+v", out.Findings)
	}
	if out.Findings[0].Message != out.Summary {
		t.Fatalf("expected synthetic finding message from summary, got %+v", out.Findings[0])
	}
}

func TestEnsureActionableFindingsFillsEmptyMessages(t *testing.T) {
	out := ReviewOutput{
		Decision: "request_changes",
		Summary:  "Need to update the failing test case.",
		Findings: []model.ReviewFinding{{Severity: "", File: "internal/config/config_test.go", Line: 42, Message: ""}},
	}

	ensureActionableFindings(&out)

	if len(out.Findings) != 1 {
		t.Fatalf("expected one finding, got %+v", out.Findings)
	}
	if out.Findings[0].Severity != "high" {
		t.Fatalf("expected default severity, got %+v", out.Findings[0])
	}
	if out.Findings[0].Message != out.Summary {
		t.Fatalf("expected empty message filled from summary, got %+v", out.Findings[0])
	}
}

func TestReviewerPromptsIncludeKBScopeContract(t *testing.T) {
	in := ReviewInput{
		Goal:          "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。",
		RetrievalMode: model.RetrievalModePrefetch,
	}

	system, user := reviewerPrompts(in)

	if !strings.Contains(system, "kb_scope_contract") {
		t.Fatalf("expected reviewer system prompt to mention kb_scope_contract, got %q", system)
	}
	if !strings.Contains(system, "adjacent rules") {
		t.Fatalf("expected reviewer system prompt to forbid adjacent kb rules, got %q", system)
	}
	if !strings.Contains(user, "\"kb_scope_contract\"") {
		t.Fatalf("expected reviewer user payload to include kb_scope_contract, got %q", user)
	}
	if !strings.Contains(user, "\"writeErr\"") {
		t.Fatalf("expected reviewer user payload to carry writeErr identifier, got %q", user)
	}
}

func TestBuildSingleTargetFunctionConstraint(t *testing.T) {
	goal := "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。"

	got := buildSingleTargetFunctionConstraint(goal, []string{"internal/http/server.go"})

	if !strings.Contains(got, "writeErr") {
		t.Fatalf("expected constraint to mention writeErr, got %q", got)
	}
	if !strings.Contains(got, "do not change its signature") {
		t.Fatalf("expected constraint to forbid signature changes, got %q", got)
	}
	if !strings.Contains(got, "call sites") {
		t.Fatalf("expected constraint to forbid call-site changes, got %q", got)
	}
}

func TestCoderPromptsIncludeSingleTargetFunctionConstraint(t *testing.T) {
	in := CoderInput{
		Goal: "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。",
	}

	system, _ := coderPrompts(in)

	if !strings.Contains(system, "do not change its signature") {
		t.Fatalf("expected coder prompt to include single-function constraint, got %q", system)
	}
}

func TestBuildMinimalTestingConstraint(t *testing.T) {
	goal := "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验，同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。"
	constraint := buildMinimalTestingConstraint(goal, []string{"internal/config/config.go", "internal/config/config_test.go"})
	if !strings.Contains(constraint, "table-driven") || !strings.Contains(constraint, "one positive and one negative case") {
		t.Fatalf("expected minimal testing constraint, got %q", constraint)
	}
}

func TestCoderPromptsIncludeMinimalTestingConstraint(t *testing.T) {
	in := CoderInput{Goal: "在 internal/config/config.go 增加 DBPath 校验，并在 internal/config/config_test.go 中添加测试用例"}
	system, _ := coderPrompts(in)
	if !strings.Contains(system, "table-driven") || !strings.Contains(system, "one positive and one negative case") {
		t.Fatalf("expected coder prompt to include minimal testing constraint, got %q", system)
	}
}

func TestCoderPromptsForMultiTargetTaskRequireNonEmptyPatch(t *testing.T) {
	in := CoderInput{
		Goal: "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。",
	}

	system, _ := coderPrompts(in)

	if !strings.Contains(system, "valid answer must return a non-empty patch touching all target files") {
		t.Fatalf("expected multi-target coder prompt to forbid empty patch, got %q", system)
	}
}

func TestCoderPromptsForMultiTargetTaskRequirePerTargetPatchSections(t *testing.T) {
	in := CoderInput{
		Goal: "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。",
	}

	system, _ := coderPrompts(in)

	if !strings.Contains(system, "exactly one file patch section for each target file") {
		t.Fatalf("expected multi-target coder prompt to require one patch section per target, got %q", system)
	}
	if !strings.Contains(system, "exact repo-relative target file paths") {
		t.Fatalf("expected multi-target coder prompt to require exact repo-relative target paths, got %q", system)
	}
}

func TestCoderPromptsForMixedTaskPreferInlineEditsOverNewHelpers(t *testing.T) {
	in := CoderInput{
		Goal: "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。",
	}

	system, _ := coderPrompts(in)

	if !strings.Contains(system, "prefer inline edits to existing functions") {
		t.Fatalf("expected mixed-task prompt to prefer inline edits, got %q", system)
	}
	if !strings.Contains(system, "Do not introduce new top-level helpers") {
		t.Fatalf("expected mixed-task prompt to forbid new helpers by default, got %q", system)
	}
}

func TestBuildDefinitionIssueRecoveryConstraintMentionsExistingTestNames(t *testing.T) {
	in := CoderInput{
		DefinitionIssues:        []string{"duplicate test name: TestLoadValidatesDBPathSuffix"},
		ExistingTestNamesByFile: map[string][]string{"internal/config/config_test.go": {"TestLoadValidatesDBPathSuffix"}},
	}

	got := buildDefinitionIssueRecoveryConstraint(in, []string{"internal/config/config.go", "internal/config/config_test.go"})

	if !strings.Contains(got, "definition_issues") {
		t.Fatalf("expected recovery constraint to mention definition_issues, got %q", got)
	}
	if !strings.Contains(got, "existing_test_names_by_file") {
		t.Fatalf("expected recovery constraint to mention existing test names, got %q", got)
	}
	if !strings.Contains(got, "prefer extending existing table-driven tests") {
		t.Fatalf("expected recovery constraint to prefer extending existing tests, got %q", got)
	}
}

func TestReviewerPromptsIncludeMinimalTestingConstraint(t *testing.T) {
	in := ReviewInput{Goal: "在 internal/config/config.go 增加 DBPath 校验，并在 internal/config/config_test.go 中添加测试用例"}
	system, _ := reviewerPrompts(in)
	if !strings.Contains(system, "table-driven") || !strings.Contains(system, "one positive and one negative case") {
		t.Fatalf("expected reviewer prompt to include minimal testing constraint, got %q", system)
	}
}

func TestReviewerPromptsForValidationTaskForbidExtraAssertions(t *testing.T) {
	in := ReviewInput{
		Goal: "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。校验规则和错误信息需通过 kb_search 获取。",
	}

	system, _ := reviewerPrompts(in)

	if !strings.Contains(system, "do not require extra constants, helper names, or assertions") {
		t.Fatalf("expected reviewer prompt to forbid extra validation-specific assertions, got %q", system)
	}
}

func TestReviewerPromptsForMixedTaskPreferInlineEditsOverNewHelpers(t *testing.T) {
	in := ReviewInput{
		Goal: "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。校验规则和错误信息需通过 kb_search 获取。",
	}

	system, _ := reviewerPrompts(in)

	if !strings.Contains(system, "prefer inline edits to existing functions") {
		t.Fatalf("expected reviewer mixed-task prompt to prefer inline edits, got %q", system)
	}
	if !strings.Contains(system, "Do not introduce new top-level helpers") {
		t.Fatalf("expected reviewer mixed-task prompt to forbid new helpers by default, got %q", system)
	}
}

func TestReviewerPromptsIncludeSingleTargetFunctionConstraint(t *testing.T) {
	in := ReviewInput{
		Goal:          "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。",
		RetrievalMode: model.RetrievalModePrefetch,
	}

	system, _ := reviewerPrompts(in)

	if !strings.Contains(system, "do not require signature or call-site changes") {
		t.Fatalf("expected reviewer prompt to include single-function review constraint, got %q", system)
	}
}

func TestCoderPromptsDoNotMentionSkillTools(t *testing.T) {
	system, _ := coderPrompts(CoderInput{
		Goal: "仅基于仓库代码，在 README.md 的 CLI commands 列表中补充 inspect 命令的 --run-id 参数说明。",
	})

	if strings.Contains(system, "read skills") {
		t.Fatalf("expected coder prompt to stop mentioning skill reading, got %q", system)
	}
	if strings.Contains(system, "list_skills") || strings.Contains(system, "view_skill") {
		t.Fatalf("expected coder prompt to stop mentioning skill tools, got %q", system)
	}
}

func TestReviewerPromptsDoNotMentionSkillTools(t *testing.T) {
	system, _ := reviewerPrompts(ReviewInput{
		Goal: "仅基于仓库代码，在 README.md 的 CLI commands 列表中补充 inspect 命令的 --run-id 参数说明。",
	})

	if strings.Contains(system, "read skills") {
		t.Fatalf("expected reviewer prompt to stop mentioning skill reading, got %q", system)
	}
	if strings.Contains(system, "list_skills") || strings.Contains(system, "view_skill") {
		t.Fatalf("expected reviewer prompt to stop mentioning skill tools, got %q", system)
	}
}

func TestEnforceReorderOnlyReviewConsistencyDowngradesOutdatedGoalReading(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "tools", "eino_tools.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir tools dir: %v", err)
	}
	body := `package tools

func buildReadOnlyTools() []string {
	return []string{
		gitDiff,
		kbSearch,
		repoList,
		repoRead,
		repoSearch,
	}
}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write eino_tools.go: %v", err)
	}
	diff := `diff --git a/internal/tools/eino_tools.go b/internal/tools/eino_tools.go
--- a/internal/tools/eino_tools.go
+++ b/internal/tools/eino_tools.go
@@ -1,7 +1,7 @@
 	return []string{
-		repoList,
-		repoRead,
-		repoSearch,
-		gitDiff,
-		kbSearch,
+		gitDiff,
+		kbSearch,
+		repoList,
+		repoRead,
+		repoSearch,
 	}
 `
	out := ReviewOutput{
		Decision: "request_changes",
		Summary:  "The function still depends on kbSearch and must remove it.",
		Markdown: "Remove kbSearch from the return slice.",
		Findings: []model.ReviewFinding{{Severity: "medium", Message: "remove kbSearch"}},
	}
	in := ReviewInput{
		Goal:          "仅基于仓库代码，在 internal/tools/eino_tools.go 的 buildReadOnlyTools 函数中，将返回的工具列表按字母顺序排列（当前顺序是 repoList, repoRead, repoSearch, gitDiff, kbSearch, listSkillTool, viewSkillTool）。禁止调用 kb_search。",
		RepoRoot:      root,
		Diff:          diff,
		AppliedPatch:  diff,
		CommandOutput: "ok\tgithub.com/kina/agent-coding-loop/internal/tools\t0.42s",
	}

	enforceReorderOnlyReviewConsistency(in, &out)

	if out.Decision == "request_changes" {
		t.Fatalf("expected reorder-only review consistency to downgrade stale request_changes, got %+v", out)
	}
	if !strings.Contains(out.Summary, "sorted correctly") {
		t.Fatalf("expected summary to mention sorted reorder-only result, got %q", out.Summary)
	}
}

func TestExtractStatusFiles(t *testing.T) {
	status := " M README.md\n?? docs/eino-agent-loop.md\nR  old.md -> docs/new.md\n"
	files := extractStatusFiles(status)
	if _, ok := files["README.md"]; !ok {
		t.Fatalf("expected README.md from status, got %v", files)
	}
	if _, ok := files["docs/eino-agent-loop.md"]; !ok {
		t.Fatalf("expected docs/eino-agent-loop.md from status, got %v", files)
	}
	if _, ok := files["docs/new.md"]; !ok {
		t.Fatalf("expected rename target docs/new.md from status, got %v", files)
	}
}

func TestPatchTouchesAnyTarget(t *testing.T) {
	patch := "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-a\n+b\n"
	if !patchTouchesAnyTarget(patch, []string{"README.md"}) {
		t.Fatalf("expected patch to touch README.md")
	}
	if patchTouchesAnyTarget(patch, []string{"docs/eino-agent-loop.md"}) {
		t.Fatalf("did not expect patch to touch docs/eino-agent-loop.md")
	}
}

func TestPatchTouchesAllTargets(t *testing.T) {
	patch := "diff --git a/internal/config/config.go b/internal/config/config.go\n--- a/internal/config/config.go\n+++ b/internal/config/config.go\n@@ -1 +1 @@\n-a\n+b\n" +
		"diff --git a/internal/config/config_test.go b/internal/config/config_test.go\n--- a/internal/config/config_test.go\n+++ b/internal/config/config_test.go\n@@ -1 +1 @@\n-a\n+b\n"
	if !patchTouchesAllTargets(patch, []string{"internal/config/config.go", "internal/config/config_test.go"}) {
		t.Fatalf("expected patch to touch all target files")
	}
	if patchTouchesAllTargets(patch, []string{"internal/config/config.go", "internal/config/missing_test.go"}) {
		t.Fatalf("did not expect patch to satisfy all targets when one file is missing")
	}
}

func TestPatchTouchesOnlyTargets(t *testing.T) {
	patch := "diff --git a/internal/loop/processor.go b/internal/loop/processor.go\n--- a/internal/loop/processor.go\n+++ b/internal/loop/processor.go\n@@ -1 +1 @@\n-a\n+b\n"
	if !patchTouchesOnlyTargets(patch, []string{"internal/loop/processor.go"}) {
		t.Fatalf("expected patch to touch only internal/loop/processor.go")
	}
	patch2 := patch + "\ndiff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-a\n+b\n"
	if patchTouchesOnlyTargets(patch2, []string{"internal/loop/processor.go"}) {
		t.Fatalf("did not expect patch touching README.md to pass repo-only target constraint")
	}
}

func TestExtractChangedFilesCanonicalizesRepoPrefixedTargetPaths(t *testing.T) {
	patch := "diff --git a/agent-coding-loop/internal/config/config.go b/agent-coding-loop/internal/config/config.go\n" +
		"--- a/agent-coding-loop/internal/config/config.go\n" +
		"+++ b/agent-coding-loop/internal/config/config.go\n" +
		"@@ -1 +1 @@\n-a\n+b\n"

	changed := extractChangedFiles(patch, "internal/config/config.go")
	if _, ok := changed["internal/config/config.go"]; !ok {
		t.Fatalf("expected target-relative changed file, got %v", changed)
	}
	if _, ok := changed["agent-coding-loop/internal/config/config.go"]; ok {
		t.Fatalf("did not expect repo-prefixed key to remain, got %v", changed)
	}
}

func TestPatchTouchesTargetsCanonicalizesRepoPrefixedMultiTargetPaths(t *testing.T) {
	patch := "diff --git a/agent-coding-loop/internal/config/config.go b/agent-coding-loop/internal/config/config.go\n" +
		"--- a/agent-coding-loop/internal/config/config.go\n" +
		"+++ b/agent-coding-loop/internal/config/config.go\n" +
		"@@ -1 +1 @@\n-a\n+b\n" +
		"diff --git a/agent-coding-loop/internal/config/config_test.go b/agent-coding-loop/internal/config/config_test.go\n" +
		"--- a/agent-coding-loop/internal/config/config_test.go\n" +
		"+++ b/agent-coding-loop/internal/config/config_test.go\n" +
		"@@ -1 +1 @@\n-a\n+b\n"

	targets := []string{"internal/config/config.go", "internal/config/config_test.go"}
	if !patchTouchesTargets(patch, targets, true) {
		t.Fatalf("expected repo-prefixed multi-target patch to satisfy target coverage")
	}
	if !patchTouchesOnlyTargets(patch, targets) {
		t.Fatalf("expected repo-prefixed multi-target patch to satisfy target-only constraint")
	}
}

func TestPatchTouchesOnlyTargetsRejectsExtraFilesWhileStillTouchingTargets(t *testing.T) {
	patch := "diff --git a/agent-coding-loop/internal/config/config.go b/agent-coding-loop/internal/config/config.go\n" +
		"--- a/agent-coding-loop/internal/config/config.go\n" +
		"+++ b/agent-coding-loop/internal/config/config.go\n" +
		"@@ -1 +1 @@\n-a\n+b\n" +
		"diff --git a/agent-coding-loop/internal/config/config_test.go b/agent-coding-loop/internal/config/config_test.go\n" +
		"--- a/agent-coding-loop/internal/config/config_test.go\n" +
		"+++ b/agent-coding-loop/internal/config/config_test.go\n" +
		"@@ -1 +1 @@\n-a\n+b\n" +
		"diff --git a/README.md b/README.md\n" +
		"--- a/README.md\n" +
		"+++ b/README.md\n" +
		"@@ -1 +1 @@\n-a\n+b\n"

	targets := []string{"internal/config/config.go", "internal/config/config_test.go"}
	if !patchTouchesTargets(patch, targets, true) {
		t.Fatalf("expected patch to still touch all declared targets despite extra file")
	}
	if patchTouchesOnlyTargets(patch, targets) {
		t.Fatalf("did not expect extra README.md change to satisfy target-only constraint")
	}
}

func TestIsRepoOnlyGoal(t *testing.T) {
	if !isRepoOnlyGoal("仅基于仓库内容完成，禁止调用 kb_search。") {
		t.Fatalf("expected repo-only goal to be detected")
	}
	if isRepoOnlyGoal("你必须先调用 kb_search 获取上下文。") {
		t.Fatalf("did not expect kb-required goal to be detected as repo-only")
	}
}

func TestBuildRepoOnlyTargetSnapshots(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "README.md")
	if err := os.WriteFile(path, []byte("hello"), 0o644); err != nil {
		t.Fatalf("write README.md: %v", err)
	}
	got := buildRepoOnlyTargetSnapshots(root, []string{"README.md", "missing.md"})
	if got["README.md"] != "hello" {
		t.Fatalf("expected README.md snapshot, got %q", got["README.md"])
	}
	if !strings.Contains(got["missing.md"], "[repo_read_error]") {
		t.Fatalf("expected missing.md snapshot to contain repo_read_error, got %q", got["missing.md"])
	}
}

func TestEnforceKBSearchConsistencyDowngradesFalseMissingSignal(t *testing.T) {
	in := ReviewInput{
		Goal:          "在 docs/eino-agent-loop.md 新增术语说明，必须先通过 kb_search 获取上下文。",
		CommandOutput: "",
		KBSearchCalls: 1,
		RetrievalMode: model.RetrievalModePrefetch,
	}
	out := ReviewOutput{
		Decision: "request_changes",
		Summary:  "未按要求先调用 kb_search 获取上下文信息。",
		Markdown: "缺少 kb_search 调用证据。",
		Findings: nil,
	}
	enforceKBSearchConsistency(in, &out)
	if out.Decision != "comment" {
		t.Fatalf("expected decision downgraded to comment, got %s", out.Decision)
	}
	if !strings.Contains(strings.ToLower(out.Summary), "kb search evidence exists") {
		t.Fatalf("expected summary mention kb_search evidence, got %q", out.Summary)
	}
}

func TestEnforceKBSearchConsistencyKeepsRealFailures(t *testing.T) {
	in := ReviewInput{
		Goal:          "根据知识库规范修改 internal/http/server.go，必须先调用 kb_search。",
		CommandOutput: "build failed: error: duplicate import",
		KBSearchCalls: 1,
		RetrievalMode: model.RetrievalModePrefetch,
	}
	out := ReviewOutput{
		Decision: "request_changes",
		Summary:  "未按要求先调用 kb_search，且构建失败。",
		Markdown: "build error",
	}
	enforceKBSearchConsistency(in, &out)
	if out.Decision != "request_changes" {
		t.Fatalf("expected request_changes preserved when command fails, got %s", out.Decision)
	}
}

func TestEnforceMarkdownDuplicateReviewConsistencyDowngradesFalsePositive(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "docs", "eino-agent-loop.md")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir docs dir: %v", err)
	}
	body := `# Agent Loop

## RAG Pipeline Glossary

Chunking splits long text into retrieval units.

Embedding maps chunks into vectors.

Hybrid Search blends vector similarity and keyword matching.

Rerank reorders top-k candidates before generation.
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write markdown: %v", err)
	}

	in := ReviewInput{
		Goal:     "在 docs/eino-agent-loop.md 新增一个 `## RAG Pipeline Glossary` 小节。",
		RepoRoot: root,
	}
	out := ReviewOutput{
		Decision: "request_changes",
		Summary:  "The new `## RAG Pipeline Glossary` section is duplicated and appears twice back-to-back.",
		Markdown: "The glossary block is duplicated; keep only one copy.",
		Findings: []model.ReviewFinding{{
			Severity: "high",
			File:     "docs/eino-agent-loop.md",
			Message:  "duplicate glossary section",
		}},
	}

	enforceMarkdownDuplicateReviewConsistency(in, &out)

	if out.Decision != "comment" {
		t.Fatalf("expected duplicate false positive to downgrade to comment, got %+v", out)
	}
	if len(out.Findings) != 0 {
		t.Fatalf("expected duplicate finding removed, got %+v", out.Findings)
	}
	if strings.Contains(strings.ToLower(out.Summary), "duplicat") {
		t.Fatalf("expected duplicate wording removed from summary, got %q", out.Summary)
	}
}

func TestEnforceMarkdownDuplicateReviewConsistencyKeepsRealDuplicateHeading(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "docs", "eino-agent-loop.md")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir docs dir: %v", err)
	}
	body := `# Agent Loop

## RAG Pipeline Glossary

Chunking splits long text into retrieval units.

## RAG Pipeline Glossary

Chunking splits long text into retrieval units.
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write markdown: %v", err)
	}

	in := ReviewInput{
		Goal:     "在 docs/eino-agent-loop.md 新增一个 `## RAG Pipeline Glossary` 小节。",
		RepoRoot: root,
	}
	out := ReviewOutput{
		Decision: "request_changes",
		Summary:  "The new `## RAG Pipeline Glossary` section is duplicated and appears twice back-to-back.",
		Markdown: "The glossary block is duplicated; keep only one copy.",
		Findings: []model.ReviewFinding{{
			Severity: "high",
			File:     "docs/eino-agent-loop.md",
			Message:  "duplicate glossary section",
		}},
	}

	enforceMarkdownDuplicateReviewConsistency(in, &out)

	if out.Decision != "request_changes" {
		t.Fatalf("expected real duplicate to remain request_changes, got %+v", out)
	}
	if len(out.Findings) != 1 {
		t.Fatalf("expected duplicate finding kept, got %+v", out.Findings)
	}
}

func TestEnforceMarkdownDuplicateReviewConsistencyKeepsOtherFindings(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "docs", "eino-agent-loop.md")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir docs dir: %v", err)
	}
	body := `# Agent Loop

## RAG Pipeline Glossary

Chunking splits long text into retrieval units.

Embedding maps chunks into vectors.
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write markdown: %v", err)
	}

	in := ReviewInput{
		Goal:     "在 docs/eino-agent-loop.md 新增一个 `## RAG Pipeline Glossary` 小节。",
		RepoRoot: root,
	}
	out := ReviewOutput{
		Decision: "request_changes",
		Summary:  "The glossary section is duplicated, and the new section is missing citations.",
		Markdown: "Duplicate glossary section. Also missing citations.",
		Findings: []model.ReviewFinding{
			{Severity: "high", File: "docs/eino-agent-loop.md", Message: "duplicate glossary section"},
			{Severity: "high", File: "docs/eino-agent-loop.md", Message: "missing citation for glossary terms"},
		},
	}

	enforceMarkdownDuplicateReviewConsistency(in, &out)

	if out.Decision != "request_changes" {
		t.Fatalf("expected other findings to keep request_changes, got %+v", out)
	}
	if len(out.Findings) != 1 {
		t.Fatalf("expected duplicate finding removed and remaining finding preserved, got %+v", out.Findings)
	}
	if !strings.Contains(strings.ToLower(out.Findings[0].Message), "missing citation") {
		t.Fatalf("expected remaining finding to be the citation issue, got %+v", out.Findings)
	}
}

func TestMaybeAutoPatchSkipsRAGGlossary(t *testing.T) {
	root := t.TempDir()
	docPath := filepath.Join(root, "docs", "eino-agent-loop.md")
	if err := os.MkdirAll(filepath.Dir(docPath), 0o755); err != nil {
		t.Fatalf("mkdir docs: %v", err)
	}
	if err := os.WriteFile(docPath, []byte("# Title\n"), 0o644); err != nil {
		t.Fatalf("write doc: %v", err)
	}
	patch, ok := maybeAutoPatch(CoderInput{
		Goal:        "在 docs/eino-agent-loop.md 新增 ## RAG Pipeline Glossary 小节",
		RepoSummary: root,
	})
	if ok {
		t.Fatalf("expected glossary task to skip deterministic autopatch, got %q", patch)
	}
}

func TestMaybeAutoPatchSkipsKBChunkSizeValidation(t *testing.T) {
	root := t.TempDir()
	serverPath := filepath.Join(root, "kb", "server.py")
	if err := os.MkdirAll(filepath.Dir(serverPath), 0o755); err != nil {
		t.Fatalf("mkdir kb: %v", err)
	}
	serverBody := `class Handler:
    def handle(self, body):
        chunk_size = int(body.get("chunk_size") or 0)
        return chunk_size
`
	if err := os.WriteFile(serverPath, []byte(serverBody), 0o644); err != nil {
		t.Fatalf("write server.py: %v", err)
	}
	patch, ok := maybeAutoPatch(CoderInput{
		Goal:        "在 kb/server.py 增加 chunk_size 校验",
		RepoSummary: root,
	})
	if ok {
		t.Fatalf("expected kb chunk_size task to skip deterministic autopatch, got %q", patch)
	}
}

func TestMaybeAutoPatchSkipsDBPathValidation(t *testing.T) {
	root := t.TempDir()
	cfgPath := filepath.Join(root, "internal", "config", "config.go")
	if err := os.MkdirAll(filepath.Dir(cfgPath), 0o755); err != nil {
		t.Fatalf("mkdir config: %v", err)
	}
	cfgBody := `package config

import (
	"fmt"
	"strings"
)

type Config struct {
	DBPath string
}

func Load(path string) (*Config, error) {
	cfg := &Config{}
	return cfg, nil
}
`
	if err := os.WriteFile(cfgPath, []byte(cfgBody), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}
	testPath := filepath.Join(root, "internal", "config", "config_test.go")
	testBody := `package config

import "testing"

func TestLoadDefaults(t *testing.T) {}
`
	if err := os.WriteFile(testPath, []byte(testBody), 0o644); err != nil {
		t.Fatalf("write config_test.go: %v", err)
	}
	patch, ok := maybeAutoPatch(CoderInput{
		Goal:        "在 internal/config/config.go 增加 DBPath 校验，并修改 internal/config/config_test.go",
		RepoSummary: root,
	})
	if ok {
		t.Fatalf("expected DBPath task to skip deterministic autopatch, got %q", patch)
	}
}

func TestMaybeAutoPatchConfigValidation(t *testing.T) {
	root := t.TempDir()
	cfgPath := filepath.Join(root, "internal", "config", "config.go")
	if err := os.MkdirAll(filepath.Dir(cfgPath), 0o755); err != nil {
		t.Fatalf("mkdir config: %v", err)
	}
	cfgBody := `package config

import (
	"fmt"
	"strings"
)

type ModelConfig struct {
	BaseURL string
	Model   string
}

type Config struct {
	Model ModelConfig
}

func Load(path string) (*Config, error) {
	cfg := &Config{}
	return cfg, nil
}
`
	if err := os.WriteFile(cfgPath, []byte(cfgBody), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}
	patch, ok := maybeAutoPatch(CoderInput{
		Goal:        "在 internal/config/config.go 增加 base_url 和 model 的互斥校验",
		RepoSummary: root,
	})
	if !ok {
		t.Fatal("expected maybeAutoPatch to keep generic config validation fallback")
	}
	if !strings.Contains(patch, "model.base_url is required when model is set") {
		t.Fatalf("expected base_url/model validation in patch, got %q", patch)
	}
}

func TestCoderFallbackDoesNotApplyDeterministicAutoPatch(t *testing.T) {
	root := t.TempDir()
	docPath := filepath.Join(root, "docs", "eino-agent-loop.md")
	if err := os.MkdirAll(filepath.Dir(docPath), 0o755); err != nil {
		t.Fatalf("mkdir docs: %v", err)
	}
	if err := os.WriteFile(docPath, []byte("# Title\n"), 0o644); err != nil {
		t.Fatalf("write doc: %v", err)
	}

	c := NewCoder(ClientConfig{})
	out, err := c.Generate(context.Background(), CoderInput{
		Goal:        "在 docs/eino-agent-loop.md 新增 ## RAG Pipeline Glossary 小节",
		RepoSummary: root,
	})
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if strings.TrimSpace(out.Patch) != "" {
		t.Fatalf("expected fallback coder to leave patch empty, got %q", out.Patch)
	}
}

func TestEnsureGoalTargetPatchStillUsesDeterministicFallback(t *testing.T) {
	root := t.TempDir()
	cfgPath := filepath.Join(root, "internal", "config", "config.go")
	if err := os.MkdirAll(filepath.Dir(cfgPath), 0o755); err != nil {
		t.Fatalf("mkdir config: %v", err)
	}
	cfgBody := `package config

import (
	"fmt"
	"strings"
)

type ModelConfig struct {
	BaseURL string
	Model   string
}

type Config struct {
	Model ModelConfig
}

func Load(path string) (*Config, error) {
	cfg := &Config{}
	return cfg, nil
}
`
	if err := os.WriteFile(cfgPath, []byte(cfgBody), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}
	c := NewCoder(ClientConfig{})
	out := CoderOutput{}
	c.ensureGoalTargetPatch(context.Background(), CoderInput{
		Goal:        "在 internal/config/config.go 增加 base_url 和 model 的互斥校验",
		RepoSummary: root,
	}, &out)

	if !strings.Contains(out.Patch, "model.base_url is required when model is set") {
		t.Fatalf("expected generic config fallback inside ensureGoalTargetPatch, got %q", out.Patch)
	}
}

func TestDetectKBScopeCreepFlagsAdjacentValidation(t *testing.T) {
	goal := "根据项目知识库中的配置校验规范，在 internal/config/config.go 的 Load 函数末尾（return 之前）增加校验：如果 Model.APIKey 非空但 Model.BaseURL 为空，返回错误。校验规则和错误信息必须先通过 kb_search 查询获取，并在最终说明中给出引用路径。"
	patch := `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -10,4 +10,10 @@ func Load(path string) (*Config, error) {
+import "errors"
 if cfg.Model.APIKey != "" && cfg.Model.BaseURL == "" {
     return nil, errors.New("model.base_url is required when api_key is set")
 }
+if cfg.Model.Model == "" {
+    return nil, errors.New("model.model is required when base_url is set")
+}
 return cfg, nil
 }`

	got := detectKBScopeCreep(goal, patch, []string{"internal/config/config.go"})
	if len(got) == 0 {
		t.Fatal("expected scope creep to be detected")
	}
	if got[0] != "Model.Model" {
		t.Fatalf("expected Model.Model scope creep, got %v", got)
	}
}

func TestDetectKBScopeCreepAllowsCodeAndTestPair(t *testing.T) {
	goal := "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。校验规则和错误信息需通过 kb_search 获取。"
	patch := `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -20,3 +20,6 @@ func Load(path string) (*Config, error) {
+if !strings.HasSuffix(cfg.DBPath, ".db") {
+    return nil, errors.New("db_path must end with .db")
+}
 return cfg, nil
 }
diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,12 @@
+func TestLoadRejectsDBPathWithoutDBSuffix(t *testing.T) {
+    _, err := Load("bad.json")
+    if err == nil {
+        t.Fatal("expected error")
+    }
+}
 `

	got := detectKBScopeCreep(goal, patch, []string{"internal/config/config.go", "internal/config/config_test.go"})
	if len(got) != 0 {
		t.Fatalf("expected no scope creep for minimal code+test pair, got %v", got)
	}
}

func TestDetectKBScopeCreepIgnoresImportsAndComments(t *testing.T) {
	goal := "根据项目知识库中的配置校验规范，在 internal/config/config.go 的 Load 函数末尾（return 之前）增加校验：如果 Model.APIKey 非空但 Model.BaseURL 为空，返回错误。校验规则和错误信息必须先通过 kb_search 查询获取，并在最终说明中给出引用路径。"
	patch := `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,6 +1,10 @@
+import "errors"
+// BaseURL is required when APIKey is set.
 if cfg.Model.APIKey != "" && cfg.Model.BaseURL == "" {
     return nil, errors.New("model.base_url is required when api_key is set")
 }
 `

	got := detectKBScopeCreep(goal, patch, []string{"internal/config/config.go"})
	if len(got) != 0 {
		t.Fatalf("expected imports/comments to be ignored, got %v", got)
	}
}

func TestDetectKBScopeCreepFlagsAdjacentFunctionEdit(t *testing.T) {
	goal := "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。"
	patch := `diff --git a/internal/http/server.go b/internal/http/server.go
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -136,8 +136,15 @@ func writeJSON(w http.ResponseWriter, code int, payload any) {
+	requestID := uuid.NewString()
+	w.Header().Set("X-Request-Id", requestID)
 	w.Header().Set("Content-Type", "application/json")
 	w.WriteHeader(code)
 	_ = json.NewEncoder(w).Encode(payload)
 }
 
 func writeErr(w http.ResponseWriter, code int, msg string) {
-	writeJSON(w, code, map[string]any{"error": msg})
+	writeJSON(w, code, map[string]any{"error": msg, "code": "NOT_FOUND"})
 }`

	got := detectKBScopeCreep(goal, patch, []string{"internal/http/server.go"})
	if len(got) == 0 {
		t.Fatal("expected adjacent function edit to be detected")
	}
	if !containsString(got, "writeJSON") {
		t.Fatalf("expected writeJSON violation, got %v", got)
	}
}

func TestDetectKBScopeCreepAllowsSingleTargetFunctionEdit(t *testing.T) {
	goal := "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。"
	patch := `diff --git a/internal/http/server.go b/internal/http/server.go
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -142,3 +142,3 @@ func writeErr(w http.ResponseWriter, code int, msg string) {
-	writeJSON(w, code, map[string]any{"error": msg})
+	writeJSON(w, code, map[string]any{"error": msg, "code": "NOT_FOUND"})
 }`

	got := detectKBScopeCreep(goal, patch, []string{"internal/http/server.go"})
	if len(got) != 0 {
		t.Fatalf("expected single target function edit to pass, got %v", got)
	}
}

func TestBuildKBScopeContractIncludesGoalFunctionIdentifiers(t *testing.T) {
	goal := "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。"

	contract := buildKBScopeContract(goal, []string{"internal/http/server.go"})

	if !containsString(contract.Identifiers, "writeErr") {
		t.Fatalf("expected kb scope contract to include writeErr, got %v", contract.Identifiers)
	}
}

func TestDetectKBScopeCreepAllowsInlineHTTPStatusMappingInTargetFunction(t *testing.T) {
	goal := "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。"
	patch := `diff --git a/internal/http/server.go b/internal/http/server.go
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -142,3 +142,15 @@ func writeErr(w http.ResponseWriter, code int, msg string) {
+	errorCode := "INTERNAL_ERROR"
+	switch code {
+	case http.StatusBadRequest:
+		errorCode = "BAD_REQUEST"
+	case http.StatusMethodNotAllowed:
+		errorCode = "METHOD_NOT_ALLOWED"
+	case http.StatusNotFound:
+		errorCode = "NOT_FOUND"
+	}
-	writeJSON(w, code, map[string]any{"error": msg})
+	writeJSON(w, code, map[string]any{"error": msg, "code": errorCode})
 }`

	got := detectKBScopeCreep(goal, patch, []string{"internal/http/server.go"})
	if len(got) != 0 {
		t.Fatalf("expected inline status mapping inside writeErr to pass, got %v", got)
	}
}

func TestDetectTargetedPatchDefinitionIssuesFlagsSingleTargetHelper(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "http", "server.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir server dir: %v", err)
	}
	body := `package http

func toMachineCode(code int) string { return "OLD" }

func writeErr(code int, msg string) string {
	return msg
}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write server.go: %v", err)
	}
	patch := `diff --git a/internal/http/server.go b/internal/http/server.go
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -1,6 +1,13 @@
+func toMachineCode(code int) string {
+	return "NEW"
+}
+
 func writeErr(code int, msg string) string {
-	return msg
+	return toMachineCode(code) + ":" + msg
 }
`

	got := detectTargetedPatchDefinitionIssues(
		"根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。",
		root,
		patch,
		[]string{"internal/http/server.go"},
	)

	if !containsString(got, "duplicate helper definition: toMachineCode") {
		t.Fatalf("expected duplicate helper issue, got %v", got)
	}
}

func TestDetectTargetedPatchDefinitionIssuesFlagsDuplicateTestName(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir test dir: %v", err)
	}
	body := `package config

func TestLoadValidatesDBPathSuffix(t *testing.T) {}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write config_test.go: %v", err)
	}
	patch := `diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,7 @@
+func TestLoadValidatesDBPathSuffix(t *testing.T) {
+	t.Fatal("duplicate")
+}
 `

	got := detectTargetedPatchDefinitionIssues(
		"根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。校验规则和错误信息需通过 kb_search 获取。",
		root,
		patch,
		[]string{"internal/config/config.go", "internal/config/config_test.go"},
	)

	if !containsString(got, "duplicate test name: TestLoadValidatesDBPathSuffix") {
		t.Fatalf("expected duplicate test issue, got %v", got)
	}
}

func TestDetectTargetedPatchDefinitionIssuesFlagsDuplicatesWithinPatch(t *testing.T) {
	root := t.TempDir()
	goPath := filepath.Join(root, "internal", "config", "config.go")
	testPath := filepath.Join(root, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(goPath), 0o755); err != nil {
		t.Fatalf("mkdir config dir: %v", err)
	}
	if err := os.WriteFile(goPath, []byte("package config\n\nfunc Load(path string) (*Config, error) { return nil, nil }\n"), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}
	if err := os.WriteFile(testPath, []byte("package config\n\nimport \"testing\"\n"), 0o644); err != nil {
		t.Fatalf("write config_test.go: %v", err)
	}
	patch := `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,3 +1,11 @@
+func validateDBPath(path string) error { return nil }
+func validateDBPath(path string) error { return nil }
 diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,11 @@
+func TestValidateDBPath(t *testing.T) {}
+func TestValidateDBPath(t *testing.T) {}
`

	got := detectTargetedPatchDefinitionIssues(
		"根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。校验规则和错误信息需通过 kb_search 获取。",
		root,
		patch,
		[]string{"internal/config/config.go", "internal/config/config_test.go"},
	)

	if !containsString(got, "duplicate helper definition: validateDBPath") {
		t.Fatalf("expected duplicate helper issue, got %v", got)
	}
	if !containsString(got, "duplicate test name: TestValidateDBPath") {
		t.Fatalf("expected duplicate test issue, got %v", got)
	}
}

func TestDetectTargetedPatchDefinitionIssuesFlagsReorderOnlyIdentifierDrift(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "tools", "eino_tools.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir tools dir: %v", err)
	}
	body := `package tools

import "fmt"

func buildReadOnlyTools() []any {
	repoList := "repoList"
	repoRead := "repoRead"
	repoSearch := "repoSearch"
	gitDiff := "gitDiff"
	kbSearch := "kbSearch"
	_ = fmt.Sprintf("%s", repoList)
	return []any{
		repoList,
		repoRead,
		repoSearch,
		gitDiff,
		kbSearch,
	}
}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write eino_tools.go: %v", err)
	}
	patch := `diff --git a/internal/tools/eino_tools.go b/internal/tools/eino_tools.go
--- a/internal/tools/eino_tools.go
+++ b/internal/tools/eino_tools.go
@@ -1,5 +1,3 @@
-import "fmt"
-
@@ -10,6 +8,5 @@
 		repoRead,
 		repoSearch,
 		gitDiff,
-		kbSearch,
 	}
 }
`

	got := detectTargetedPatchDefinitionIssues(
		"仅基于仓库代码，在 internal/tools/eino_tools.go 的 buildReadOnlyTools 函数中，将返回的工具列表按字母顺序排列（当前顺序是 repoList, repoRead, repoSearch, gitDiff, kbSearch, listSkillTool, viewSkillTool）。禁止调用 kb_search。",
		root,
		patch,
		[]string{"internal/tools/eino_tools.go"},
	)

	if !containsString(got, "reorder-only identifier drift: kbSearch") {
		t.Fatalf("expected reorder-only identifier drift for kbSearch, got %v", got)
	}
	if len(got) != 1 {
		t.Fatalf("expected only kbSearch drift to be reported, got %v", got)
	}
}

func TestDetectTargetedPatchDefinitionIssuesFlagsDeletedRequiredMarkdownHeading(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "docs", "eino-agent-loop.md")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir docs dir: %v", err)
	}
	body := `# Agent Loop

## RAG Pipeline Glossary

Chunking ...
Embedding ...
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write markdown: %v", err)
	}
	patch := `diff --git a/docs/eino-agent-loop.md b/docs/eino-agent-loop.md
--- a/docs/eino-agent-loop.md
+++ b/docs/eino-agent-loop.md
@@ -3,4 +3,0 @@
-## RAG Pipeline Glossary
-
-Chunking ...
-Embedding ...
`

	got := detectTargetedPatchDefinitionIssues(
		"在 docs/eino-agent-loop.md 新增一个 `## RAG Pipeline Glossary` 小节，解释 Chunking、Embedding、Hybrid Search、Rerank 四个术语（每个 1 句）。",
		root,
		patch,
		[]string{"docs/eino-agent-loop.md"},
	)

	if !containsString(got, "deleted required heading without replacement: ## RAG Pipeline Glossary") {
		t.Fatalf("expected deleted heading issue, got %v", got)
	}
}

func TestDetectTargetedPatchDefinitionIssuesIgnoresNonEntryStringsForReorderOnly(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "tools", "eino_tools.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir tools dir: %v", err)
	}
	body := `package tools

func buildReadOnlyTools() []string {
	return []string{
		"repoList",
		"repoRead",
		"repoSearch",
		"gitDiff",
		"kbSearch",
	}
}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write eino_tools.go: %v", err)
	}
	patch := `diff --git a/internal/tools/eino_tools.go b/internal/tools/eino_tools.go
--- a/internal/tools/eino_tools.go
+++ b/internal/tools/eino_tools.go
@@ -1,8 +1,5 @@
-		"Search external knowledge base (LanceDB sidecar) for relevant context. Returns cited chunks with path and offsets.",
-		"github.com/kina/agent-coding-loop/internal/kb",
 		return []string{
 			"repoList",
 			"repoRead",
 			"repoSearch",
 			"gitDiff",
-			"kbSearch",
 		}
 `

	got := detectTargetedPatchDefinitionIssues(
		"仅基于仓库代码，在 internal/tools/eino_tools.go 的 buildReadOnlyTools 函数中，将返回的工具列表按字母顺序排列（当前顺序是 repoList, repoRead, repoSearch, gitDiff, kbSearch）。禁止调用 kb_search。",
		root,
		patch,
		[]string{"internal/tools/eino_tools.go"},
	)

	if len(got) != 1 || got[0] != "reorder-only identifier drift: kbSearch" {
		t.Fatalf("expected only kbSearch reorder drift, got %v", got)
	}
}

func TestEnsureGoalTargetPatchUsesRetryPatchForSingleTargetDoc(t *testing.T) {
	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targeted: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/docs/eino-agent-loop.md b/docs/eino-agent-loop.md
--- a/docs/eino-agent-loop.md
+++ b/docs/eino-agent-loop.md
@@ -1 +1,3 @@
+## RAG Pipeline Glossary
+
+- Chunking splits text into retrieval units.
`,
				Notes: "built from target snapshot",
			}, nil
		},
	}

	out := CoderOutput{}
	c.ensureGoalTargetPatch(context.Background(), CoderInput{
		Goal: "在 docs/eino-agent-loop.md 新增一个 `## RAG Pipeline Glossary` 小节。",
	}, &out)

	if !strings.Contains(out.Patch, "docs/eino-agent-loop.md") {
		t.Fatalf("expected retry patch to be preserved, got %q", out.Patch)
	}
	if !strings.Contains(out.Notes, "targeted_patch_retry") {
		t.Fatalf("expected retry diagnostics in notes, got %q", out.Notes)
	}
}

func TestEnsureGoalTargetPatchReportsEmptyRetryDiagnostics(t *testing.T) {
	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targeted: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: "", Notes: "snapshot already contains the glossary heading"}, nil
		},
		targetedStrict: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: "", Notes: "strict retry also found no missing lines"}, nil
		},
	}

	out := CoderOutput{}
	c.ensureGoalTargetPatch(context.Background(), CoderInput{
		Goal: "在 docs/eino-agent-loop.md 新增一个 `## RAG Pipeline Glossary` 小节。",
	}, &out)

	if !strings.Contains(out.Notes, "targeted_patch_retry returned empty patch") {
		t.Fatalf("expected targeted retry empty patch diagnostics, got %q", out.Notes)
	}
	if !strings.Contains(out.Notes, "targeted_strict_retry returned empty patch") {
		t.Fatalf("expected strict retry empty patch diagnostics, got %q", out.Notes)
	}
	if !strings.Contains(out.Notes, "snapshot already contains the glossary heading") {
		t.Fatalf("expected targeted retry note to survive, got %q", out.Notes)
	}
	if !strings.Contains(out.Notes, "strict retry also found no missing lines") {
		t.Fatalf("expected strict retry note to survive, got %q", out.Notes)
	}
}

func TestEnsureGoalTargetPatchRejectsEmptyPatchForMultiTargetTask(t *testing.T) {
	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targeted: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: "", Notes: "config.go and config_test.go already appear complete"}, nil
		},
		targetedStrict: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: "", Notes: "strict retry also believes both targets are already satisfied"}, nil
		},
	}

	out := CoderOutput{}
	c.ensureGoalTargetPatch(context.Background(), CoderInput{
		Goal: "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。",
	}, &out)

	if !strings.Contains(out.Notes, "empty patch is invalid for multi-target goal") {
		t.Fatalf("expected multi-target empty patch diagnostic, got %q", out.Notes)
	}
	if !strings.Contains(out.Notes, "Unable to produce patch touching required goal target files.") {
		t.Fatalf("expected final goal-target failure note, got %q", out.Notes)
	}
}

func TestEnsureGoalTargetPatchUsesStrictRetryPatchWhenFirstRetryEmpty(t *testing.T) {
	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targeted: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: "", Notes: "first retry could not build patch"}, nil
		},
		targetedStrict: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/kb/server.py b/kb/server.py
--- a/kb/server.py
+++ b/kb/server.py
@@ -1 +1,3 @@
+if chunk_size < 100 or chunk_size > 8192:
+    raise ValueError("chunk_size must be between 100 and 8192")
`,
				Notes: "strict retry built patch from exact snapshot",
			}, nil
		},
	}

	out := CoderOutput{}
	c.ensureGoalTargetPatch(context.Background(), CoderInput{
		Goal: "根据知识库规范，修改 kb/server.py 增加 chunk_size 校验。",
	}, &out)

	if !strings.Contains(out.Patch, "kb/server.py") {
		t.Fatalf("expected strict retry patch to be preserved, got %q", out.Patch)
	}
	if strings.Contains(out.Notes, "Unable to produce patch touching required goal target files.") {
		t.Fatalf("did not expect final failure note after strict retry success, got %q", out.Notes)
	}
	if !strings.Contains(out.Notes, "targeted_strict_retry") {
		t.Fatalf("expected strict retry diagnostics in notes, got %q", out.Notes)
	}
}

func TestEnsureGoalTargetPatchPassesMissingTargetFilesToStrictRetry(t *testing.T) {
	root := t.TempDir()
	configPath := filepath.Join(root, "internal", "config", "config.go")
	testPath := filepath.Join(root, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatalf("mkdir config dir: %v", err)
	}
	if err := os.WriteFile(configPath, []byte("package config\n\nfunc Load(path string) error {\n\treturn nil\n}\n"), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}
	if err := os.WriteFile(testPath, []byte("package config\n\nimport \"testing\"\n\nfunc TestLoadDefaults(t *testing.T) {}\n"), 0o644); err != nil {
		t.Fatalf("write config_test.go: %v", err)
	}

	c := NewCoder(ClientConfig{})
	var seenMissingTargets []string
	c.retryHooks = &coderRetryHooks{
		targeted: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: "", Notes: "first retry could not build missing test patch"}, nil
		},
		targetedStrict: func(_ context.Context, in CoderInput, targets []string, priorPatch string) (CoderOutput, error) {
			seenMissingTargets = append([]string{}, in.MissingTargetFiles...)
			return CoderOutput{
				Patch: `diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -3,3 +3,7 @@
 import "testing"
 
 func TestLoadDefaults(t *testing.T) {}
+
+func TestLoadRejectsInvalidDBPathSuffix(t *testing.T) {
+	t.Fatal("added")
+}
`,
				Notes: "strict retry focused on missing test target",
			}, nil
		},
	}

	out := CoderOutput{
		Patch: `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,5 +1,8 @@
 package config
 
 func Load(path string) error {
+	if path == "" {
+		return errors.New("db_path must end with .db")
+	}
 	return nil
 }
`,
	}

	c.ensureGoalTargetPatch(context.Background(), CoderInput{
		Goal:        "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。校验规则和错误信息需通过 kb_search 获取。",
		RepoSummary: root,
	}, &out)

	if !containsString(seenMissingTargets, "internal/config/config_test.go") {
		t.Fatalf("expected missing target files to include config_test.go, got %v", seenMissingTargets)
	}
	if containsString(seenMissingTargets, "internal/config/config.go") {
		t.Fatalf("expected already covered config.go to be excluded, got %v", seenMissingTargets)
	}
	if !strings.Contains(out.Patch, "diff --git a/internal/config/config.go b/internal/config/config.go") {
		t.Fatalf("expected merged patch to keep code target, got %q", out.Patch)
	}
	if !strings.Contains(out.Patch, "diff --git a/internal/config/config_test.go b/internal/config/config_test.go") {
		t.Fatalf("expected merged patch to add missing test target, got %q", out.Patch)
	}
	if !strings.Contains(out.Notes, "filled missing target files: internal/config/config_test.go") {
		t.Fatalf("expected missing target recovery note, got %q", out.Notes)
	}
}

func TestEnsureGoalTargetPatchReportsMissingTargetFilesWhenRecoveryStillMisses(t *testing.T) {
	root := t.TempDir()
	configPath := filepath.Join(root, "internal", "config", "config.go")
	testPath := filepath.Join(root, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatalf("mkdir config dir: %v", err)
	}
	if err := os.WriteFile(configPath, []byte("package config\n\nfunc Load(path string) error {\n\treturn nil\n}\n"), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}
	if err := os.WriteFile(testPath, []byte("package config\n\nimport \"testing\"\n\nfunc TestLoadDefaults(t *testing.T) {}\n"), 0o644); err != nil {
		t.Fatalf("write config_test.go: %v", err)
	}

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targeted: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: "", Notes: "first retry could not build missing test patch"}, nil
		},
		targetedStrict: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,5 +1,8 @@
 package config
 
 func Load(path string) error {
+	if path == "" {
+		return errors.New("db_path must end with .db")
+	}
 	return nil
 }
`,
				Notes: "strict retry still only touched config.go",
			}, nil
		},
	}

	out := CoderOutput{
		Patch: `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,5 +1,8 @@
 package config
 
 func Load(path string) error {
+	if path == "" {
+		return errors.New("db_path must end with .db")
+	}
 	return nil
 }
`,
	}

	c.ensureGoalTargetPatch(context.Background(), CoderInput{
		Goal:        "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。校验规则和错误信息需通过 kb_search 获取。",
		RepoSummary: root,
	}, &out)

	if !strings.Contains(out.Notes, "missing target files: internal/config/config_test.go") {
		t.Fatalf("expected missing target diagnostic, got %q", out.Notes)
	}
}

func TestEnsureGoalTargetPatchAppliesHardTimeoutToStrictRetry(t *testing.T) {
	oldTimeout := targetPatchHardRetryTimeout
	targetPatchHardRetryTimeout = 50 * time.Millisecond
	defer func() { targetPatchHardRetryTimeout = oldTimeout }()

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targeted: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: "", Notes: "first retry could not build patch"}, nil
		},
		targetedStrict: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			select {}
		},
	}

	out := CoderOutput{}
	done := make(chan struct{})
	go func() {
		c.ensureGoalTargetPatch(context.Background(), CoderInput{
			Goal: "根据知识库规范，修改 kb/server.py 增加 chunk_size 校验。",
		}, &out)
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(250 * time.Millisecond):
		t.Fatal("expected targeted strict retry to respect hard timeout")
	}
	if !strings.Contains(out.Notes, "targeted_strict_retry failed:") {
		t.Fatalf("expected strict retry timeout diagnostic, got %q", out.Notes)
	}
}

func TestDetectMissingTargetSnapshotContextFlagsHallucinatedGoDecls(t *testing.T) {
	root := t.TempDir()
	cfgPath := filepath.Join(root, "internal", "config", "config.go")
	testPath := filepath.Join(root, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(cfgPath), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(cfgPath, []byte(`package config

func Load(path string) (*Config, error) {
	return &Config{}, nil
}
`), 0o644); err != nil {
		t.Fatalf("write config: %v", err)
	}
	if err := os.WriteFile(testPath, []byte(`package config

import "testing"

func TestLoadDefaults(t *testing.T) {}
`), 0o644); err != nil {
		t.Fatalf("write test: %v", err)
	}
	patch := `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,3 +1,5 @@
 func (c *Config) Validate() error {
+    return nil
 }
diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,5 @@
 func TestConfigValidate(t *testing.T) {
+    t.Parallel()
 }`

	got := detectMissingTargetSnapshotContext(root, patch, []string{"internal/config/config.go", "internal/config/config_test.go"})
	if len(got) != 2 {
		t.Fatalf("expected 2 missing context issues, got %v", got)
	}
	if !containsString(got, "internal/config/config.go: func (c *Config) Validate() error {") {
		t.Fatalf("expected missing Validate context, got %v", got)
	}
	if !containsString(got, "internal/config/config_test.go: func TestConfigValidate(t *testing.T) {") {
		t.Fatalf("expected missing TestConfigValidate context, got %v", got)
	}
}

func TestEnsureGoalTargetPatchRejectsHallucinatedContextAndUsesRetryPatch(t *testing.T) {
	root := t.TempDir()
	cfgPath := filepath.Join(root, "internal", "config", "config.go")
	testPath := filepath.Join(root, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(cfgPath), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(cfgPath, []byte(`package config

func Load(path string) (*Config, error) {
	return &Config{}, nil
}
`), 0o644); err != nil {
		t.Fatalf("write config: %v", err)
	}
	if err := os.WriteFile(testPath, []byte(`package config

import "testing"

func TestLoadDefaults(t *testing.T) {}
`), 0o644); err != nil {
		t.Fatalf("write test: %v", err)
	}

	initialPatch := `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,3 +1,5 @@
 func (c *Config) Validate() error {
+    return nil
 }
diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,5 @@
 func TestConfigValidate(t *testing.T) {
+    t.Parallel()
 }`
	retryPatch := `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,3 +1,4 @@
 func Load(path string) (*Config, error) {
+    _ = path
 	return &Config{}, nil
 }
diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,4 +1,5 @@
 import "testing"
 func TestLoadDefaults(t *testing.T) {}
+func TestLoadDBPathValidation(t *testing.T) {}`

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targeted: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: retryPatch, Summary: "retry patch"}, nil
		},
	}
	out := CoderOutput{Patch: initialPatch}
	in := CoderInput{
		Goal:        "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。",
		RepoSummary: root,
	}

	c.ensureGoalTargetPatch(context.Background(), in, &out)

	if strings.TrimSpace(out.Patch) != strings.TrimSpace(retryPatch) {
		t.Fatalf("expected retry patch to replace hallucinated-context patch, got %q", out.Patch)
	}
	if !strings.Contains(out.Notes, "missing from snapshots") {
		t.Fatalf("expected missing snapshot context note, got %q", out.Notes)
	}
}

func TestEnsureSingleTargetOutputConstraintsUsesStrictRetryForDuplicateHelper(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "http", "server.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir server dir: %v", err)
	}
	body := `package http

func toMachineCode(code int) string { return "OLD" }

func writeErr(code int, msg string) string {
	return msg
}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write server.go: %v", err)
	}

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targetedStrict: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/internal/http/server.go b/internal/http/server.go
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -3,3 +3,3 @@
-	return msg
+	return "NOT_FOUND:" + msg
 `,
				Notes: "strict retry rewrote writeErr in place",
			}, nil
		},
	}

	out := CoderOutput{
		Patch: `diff --git a/internal/http/server.go b/internal/http/server.go
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -1,6 +1,13 @@
+func toMachineCode(code int) string {
+	return "NEW"
+}
+
 func writeErr(code int, msg string) string {
-	return msg
+	return toMachineCode(code) + ":" + msg
 }
`,
	}

	c.ensureSingleTargetOutputConstraints(context.Background(), CoderInput{
		Goal:        "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。",
		RepoSummary: root,
	}, &out)

	if strings.Contains(out.Patch, "func toMachineCode") {
		t.Fatalf("expected duplicate helper patch to be replaced, got %q", out.Patch)
	}
	if !strings.Contains(out.Notes, "single_target_patch_retry removed duplicate definition issues") {
		t.Fatalf("expected duplicate helper retry note, got %q", out.Notes)
	}
}

func TestEnsureSingleTargetOutputConstraintsUsesStrictRetryForDuplicateTestName(t *testing.T) {
	root := t.TempDir()
	configPath := filepath.Join(root, "internal", "config", "config.go")
	path := filepath.Join(root, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatalf("mkdir config dir: %v", err)
	}
	if err := os.WriteFile(configPath, []byte("package config\n\nfunc Load(path string) (*Config, error) { return nil, nil }\n"), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir test dir: %v", err)
	}
	body := `package config

func TestLoadValidatesDBPathSuffix(t *testing.T) {}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write config_test.go: %v", err)
	}

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targetedStrict: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,3 +1,6 @@
+if !strings.HasSuffix(cfg.DBPath, ".db") {
+	return nil, errors.New("db_path must end with .db")
+}
diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,8 @@
+func TestLoadRejectsInvalidDBPathSuffix(t *testing.T) {
+	t.Fatal("new unique name")
+}
 `,
				Notes: "strict retry renamed duplicate test",
			}, nil
		},
	}

	out := CoderOutput{
		Patch: `diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,7 @@
+func TestLoadValidatesDBPathSuffix(t *testing.T) {
+	t.Fatal("duplicate")
+}
 `,
	}

	c.ensureSingleTargetOutputConstraints(context.Background(), CoderInput{
		Goal:        "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。校验规则和错误信息需通过 kb_search 获取。",
		RepoSummary: root,
	}, &out)

	if strings.Contains(out.Patch, "func TestLoadValidatesDBPathSuffix") {
		t.Fatalf("expected duplicate test patch to be replaced, got %q", out.Patch)
	}
	if !strings.Contains(out.Patch, "TestLoadRejectsInvalidDBPathSuffix") {
		t.Fatalf("expected strict retry patch to survive, got %q", out.Patch)
	}
}

func TestEnsureSingleTargetOutputConstraintsPassesDefinitionIssueRecoveryContextToStrictRetry(t *testing.T) {
	root := t.TempDir()
	configPath := filepath.Join(root, "internal", "config", "config.go")
	testPath := filepath.Join(root, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatalf("mkdir config dir: %v", err)
	}
	if err := os.WriteFile(configPath, []byte("package config\n\nfunc Load(path string) (*Config, error) { return nil, nil }\n"), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}
	if err := os.WriteFile(testPath, []byte("package config\n\nfunc TestLoadValidatesDBPathSuffix(t *testing.T) {}\n"), 0o644); err != nil {
		t.Fatalf("write config_test.go: %v", err)
	}

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targetedStrict: func(_ context.Context, in CoderInput, targets []string, priorPatch string) (CoderOutput, error) {
			if !containsString(in.DefinitionIssues, "duplicate test name: TestLoadValidatesDBPathSuffix") {
				t.Fatalf("expected duplicate test definition issue, got %v", in.DefinitionIssues)
			}
			if !containsString(in.ExistingTestNamesByFile["internal/config/config_test.go"], "TestLoadValidatesDBPathSuffix") {
				t.Fatalf("expected existing test names in retry payload, got %#v", in.ExistingTestNamesByFile)
			}
			if !containsString(in.ExistingTopLevelNamesByFile["internal/config/config.go"], "Load") {
				t.Fatalf("expected existing top-level names in retry payload, got %#v", in.ExistingTopLevelNamesByFile)
			}
			if !containsString(in.AllowedGoalFunctions, "Load") {
				t.Fatalf("expected allowed goal functions in retry payload, got %v", in.AllowedGoalFunctions)
			}
			return CoderOutput{
				Patch: `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,3 +1,6 @@
+if !strings.HasSuffix(cfg.DBPath, ".db") {
+	return nil, errors.New("db_path must end with .db")
+}
diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,8 @@
+func TestLoadRejectsInvalidDBPathSuffix(t *testing.T) {
+	t.Fatal("new unique name")
+}
 `,
				Notes: "strict retry resolved duplicate test",
			}, nil
		},
	}

	out := CoderOutput{
		Patch: `diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,7 @@
+func TestLoadValidatesDBPathSuffix(t *testing.T) {
+	t.Fatal("duplicate")
+}
 `,
	}

	c.ensureSingleTargetOutputConstraints(context.Background(), CoderInput{
		Goal:        "根据知识库中的配置校验规则，修改 internal/config/config.go 中的 Load 函数增加 DBPath 以 .db 结尾的校验，并在 internal/config/config_test.go 中补充测试。必须先调用 kb_search。",
		RepoSummary: root,
	}, &out)

	if strings.Contains(out.Patch, "func TestLoadValidatesDBPathSuffix") {
		t.Fatalf("expected duplicate retry patch to be replaced, got %q", out.Patch)
	}
	if !strings.Contains(out.Patch, "TestLoadRejectsInvalidDBPathSuffix") {
		t.Fatalf("expected recovered retry patch to survive, got %q", out.Patch)
	}
}

func TestEnsureSingleTargetOutputConstraintsKeepsDuplicateIssueNotesWhenRetryStillConflicts(t *testing.T) {
	root := t.TempDir()
	configPath := filepath.Join(root, "internal", "config", "config.go")
	testPath := filepath.Join(root, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatalf("mkdir config dir: %v", err)
	}
	if err := os.WriteFile(configPath, []byte("package config\n\nfunc Load(path string) (*Config, error) { return nil, nil }\n"), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}
	if err := os.WriteFile(testPath, []byte("package config\n\nfunc TestLoadValidatesDBPathSuffix(t *testing.T) {}\n"), 0o644); err != nil {
		t.Fatalf("write config_test.go: %v", err)
	}

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targetedStrict: func(_ context.Context, in CoderInput, targets []string, priorPatch string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,3 +1,6 @@
+if !strings.HasSuffix(cfg.DBPath, ".db") {
+	return nil, errors.New("db_path must end with .db")
+}
diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,7 @@
+func TestLoadValidatesDBPathSuffix(t *testing.T) {
+	t.Fatal("duplicate again")
+}
 `,
				Notes: "retry kept duplicate test name",
			}, nil
		},
	}

	out := CoderOutput{
		Patch: `diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,7 @@
+func TestLoadValidatesDBPathSuffix(t *testing.T) {
+	t.Fatal("duplicate")
+}
 `,
	}

	c.ensureSingleTargetOutputConstraints(context.Background(), CoderInput{
		Goal:        "根据知识库中的配置校验规则，修改 internal/config/config.go 中的 Load 函数增加 DBPath 以 .db 结尾的校验，并在 internal/config/config_test.go 中补充测试。必须先调用 kb_search。",
		RepoSummary: root,
	}, &out)

	if !strings.Contains(out.Notes, "single_target_patch_retry still has definition issues: duplicate test name: TestLoadValidatesDBPathSuffix") {
		t.Fatalf("expected duplicate issue to remain in notes, got %q", out.Notes)
	}
}

func TestEnsureSingleTargetOutputConstraintsAppliesHardTimeoutToStrictRetry(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "http", "server.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir server dir: %v", err)
	}
	body := `package http

func toMachineCode(code int) string { return "OLD" }

func writeErr(code int, msg string) string {
	return msg
}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write server.go: %v", err)
	}

	oldTimeout := targetPatchHardRetryTimeout
	targetPatchHardRetryTimeout = 50 * time.Millisecond
	defer func() { targetPatchHardRetryTimeout = oldTimeout }()

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targetedStrict: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			select {}
		},
	}

	out := CoderOutput{
		Patch: `diff --git a/internal/http/server.go b/internal/http/server.go
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -1,6 +1,13 @@
+func toMachineCode(code int) string {
+	return "NEW"
+}
+
 func writeErr(code int, msg string) string {
-	return msg
+	return toMachineCode(code) + ":" + msg
 }
`,
	}

	done := make(chan struct{})
	go func() {
		c.ensureSingleTargetOutputConstraints(context.Background(), CoderInput{
			Goal:        "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段（code 为大写下划线格式的机器可读错误码）。需先调用 kb_search 查询 API 规范，并在说明中引用来源。",
			RepoSummary: root,
		}, &out)
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(250 * time.Millisecond):
		t.Fatal("expected strict retry to respect hard timeout")
	}
	if !strings.Contains(out.Notes, "single_target_patch_retry failed:") {
		t.Fatalf("expected timeout diagnostic in notes, got %q", out.Notes)
	}
}

func TestEnsureRepoOnlyMinimalModeUsesRetryPatchForSingleTargetDoc(t *testing.T) {
	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		repoOnly: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
+- inspect: supports --run-id for resuming a previous run
`,
				Notes: "repo-only retry built README patch from snapshot",
			}, nil
		},
	}

	out := CoderOutput{UsedFallback: true}
	c.ensureRepoOnlyMinimalMode(context.Background(), CoderInput{
		Goal:     "仅基于仓库代码，在 README.md 的 CLI commands 列表中补充 inspect 命令的 --run-id 参数说明。不要调用 kb_search。",
		Commands: []string{"test -f README.md"},
	}, &out)

	if !strings.Contains(out.Patch, "README.md") {
		t.Fatalf("expected repo-only retry patch to be preserved, got %q", out.Patch)
	}
	if !strings.Contains(out.Notes, "repo_only_retry") {
		t.Fatalf("expected repo-only retry diagnostics in notes, got %q", out.Notes)
	}
}

func TestEnsureRepoOnlyMinimalModeKeepsExistingValidPatch(t *testing.T) {
	called := 0
	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		repoOnly: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			called++
			return CoderOutput{}, nil
		},
	}

	out := CoderOutput{
		Patch: `diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
+- inspect: supports --run-id for resuming a previous run
`,
		UsedFallback: true,
	}
	c.ensureRepoOnlyMinimalMode(context.Background(), CoderInput{
		Goal:     "仅基于仓库代码，在 README.md 的 CLI commands 列表中补充 inspect 命令的 --run-id 参数说明。不要调用 kb_search。",
		Commands: []string{"test -f README.md"},
	}, &out)

	if called != 0 {
		t.Fatalf("expected existing valid patch to skip repo-only retry, got %d calls", called)
	}
	if !strings.Contains(out.Patch, "README.md") {
		t.Fatalf("expected existing patch to be preserved, got %q", out.Patch)
	}
}

func TestEnsureRepoOnlyMinimalModeReportsEmptyRetryDiagnostics(t *testing.T) {
	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		repoOnly: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: "", Notes: "snapshot already contains the inspect flag"}, nil
		},
	}

	out := CoderOutput{UsedFallback: true}
	c.ensureRepoOnlyMinimalMode(context.Background(), CoderInput{
		Goal:     "仅基于仓库代码，在 README.md 的 CLI commands 列表中补充 inspect 命令的 --run-id 参数说明。不要调用 kb_search。",
		Commands: []string{"test -f README.md"},
	}, &out)

	if !strings.Contains(out.Notes, "repo_only_retry returned empty patch") {
		t.Fatalf("expected repo-only empty patch diagnostics, got %q", out.Notes)
	}
	if !strings.Contains(out.Notes, "snapshot already contains the inspect flag") {
		t.Fatalf("expected repo-only retry note to survive, got %q", out.Notes)
	}
}

func TestEnsureRepoOnlyMinimalModePassesDefinitionIssueRecoveryContextToRetry(t *testing.T) {
	root := t.TempDir()
	configPath := filepath.Join(root, "internal", "config", "config.go")
	testPath := filepath.Join(root, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatalf("mkdir config dir: %v", err)
	}
	if err := os.WriteFile(configPath, []byte("package config\n\nfunc Load(path string) (*Config, error) { return nil, nil }\n"), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}
	if err := os.WriteFile(testPath, []byte("package config\n\nfunc TestLoadValidatesDBPathSuffix(t *testing.T) {}\n"), 0o644); err != nil {
		t.Fatalf("write config_test.go: %v", err)
	}

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		repoOnly: func(_ context.Context, in CoderInput, targets []string, priorPatch string) (CoderOutput, error) {
			if !containsString(in.DefinitionIssues, "duplicate test name: TestLoadValidatesDBPathSuffix") {
				t.Fatalf("expected duplicate test definition issue, got %v", in.DefinitionIssues)
			}
			if !containsString(in.ExistingTestNamesByFile["internal/config/config_test.go"], "TestLoadValidatesDBPathSuffix") {
				t.Fatalf("expected existing test names in repo-only retry payload, got %#v", in.ExistingTestNamesByFile)
			}
			return CoderOutput{
				Patch: `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,3 +1,6 @@
+if !strings.HasSuffix(cfg.DBPath, ".db") {
+	return nil, errors.New("db_path must end with .db")
+}
diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,8 @@
+func TestLoadRejectsInvalidDBPathSuffix(t *testing.T) {
+	t.Fatal("new unique name")
+}
 `,
				Notes: "repo-only retry resolved duplicate test",
			}, nil
		},
	}

	out := CoderOutput{
		Patch: `diff --git a/internal/config/config_test.go b/internal/config/config_test.go
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@ -1,3 +1,7 @@
+func TestLoadValidatesDBPathSuffix(t *testing.T) {
+	t.Fatal("duplicate")
+}
 `,
		UsedFallback: true,
	}

	c.ensureRepoOnlyMinimalMode(context.Background(), CoderInput{
		Goal:        "仅基于仓库代码，修改 internal/config/config.go 中的 Load 函数增加 DBPath 以 .db 结尾的校验，并在 internal/config/config_test.go 中补充测试。不要调用 kb_search。",
		RepoSummary: root,
		Commands:    []string{"go test ./internal/config/..."},
	}, &out)

	if strings.Contains(out.Patch, "func TestLoadValidatesDBPathSuffix") {
		t.Fatalf("expected duplicate retry patch to be replaced, got %q", out.Patch)
	}
	if !strings.Contains(out.Patch, "TestLoadRejectsInvalidDBPathSuffix") {
		t.Fatalf("expected repo-only recovered patch to survive, got %q", out.Patch)
	}
}

func TestEnsureRepoOnlyMinimalModeRejectsReorderOnlyIdentifierDrift(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "tools", "eino_tools.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir tools dir: %v", err)
	}
	body := `package tools

func buildReadOnlyTools() []string {
	return []string{
		"repoList",
		"repoRead",
		"repoSearch",
		"gitDiff",
		"kbSearch",
		"listSkillTool",
		"viewSkillTool",
	}
}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write eino_tools.go: %v", err)
	}

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		repoOnly: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/internal/tools/eino_tools.go b/internal/tools/eino_tools.go
--- a/internal/tools/eino_tools.go
+++ b/internal/tools/eino_tools.go
@@ -1,11 +1,10 @@
 func buildReadOnlyTools() []string {
 	return []string{
-		"repoList",
-		"repoRead",
-		"repoSearch",
 		"gitDiff",
-		"kbSearch",
+		"repoList",
+		"repoRead",
+		"repoSearch",
 		"listSkillTool",
 		"viewSkillTool",
 	}
 }
`,
				Notes: "reordered the slice alphabetically",
			}, nil
		},
	}

	out := CoderOutput{UsedFallback: true}
	c.ensureRepoOnlyMinimalMode(context.Background(), CoderInput{
		Goal:        "仅基于仓库代码，在 internal/tools/eino_tools.go 的 buildReadOnlyTools 函数中，将返回的工具列表按字母顺序排列（当前顺序是 repoList, repoRead, repoSearch, gitDiff, kbSearch, listSkillTool, viewSkillTool）。禁止调用 kb_search。",
		RepoSummary: root,
	}, &out)

	if !strings.Contains(out.Notes, "repo_only_retry definition issues: reorder-only identifier drift: kbSearch") {
		t.Fatalf("expected reorder-only drift diagnostic, got %q", out.Notes)
	}
	if !strings.Contains(out.Notes, "synthesized reorder-only patch from snapshots") {
		t.Fatalf("expected synthesized fallback note, got %q", out.Notes)
	}
	if !strings.Contains(out.Patch, "+\t\t\"kbSearch\",") {
		t.Fatalf("expected synthesized patch to preserve kbSearch, got %q", out.Patch)
	}
}

func TestEnsureRepoOnlyMinimalModeClearsUnsafeReorderOnlyPatch(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "tools", "eino_tools.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir tools dir: %v", err)
	}
	body := `package tools

func buildReadOnlyTools() []string {
	return []string{
		repoList,
		repoRead,
		repoSearch,
		gitDiff,
		kbSearch,
		listSkillTool,
		viewSkillTool,
	}
}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write eino_tools.go: %v", err)
	}

	badPatch := `diff --git a/internal/tools/eino_tools.go b/internal/tools/eino_tools.go
--- a/internal/tools/eino_tools.go
+++ b/internal/tools/eino_tools.go
@@ -1,11 +1,10 @@
 func buildReadOnlyTools() []string {
 	return []string{
-		repoList,
-		repoRead,
-		repoSearch,
 		gitDiff,
-		kbSearch,
+		repoList,
+		repoRead,
+		repoSearch,
 		listSkillTool,
 		viewSkillTool,
 	}
 }
`

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		repoOnly: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: badPatch,
				Notes: "reordered the slice alphabetically",
			}, nil
		},
	}

	out := CoderOutput{Patch: badPatch, UsedFallback: true}
	c.ensureRepoOnlyMinimalMode(context.Background(), CoderInput{
		Goal:        "仅基于仓库代码，在 internal/tools/eino_tools.go 的 buildReadOnlyTools 函数中，将返回的工具列表按字母顺序排列（当前顺序是 repoList, repoRead, repoSearch, gitDiff, kbSearch, listSkillTool, viewSkillTool）。禁止调用 kb_search。",
		RepoSummary: root,
	}, &out)

	if !strings.Contains(out.Patch, "+\t\tkbSearch,") {
		t.Fatalf("expected synthesized reorder-only patch to preserve kbSearch, got %q", out.Patch)
	}
	if !strings.Contains(out.Notes, "synthesized reorder-only patch from snapshots") {
		t.Fatalf("expected synthesis note, got %q", out.Notes)
	}
}

func TestEnsureSingleTargetOutputConstraintsClearsUnsafeReorderOnlyPatch(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "tools", "eino_tools.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir tools dir: %v", err)
	}
	body := `package tools

func buildReadOnlyTools() []string {
	return []string{
		repoList,
		repoRead,
		repoSearch,
		gitDiff,
		kbSearch,
	}
}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write eino_tools.go: %v", err)
	}

	badPatch := `diff --git a/internal/tools/eino_tools.go b/internal/tools/eino_tools.go
--- a/internal/tools/eino_tools.go
+++ b/internal/tools/eino_tools.go
@@ -1,9 +1,10 @@
 func buildReadOnlyTools() []string {
 	return []string{
-		repoList,
-		repoRead,
-		repoSearch,
 		gitDiff,
-		kbSearch,
+		listSkillTool,
+		repoList,
+		repoRead,
+		repoSearch,
+		viewSkillTool,
 	}
 }
`

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targetedStrict: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: ""}, nil
		},
	}

	out := CoderOutput{Patch: badPatch}
	c.ensureSingleTargetOutputConstraints(context.Background(), CoderInput{
		Goal:        "仅基于仓库代码，在 internal/tools/eino_tools.go 的 buildReadOnlyTools 函数中，将返回的工具列表按字母顺序排列（当前顺序是 repoList, repoRead, repoSearch, gitDiff, kbSearch, listSkillTool, viewSkillTool）。禁止调用 kb_search。",
		RepoSummary: root,
	}, &out)

	if strings.TrimSpace(out.Patch) != "" {
		t.Fatalf("expected unsafe single-target reorder-only patch to be cleared, got %q", out.Patch)
	}
	if !strings.Contains(out.Notes, "rejected unsafe reorder-only patch") {
		t.Fatalf("expected rejection note, got %q", out.Notes)
	}
}

func TestEnsureRepoOnlyMinimalModeSynthesizesReorderOnlyPatchFromSnapshots(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "internal", "tools", "eino_tools.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir tools dir: %v", err)
	}
	body := `package tools

func buildReadOnlyTools() []string {
	return []string{
		repoList,
		repoRead,
		repoSearch,
		gitDiff,
		kbSearch,
	}
}
`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write eino_tools.go: %v", err)
	}

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		repoOnly: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{Patch: ""}, nil
		},
	}

	out := CoderOutput{Patch: "", UsedFallback: true}
	c.ensureRepoOnlyMinimalMode(context.Background(), CoderInput{
		Goal:        "仅基于仓库代码，在 internal/tools/eino_tools.go 的 buildReadOnlyTools 函数中，将返回的工具列表按字母顺序排列（当前顺序是 repoList, repoRead, repoSearch, gitDiff, kbSearch, listSkillTool, viewSkillTool）。禁止调用 kb_search。",
		RepoSummary: root,
	}, &out)

	if !strings.Contains(out.Patch, "diff --git a/internal/tools/eino_tools.go b/internal/tools/eino_tools.go") {
		t.Fatalf("expected synthesized patch, got %q", out.Patch)
	}
	if !strings.Contains(out.Patch, "+\t\tgitDiff,") || !strings.Contains(out.Patch, "+\t\tkbSearch,") {
		t.Fatalf("expected alphabetical reorder entries in patch, got %q", out.Patch)
	}
	if strings.Contains(out.Patch, "listSkillTool") || strings.Contains(out.Patch, "viewSkillTool") {
		t.Fatalf("expected synthesized patch to use snapshot entries only, got %q", out.Patch)
	}
	if !strings.Contains(out.Notes, "synthesized reorder-only patch from snapshots") {
		t.Fatalf("expected synthesis note, got %q", out.Notes)
	}
}

func TestRetryStageRecorderEmitsSubstages(t *testing.T) {
	root := t.TempDir()
	readmePath := filepath.Join(root, "README.md")
	serverPath := filepath.Join(root, "internal", "http", "server.go")
	configPath := filepath.Join(root, "internal", "config", "config.go")
	if err := os.MkdirAll(filepath.Dir(serverPath), 0o755); err != nil {
		t.Fatalf("mkdir server dir: %v", err)
	}
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		t.Fatalf("mkdir config dir: %v", err)
	}
	if err := os.WriteFile(readmePath, []byte("# demo\n"), 0o644); err != nil {
		t.Fatalf("write readme: %v", err)
	}
	if err := os.WriteFile(serverPath, []byte("package http\n\nfunc writeErr(code int, msg string) string {\n\treturn msg\n}\n"), 0o644); err != nil {
		t.Fatalf("write server.go: %v", err)
	}
	if err := os.WriteFile(configPath, []byte("package config\n\nfunc Load(path string) (*Config, error) {\n\treturn &Config{}, nil\n}\n"), 0o644); err != nil {
		t.Fatalf("write config.go: %v", err)
	}

	var stages []string
	ctx := withAgentStageRecorder(context.Background(), func(stage string) {
		stages = append(stages, stage)
	})

	c := NewCoder(ClientConfig{})
	c.retryHooks = &coderRetryHooks{
		targeted: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/docs/eino-agent-loop.md b/docs/eino-agent-loop.md
--- a/docs/eino-agent-loop.md
+++ b/docs/eino-agent-loop.md
@@ -1 +1,2 @@
+glossary
`,
			}, nil
		},
		targetedStrict: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/internal/http/server.go b/internal/http/server.go
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -1,4 +1,4 @@
-func writeErr(code int, msg string) string {
+func writeErr(code int, msg string) string {
 	return "NOT_FOUND:" + msg
 }
`,
			}, nil
		},
		scopedStrict: func(context.Context, CoderInput, []string, string, []string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,4 +1,4 @@
 func Load(path string) (*Config, error) {
-	return &Config{}, nil
+	return nil, errors.New("model.base_url is required when api_key is set")
 }
`,
			}, nil
		},
		repoOnly: func(context.Context, CoderInput, []string, string) (CoderOutput, error) {
			return CoderOutput{
				Patch: `diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
+inspect docs
`,
			}, nil
		},
	}

	targetOut := CoderOutput{}
	c.ensureGoalTargetPatch(ctx, CoderInput{
		Goal: "在 docs/eino-agent-loop.md 新增一行 glossary。",
	}, &targetOut)

	scopeOut := CoderOutput{
		Patch: `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,4 +1,8 @@
 func Load(path string) (*Config, error) {
+if cfg.Model.APIKey != "" && cfg.Model.BaseURL == "" {
+    return nil, errors.New("model.base_url is required when api_key is set")
+}
+if cfg.Model.Model == "" {
+    return nil, errors.New("model.model is required when base_url is set")
+}
 	return &Config{}, nil
 }
`,
	}
	c.ensureKBTaskScope(ctx, CoderInput{
		Goal:        "根据项目知识库中的配置校验规范，在 internal/config/config.go 的 Load 函数末尾（return 之前）增加校验：如果 Model.APIKey 非空但 Model.BaseURL 为空，返回错误。校验规则和错误信息必须先通过 kb_search 查询获取，并在最终说明中给出引用路径。",
		RepoSummary: root,
	}, &scopeOut)

	singleTargetOut := CoderOutput{
		Patch: `diff --git a/internal/http/server.go b/internal/http/server.go
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -1,4 +1,8 @@
+func toMachineCode(code int) string { return "NEW" }
 func writeErr(code int, msg string) string {
 	return toMachineCode(code) + ":" + msg
 }
`,
	}
	c.ensureSingleTargetOutputConstraints(ctx, CoderInput{
		Goal:        "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段。",
		RepoSummary: root,
	}, &singleTargetOut)

	repoOnlyOut := CoderOutput{UsedFallback: true}
	c.ensureRepoOnlyMinimalMode(ctx, CoderInput{
		Goal:        "仅基于仓库代码，在 README.md 中补一行 inspect 说明。不要调用 kb_search。",
		RepoSummary: root,
	}, &repoOnlyOut)

	expected := []string{
		"coder_targeted_retry_start",
		"coder_targeted_retry_done",
		"coder_single_target_retry_start",
		"coder_single_target_retry_done",
		"coder_repo_only_retry_start",
		"coder_repo_only_retry_done",
	}
	for _, stage := range expected {
		if !containsString(stages, stage) {
			t.Fatalf("expected stage %q, got %v", stage, stages)
		}
	}
}

type emptyDiagnosticError struct{}

func (emptyDiagnosticError) Error() string { return "" }

func TestPatchAttemptDiagnosticIncludesUnwrappedCauseInNotes(t *testing.T) {
	err := fmt.Errorf("node wrapper: %w", errors.New("upstream 429"))
	note := patchAttemptDiagnostic("eino_generate", CoderOutput{}, err, nil, false, false, false)
	if !strings.Contains(note, "eino_generate failed:") {
		t.Fatalf("expected failure prefix, got %q", note)
	}
	if !strings.Contains(note, "node wrapper") {
		t.Fatalf("expected wrapped message, got %q", note)
	}
	if !strings.Contains(note, "upstream 429") {
		t.Fatalf("expected unwrapped cause, got %q", note)
	}
}

func TestPatchAttemptDiagnosticFormatsEmptyErrorBody(t *testing.T) {
	note := patchAttemptDiagnostic("client_completion", CoderOutput{}, emptyDiagnosticError{}, nil, false, false, false)
	if !strings.Contains(note, "client_completion failed:") {
		t.Fatalf("expected failure prefix, got %q", note)
	}
	if strings.HasSuffix(strings.TrimSpace(note), "failed:") {
		t.Fatalf("expected non-empty failure body, got %q", note)
	}
	if !strings.Contains(note, "empty model error") {
		t.Fatalf("expected empty model error fallback, got %q", note)
	}
}

func TestPatchAttemptDiagnosticMarksMultiTargetEmptyPatchInvalid(t *testing.T) {
	note := patchAttemptDiagnostic("targeted_patch_retry", CoderOutput{Notes: "claimed already satisfied"}, nil, []string{"internal/config/config.go", "internal/config/config_test.go"}, true, false, false)
	if !strings.Contains(note, "empty patch is invalid for multi-target goal") {
		t.Fatalf("expected multi-target invalid note, got %q", note)
	}
	if !strings.Contains(note, "claimed already satisfied") {
		t.Fatalf("expected retry note to survive, got %q", note)
	}
}

func TestWithCoderToolCallingTimeoutShortensLongParentContext(t *testing.T) {
	prev := coderToolCallingTimeout
	coderToolCallingTimeout = 50 * time.Millisecond
	defer func() { coderToolCallingTimeout = prev }()

	ctx, cancel := withCoderToolCallingTimeout(context.Background())
	defer cancel()

	deadline, ok := ctx.Deadline()
	if !ok {
		t.Fatal("expected deadline on coder tool-calling context")
	}
	remaining := time.Until(deadline)
	if remaining > 250*time.Millisecond {
		t.Fatalf("expected shortened coder deadline, got %s", remaining)
	}
}

func TestWithCoderToolCallingTimeoutPreservesShorterParentDeadline(t *testing.T) {
	prev := coderToolCallingTimeout
	coderToolCallingTimeout = 5 * time.Second
	defer func() { coderToolCallingTimeout = prev }()

	parent, cancelParent := context.WithTimeout(context.Background(), 40*time.Millisecond)
	defer cancelParent()
	ctx, cancel := withCoderToolCallingTimeout(parent)
	defer cancel()

	deadline, ok := ctx.Deadline()
	if !ok {
		t.Fatal("expected deadline on derived context")
	}
	remaining := time.Until(deadline)
	if remaining > 200*time.Millisecond {
		t.Fatalf("expected parent deadline to win, got %s", remaining)
	}
}

func TestWithReviewerTimeoutShortensLongParentContext(t *testing.T) {
	ctx, cancel := withReviewerTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	deadline, ok := ctx.Deadline()
	if !ok {
		t.Fatal("expected deadline on reviewer context")
	}
	remaining := time.Until(deadline)
	if remaining > 250*time.Millisecond {
		t.Fatalf("expected shortened reviewer deadline, got %s", remaining)
	}
}

func TestWithReviewerTimeoutPreservesShorterParentDeadline(t *testing.T) {
	parent, cancelParent := context.WithTimeout(context.Background(), 40*time.Millisecond)
	defer cancelParent()
	ctx, cancel := withReviewerTimeout(parent, 5*time.Second)
	defer cancel()

	deadline, ok := ctx.Deadline()
	if !ok {
		t.Fatal("expected deadline on derived reviewer context")
	}
	remaining := time.Until(deadline)
	if remaining > 200*time.Millisecond {
		t.Fatalf("expected parent reviewer deadline to win, got %s", remaining)
	}
}

func TestRunWithHardTimeoutReturnsDeadlineExceededWhenFnIgnoresContext(t *testing.T) {
	start := time.Now()
	_, err := runWithHardTimeout(context.Background(), 30*time.Millisecond, func(context.Context) (string, error) {
		time.Sleep(200 * time.Millisecond)
		return "late", nil
	})
	if err == nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected deadline exceeded, got %v", err)
	}
	if elapsed := time.Since(start); elapsed > 150*time.Millisecond {
		t.Fatalf("expected hard timeout to return quickly, got %s", elapsed)
	}
}

func TestRunWithHardTimeoutPreservesShorterParentDeadline(t *testing.T) {
	parent, cancelParent := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancelParent()
	start := time.Now()
	_, err := runWithHardTimeout(parent, time.Second, func(context.Context) (string, error) {
		time.Sleep(200 * time.Millisecond)
		return "late", nil
	})
	if err == nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected deadline exceeded, got %v", err)
	}
	if elapsed := time.Since(start); elapsed > 150*time.Millisecond {
		t.Fatalf("expected shorter parent deadline to win quickly, got %s", elapsed)
	}
}

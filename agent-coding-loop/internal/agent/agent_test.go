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
	c := NewCoder(ClientConfig{})
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

func TestReviewerPromptsIncludeMinimalTestingConstraint(t *testing.T) {
	in := ReviewInput{Goal: "在 internal/config/config.go 增加 DBPath 校验，并在 internal/config/config_test.go 中添加测试用例"}
	system, _ := reviewerPrompts(in)
	if !strings.Contains(system, "table-driven") || !strings.Contains(system, "one positive and one negative case") {
		t.Fatalf("expected reviewer prompt to include minimal testing constraint, got %q", system)
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

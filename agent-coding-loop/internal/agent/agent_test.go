package agent

import (
	"context"
	"encoding/json"
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

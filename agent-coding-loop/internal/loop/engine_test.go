package loop

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	agentpkg "github.com/kina/agent-coding-loop/internal/agent"
	gitpkg "github.com/kina/agent-coding-loop/internal/git"
	ghpkg "github.com/kina/agent-coding-loop/internal/github"
	kbpkg "github.com/kina/agent-coding-loop/internal/kb"
	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/skills"
	sqlite "github.com/kina/agent-coding-loop/internal/store/sqlite"
	"github.com/kina/agent-coding-loop/internal/tools"
)

func TestEngineRunDryRun(t *testing.T) {
	ctx := context.Background()
	repo := t.TempDir()

	r := tools.NewRunner()
	mustRun(t, r, repo, "git init")
	mustRun(t, r, repo, "git config user.email test@example.com")
	mustRun(t, r, repo, "git config user.name tester")
	if err := os.WriteFile(filepath.Join(repo, "README.md"), []byte("demo"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	mustRun(t, r, repo, "git add README.md")
	mustRun(t, r, repo, "git commit -m init")

	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	engine := NewEngine(EngineDeps{
		Store:      store,
		Runner:     r,
		Git:        gitpkg.NewClient(r),
		GitHub:     ghpkg.NewClient(r),
		Coder:      agentpkg.NewCoder(agentpkg.ClientConfig{}),
		Reviewer:   agentpkg.NewReviewer(agentpkg.ClientConfig{}),
		Skills:     skills.NewRegistry(nil),
		Artifacts:  filepath.Join(repo, ".agent-loop-artifacts"),
		DoomThresh: 3,
	})

	spec := model.RunSpec{
		Goal:          "validate repo",
		Repo:          repo,
		PRMode:        model.PRModeDryRun,
		MaxIterations: 2,
		Commands: model.CommandSet{
			Test: []string{"echo PASS"},
		},
	}
	result, err := engine.Run(ctx, spec)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if result.Status != model.RunStatusCompleted {
		t.Fatalf("expected completed, got %s", result.Status)
	}
	if result.RunID == "" {
		t.Fatal("expected run id")
	}
}

func TestEngineReviewerTimeoutFallsBackAndCompletesRun(t *testing.T) {
	ctx := context.Background()
	repo := t.TempDir()

	r := tools.NewRunner()
	mustRun(t, r, repo, "git init")
	mustRun(t, r, repo, "git config user.email test@example.com")
	mustRun(t, r, repo, "git config user.name tester")
	if err := os.WriteFile(filepath.Join(repo, "README.md"), []byte("demo"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	mustRun(t, r, repo, "git add README.md")
	mustRun(t, r, repo, "git commit -m init")

	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		select {
		case <-req.Context().Done():
			return
		case <-time.After(2 * time.Second):
			w.WriteHeader(http.StatusGatewayTimeout)
		}
	}))
	defer srv.Close()

	engine := NewEngine(EngineDeps{
		Store:           store,
		Runner:          r,
		Git:             gitpkg.NewClient(r),
		GitHub:          ghpkg.NewClient(r),
		Coder:           agentpkg.NewCoder(agentpkg.ClientConfig{}),
		Reviewer:        agentpkg.NewReviewer(agentpkg.ClientConfig{BaseURL: srv.URL, Model: "claude-haiku-4-5", APIKey: "x"}),
		Skills:          skills.NewRegistry(nil),
		Artifacts:       filepath.Join(repo, ".agent-loop-artifacts"),
		DoomThresh:      3,
		ReviewerTimeout: 50 * time.Millisecond,
	})

	spec := model.RunSpec{
		Goal:          "validate repo",
		Repo:          repo,
		PRMode:        model.PRModeDryRun,
		MaxIterations: 1,
		Commands: model.CommandSet{
			Test: []string{"echo PASS"},
		},
	}
	started := time.Now()
	result, err := engine.Run(ctx, spec)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if time.Since(started) > 5*time.Second {
		t.Fatalf("expected reviewer timeout fallback to finish quickly, took %s", time.Since(started))
	}
	if result.Status != model.RunStatusCompleted {
		t.Fatalf("expected completed after reviewer fallback, got %s summary=%q", result.Status, result.Summary)
	}
	run, err := store.GetRun(ctx, result.RunID)
	if err != nil {
		t.Fatalf("GetRun: %v", err)
	}
	if run.Status != string(model.RunStatusCompleted) {
		t.Fatalf("expected stored run status completed, got %s", run.Status)
	}
	events, err := store.GetRunEvents(ctx, result.RunID)
	if err != nil {
		t.Fatalf("GetRunEvents: %v", err)
	}
	joined := make([]string, 0, len(events))
	for _, ev := range events {
		joined = append(joined, ev.Summary)
	}
	if !strings.Contains(strings.Join(joined, "\n"), "reviewer_meta:completed") {
		t.Fatalf("expected reviewer_meta after timeout fallback, got %v", joined)
	}
}

func TestEnginePersistsStructuredMetaToolCalls(t *testing.T) {
	ctx := context.Background()
	repo := t.TempDir()

	r := tools.NewRunner()
	mustRun(t, r, repo, "git init")
	mustRun(t, r, repo, "git config user.email test@example.com")
	mustRun(t, r, repo, "git config user.name tester")
	if err := os.WriteFile(filepath.Join(repo, "README.md"), []byte("demo"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	mustRun(t, r, repo, "git add README.md")
	mustRun(t, r, repo, "git commit -m init")

	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	engine := NewEngine(EngineDeps{
		Store:      store,
		Runner:     r,
		Git:        gitpkg.NewClient(r),
		GitHub:     ghpkg.NewClient(r),
		Coder:      agentpkg.NewCoder(agentpkg.ClientConfig{}),
		Reviewer:   agentpkg.NewReviewer(agentpkg.ClientConfig{}),
		Skills:     skills.NewRegistry(nil),
		Artifacts:  filepath.Join(repo, ".agent-loop-artifacts"),
		DoomThresh: 3,
	})

	spec := model.RunSpec{
		Goal:          "validate repo",
		Repo:          repo,
		PRMode:        model.PRModeDryRun,
		MaxIterations: 2,
		Commands: model.CommandSet{
			Test: []string{"echo PASS"},
		},
	}
	result, err := engine.Run(ctx, spec)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	events, err := store.GetRunEvents(ctx, result.RunID)
	if err != nil {
		t.Fatalf("GetRunEvents: %v", err)
	}
	joined := make([]string, 0, len(events))
	for _, ev := range events {
		joined = append(joined, ev.Summary)
	}
	text := strings.Join(joined, "\n")
	if !strings.Contains(text, "coder_meta:completed") {
		t.Fatalf("expected coder_meta tool call in events, got %q", text)
	}
	if !strings.Contains(text, "reviewer_meta:completed") {
		t.Fatalf("expected reviewer_meta tool call in events, got %q", text)
	}
}

func TestEnginePersistsCoderMetaDiagnostics(t *testing.T) {
	ctx := context.Background()
	repo := t.TempDir()

	r := tools.NewRunner()
	mustRun(t, r, repo, "git init")
	mustRun(t, r, repo, "git config user.email test@example.com")
	mustRun(t, r, repo, "git config user.name tester")
	if err := os.WriteFile(filepath.Join(repo, "README.md"), []byte("demo"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	mustRun(t, r, repo, "git add README.md")
	mustRun(t, r, repo, "git commit -m init")

	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	engine := NewEngine(EngineDeps{
		Store:      store,
		Runner:     r,
		Git:        gitpkg.NewClient(r),
		GitHub:     ghpkg.NewClient(r),
		Coder:      agentpkg.NewCoder(agentpkg.ClientConfig{}),
		Reviewer:   agentpkg.NewReviewer(agentpkg.ClientConfig{}),
		Skills:     skills.NewRegistry(nil),
		Artifacts:  filepath.Join(repo, ".agent-loop-artifacts"),
		DoomThresh: 3,
	})

	spec := model.RunSpec{
		Goal:          "在 README.md 增加一行说明",
		Repo:          repo,
		PRMode:        model.PRModeDryRun,
		MaxIterations: 2,
		Commands: model.CommandSet{
			Test: []string{"echo PASS"},
		},
	}
	result, err := engine.Run(ctx, spec)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}

	query := "select output_text from tool_calls where run_id='" + result.RunID + "' and tool='coder_meta' order by id desc limit 1;"
	out, err := exec.Command("sqlite3", "-json", dbPath, query).Output()
	if err != nil {
		t.Fatalf("sqlite3 coder_meta query: %v", err)
	}
	var rows []struct {
		OutputText string `json:"output_text"`
	}
	if err := json.Unmarshal(out, &rows); err != nil {
		t.Fatalf("unmarshal sqlite json: %v; raw=%s", err, string(out))
	}
	if len(rows) != 1 {
		t.Fatalf("expected 1 coder_meta row, got %d raw=%s", len(rows), string(out))
	}
	var meta map[string]any
	if err := json.Unmarshal([]byte(rows[0].OutputText), &meta); err != nil {
		t.Fatalf("unmarshal coder_meta payload: %v; raw=%s", err, rows[0].OutputText)
	}
	if _, ok := meta["notes"].(string); !ok {
		t.Fatalf("expected notes diagnostic field, got %v", meta)
	}
	if _, ok := meta["patch_empty"].(bool); !ok {
		t.Fatalf("expected patch_empty diagnostic field, got %v", meta)
	}
	if _, ok := meta["patch_touches_target"].(bool); !ok {
		t.Fatalf("expected patch_touches_target diagnostic field, got %v", meta)
	}
	notes, _ := meta["notes"].(string)
	if strings.TrimSpace(notes) == "" {
		t.Fatalf("expected non-empty notes, got %v", meta)
	}
}

func TestEnginePersistsCoderStageToolCalls(t *testing.T) {
	ctx := context.Background()
	repo := t.TempDir()

	r := tools.NewRunner()
	mustRun(t, r, repo, "git init")
	mustRun(t, r, repo, "git config user.email test@example.com")
	mustRun(t, r, repo, "git config user.name tester")
	if err := os.WriteFile(filepath.Join(repo, "README.md"), []byte("demo\n"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	mustRun(t, r, repo, "git add README.md")
	mustRun(t, r, repo, "git commit -m init")

	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	coder := agentpkg.NewCoder(agentpkg.ClientConfig{})
	coder.SetRetryHooksForTests(agentpkg.CoderRetryHooksForTests{
		Targeted: func(context.Context, agentpkg.CoderInput, []string, string) (agentpkg.CoderOutput, error) {
			return agentpkg.CoderOutput{
				Patch: `diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
+inspect docs
`,
			}, nil
		},
	})

	engine := NewEngine(EngineDeps{
		Store:      store,
		Runner:     r,
		Git:        gitpkg.NewClient(r),
		GitHub:     ghpkg.NewClient(r),
		Coder:      coder,
		Reviewer:   agentpkg.NewReviewer(agentpkg.ClientConfig{}),
		Skills:     skills.NewRegistry(nil),
		Artifacts:  filepath.Join(repo, ".agent-loop-artifacts"),
		DoomThresh: 3,
	})

	spec := model.RunSpec{
		Goal:          "在 README.md 增加一行说明",
		Repo:          repo,
		PRMode:        model.PRModeDryRun,
		MaxIterations: 1,
		Commands: model.CommandSet{
			Test: []string{"echo PASS"},
		},
	}
	result, err := engine.Run(ctx, spec)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}

	query := "select output_text from tool_calls where run_id='" + result.RunID + "' and tool='coder_stage' order by id;"
	out, err := exec.Command("sqlite3", "-json", dbPath, query).Output()
	if err != nil {
		t.Fatalf("sqlite3 coder_stage query: %v", err)
	}
	var rows []struct {
		OutputText string `json:"output_text"`
	}
	if err := json.Unmarshal(out, &rows); err != nil {
		t.Fatalf("unmarshal sqlite json: %v; raw=%s", err, string(out))
	}
	if len(rows) == 0 {
		t.Fatalf("expected coder_stage rows, got raw=%s", string(out))
	}
	found := false
	for _, row := range rows {
		if row.OutputText == "coder_targeted_retry_start" {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected coder_targeted_retry_start in coder_stage rows, got %+v", rows)
	}
}

func TestEngineRunDryRunDoesNotRequireCommitIdentity(t *testing.T) {
	ctx := context.Background()
	repo := t.TempDir()

	r := tools.NewRunner()
	mustRun(t, r, repo, "git init")
	if err := os.WriteFile(filepath.Join(repo, "README.md"), []byte("demo"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	mustRun(t, r, repo, "git add README.md")
	mustRun(t, r, repo, "git commit -m init || true")

	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	engine := NewEngine(EngineDeps{
		Store:      store,
		Runner:     r,
		Git:        gitpkg.NewClient(r),
		GitHub:     ghpkg.NewClient(r),
		Coder:      agentpkg.NewCoder(agentpkg.ClientConfig{}),
		Reviewer:   agentpkg.NewReviewer(agentpkg.ClientConfig{}),
		Skills:     skills.NewRegistry(nil),
		Artifacts:  filepath.Join(repo, ".agent-loop-artifacts"),
		DoomThresh: 3,
	})

	spec := model.RunSpec{
		Goal:          "validate repo",
		Repo:          repo,
		PRMode:        model.PRModeDryRun,
		MaxIterations: 2,
		Commands: model.CommandSet{
			Test: []string{"echo PASS"},
		},
	}
	result, err := engine.Run(ctx, spec)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if result.Status != model.RunStatusCompleted {
		t.Fatalf("expected completed, got %s", result.Status)
	}
}

func TestEngineResumeRespectsMaxIterations(t *testing.T) {
	ctx := context.Background()
	repo := t.TempDir()

	r := tools.NewRunner()
	mustRun(t, r, repo, "git init")
	mustRun(t, r, repo, "git config user.email test@example.com")
	mustRun(t, r, repo, "git config user.name tester")
	if err := os.WriteFile(filepath.Join(repo, "README.md"), []byte("demo"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	mustRun(t, r, repo, "git add README.md")
	mustRun(t, r, repo, "git commit -m init")

	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	engine := NewEngine(EngineDeps{
		Store:      store,
		Runner:     r,
		Git:        gitpkg.NewClient(r),
		GitHub:     ghpkg.NewClient(r),
		Coder:      agentpkg.NewCoder(agentpkg.ClientConfig{}),
		Reviewer:   agentpkg.NewReviewer(agentpkg.ClientConfig{}),
		Skills:     skills.NewRegistry(nil),
		Artifacts:  filepath.Join(repo, ".agent-loop-artifacts"),
		DoomThresh: 3,
	})

	spec := model.RunSpec{
		Goal:          "validate repo",
		Repo:          repo,
		PRMode:        model.PRModeDryRun,
		MaxIterations: 2,
		Commands: model.CommandSet{
			Test: []string{"echo PASS"},
		},
	}
	runID, err := store.CreateRun(ctx, spec, model.RunStatusQueued)
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}

	now := time.Now().UnixMilli()
	if err := store.InsertStep(ctx, sqlite.StepRecord{
		RunID:     runID,
		Iteration: 1,
		Agent:     "reviewer",
		Decision:  string(model.LoopDecisionRequestChanges),
		Status:    string(model.RunStatusNeedsChange),
		StartedAt: now,
		EndedAt:   now,
	}); err != nil {
		t.Fatalf("InsertStep: %v", err)
	}
	if err := store.InsertStep(ctx, sqlite.StepRecord{
		RunID:     runID,
		Iteration: 2,
		Agent:     "reviewer",
		Decision:  string(model.LoopDecisionRequestChanges),
		Status:    string(model.RunStatusNeedsChange),
		StartedAt: now,
		EndedAt:   now,
	}); err != nil {
		t.Fatalf("InsertStep: %v", err)
	}

	result, err := engine.Resume(ctx, runID)
	if err != nil {
		t.Fatalf("Resume: %v", err)
	}
	if result.Status != model.RunStatusFailed {
		t.Fatalf("expected failed, got %s", result.Status)
	}
	if result.Summary != "max iterations reached" {
		t.Fatalf("expected max iterations reached, got %q", result.Summary)
	}
}

func TestFileCheckpointStorePersistsAcrossInstances(t *testing.T) {
	ctx := context.Background()
	dir := filepath.Join(t.TempDir(), "checkpoints")
	s1 := newFileCheckpointStore(dir)
	if _, ok, err := s1.Get(ctx, "missing"); err != nil || ok {
		t.Fatalf("expected missing checkpoint, ok=%v err=%v", ok, err)
	}
	if err := s1.Set(ctx, "run_1", []byte("abc")); err != nil {
		t.Fatalf("Set: %v", err)
	}
	s2 := newFileCheckpointStore(dir)
	b, ok, err := s2.Get(ctx, "run_1")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if !ok || string(b) != "abc" {
		t.Fatalf("expected abc, ok=%v got=%q", ok, string(b))
	}
}

func mustRun(t *testing.T, r *tools.Runner, repo, cmd string) {
	t.Helper()
	_, stderr, err := r.Run(context.Background(), cmd, repo)
	if err != nil {
		t.Fatalf("cmd failed: %s err=%v stderr=%s", cmd, err, stderr)
	}
}

func TestEngineKBPrefetchInsertsToolCallOnce(t *testing.T) {
	ctx := context.Background()
	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/search" || r.Method != http.MethodPost {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"hits": []map[string]any{
				{"id": "1", "path": "eval/ab/kb/rag_pipeline.md", "heading": "Glossary", "start": 0, "end": 42, "text": "chunking and rerank"},
			},
		})
	}))
	defer srv.Close()

	spec := model.RunSpec{
		Goal:          "根据知识库规范更新文档。",
		Repo:          t.TempDir(),
		PRMode:        model.PRModeDryRun,
		RetrievalMode: model.RetrievalModePrefetch,
		MaxIterations: 1,
	}
	runID, err := store.CreateRun(ctx, spec, model.RunStatusQueued)
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	e := NewEngine(EngineDeps{
		Store: store,
		KB:    kbpkg.NewClient(srv.URL),
	})
	st := &loopSession{
		RunID: runID,
		Spec:  spec,
	}
	e.maybePreflightKBSearch(ctx, st, 1)
	e.maybePreflightKBSearch(ctx, st, 2)

	events, err := store.GetRunEvents(ctx, runID)
	if err != nil {
		t.Fatalf("GetRunEvents: %v", err)
	}
	count := 0
	for _, ev := range events {
		if strings.Contains(ev.Summary, "retrieval_preflight:completed") {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("expected exactly one retrieval_preflight call, got %d", count)
	}
}

func TestEngineKBPrefetchSkipsOffMode(t *testing.T) {
	ctx := context.Background()
	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	spec := model.RunSpec{
		Goal:          "根据知识库规范更新文档。",
		Repo:          t.TempDir(),
		PRMode:        model.PRModeDryRun,
		RetrievalMode: model.RetrievalModeOff,
		MaxIterations: 1,
	}
	runID, err := store.CreateRun(ctx, spec, model.RunStatusQueued)
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	e := NewEngine(EngineDeps{
		Store: store,
	})
	st := &loopSession{
		RunID: runID,
		Spec:  spec,
	}
	e.maybePreflightKBSearch(ctx, st, 1)
	events, err := store.GetRunEvents(ctx, runID)
	if err != nil {
		t.Fatalf("GetRunEvents: %v", err)
	}
	for _, ev := range events {
		if strings.Contains(ev.Summary, "retrieval_preflight") {
			t.Fatalf("did not expect retrieval_preflight event when mode is off, got %q", ev.Summary)
		}
	}
}

func TestBuildCoderInputIncludesRetrievedContext(t *testing.T) {
	st := &loopSession{
		Spec: model.RunSpec{
			Goal:          "根据知识库规范更新文档。",
			RetrievalMode: model.RetrievalModePrefetch,
		},
		RepoAbs:        "/tmp/repo",
		PreviousReview: "fix this",
		CommandOutput:  "PASS",
		Commands:       model.CommandSet{Test: []string{"echo PASS"}},
		SkillsSummary:  "skill",
		RetrievedQuery: "review query",
		RetrievedHits: []kbpkg.SearchHit{
			{ID: "h1", Path: "eval/ab/kb/rag_pipeline.md", Start: 0, End: 12, Text: strings.Repeat("a", 520)},
			{ID: "h2", Path: "eval/ab/kb/api_conventions.md", Start: 13, End: 24, Text: strings.Repeat("b", 520)},
			{ID: "h3", Path: "eval/ab/kb/retrieval.md", Start: 25, End: 36, Text: strings.Repeat("c", 520)},
			{ID: "h4", Path: "eval/ab/kb/eval.md", Start: 37, End: 48, Text: strings.Repeat("d", 520)},
			{ID: "h5", Path: "eval/ab/kb/extra.md", Start: 49, End: 60, Text: strings.Repeat("e", 520)},
		},
	}
	got := buildCoderInput(st, "diff --git a/README.md b/README.md")
	if got.RetrievedQuery != "review query" {
		t.Fatalf("expected retrieved query, got %q", got.RetrievedQuery)
	}
	if len(got.RetrievedContext) != 4 {
		t.Fatalf("expected compacted hits, got %+v", got.RetrievedContext)
	}
	if got.RetrievedContext[0].ID != "h1" || got.RetrievedContext[3].ID != "h4" {
		t.Fatalf("expected first four hits preserved, got %+v", got.RetrievedContext)
	}
	if len(got.RetrievedContext[0].Text) != 500 {
		t.Fatalf("expected first hit text truncated to 500 chars, got %d", len(got.RetrievedContext[0].Text))
	}
	if got.RetrievedContext[0].Start != 0 || got.RetrievedContext[0].End != 12 {
		t.Fatalf("expected hit metadata preserved, got %+v", got.RetrievedContext[0])
	}
}

func TestBuildCoderInputDeduplicatesRetrievedContextByChunk(t *testing.T) {
	dupText := strings.Repeat("d", 640)
	st := &loopSession{
		Spec: model.RunSpec{Goal: "need kb", RetrievalMode: model.RetrievalModePrefetch},
		RetrievedHits: []kbpkg.SearchHit{
			{ID: "cfg-1", Path: "eval/ab/kb/config_validation.md", Start: 0, End: 900, Text: dupText},
			{ID: "cfg-dup", Path: "eval/ab/kb/config_validation.md", Start: 0, End: 900, Text: dupText},
			{ID: "plan-1", Path: "docs/2026-03-07-weaken-maybe-autopatch-plan.md", Start: 3120, End: 4020, Text: dupText},
			{ID: "plan-dup", Path: "docs/2026-03-07-weaken-maybe-autopatch-plan.md", Start: 3120, End: 4020, Text: dupText},
			{ID: "test-1", Path: "eval/ab/kb/testing_standards.md", Start: 0, End: 900, Text: dupText},
			{ID: "test-dup", Path: "eval/ab/kb/testing_standards.md", Start: 0, End: 900, Text: dupText},
		},
	}

	got := buildCoderInput(st, "")
	if len(got.RetrievedContext) != 3 {
		t.Fatalf("expected duplicate chunks removed, got %+v", got.RetrievedContext)
	}
	if got.RetrievedContext[0].Path != "eval/ab/kb/config_validation.md" || got.RetrievedContext[1].Path != "docs/2026-03-07-weaken-maybe-autopatch-plan.md" || got.RetrievedContext[2].Path != "eval/ab/kb/testing_standards.md" {
		t.Fatalf("expected unique chunks preserved in first-seen order, got %+v", got.RetrievedContext)
	}
	if len(got.RetrievedContext[2].Text) != 500 {
		t.Fatalf("expected deduplicated hit text truncated to 500 chars, got %d", len(got.RetrievedContext[2].Text))
	}
}

func TestBuildReviewInputIncludesRetrievedContext(t *testing.T) {
	st := &loopSession{
		Spec: model.RunSpec{
			Goal:          "根据知识库规范更新文档。",
			RetrievalMode: model.RetrievalModePrefetch,
		},
		RepoAbs:        "/tmp/repo",
		CommandOutput:  "PASS",
		SkillsSummary:  "skill",
		KBSearchCalls:  2,
		RetrievedQuery: "review query",
		RetrievedHits: []kbpkg.SearchHit{
			{ID: "h1", Path: "eval/ab/kb/rag_pipeline.md", Start: 0, End: 12, Text: strings.Repeat("a", 520)},
			{ID: "h2", Path: "eval/ab/kb/api_conventions.md", Start: 13, End: 24, Text: strings.Repeat("b", 520)},
		},
	}
	got := buildReviewInput(st, "diff --git a/README.md b/README.md", "M README.md", "patch", "PASS")
	if got.RetrievedQuery != "review query" {
		t.Fatalf("expected retrieved query, got %q", got.RetrievedQuery)
	}
	if got.KBSearchCalls != 2 {
		t.Fatalf("expected kb search calls forwarded, got %d", got.KBSearchCalls)
	}
	if len(got.RetrievedContext) != 2 || got.RetrievedContext[0].ID != "h1" {
		t.Fatalf("expected compacted retrieved hits, got %+v", got.RetrievedContext)
	}
	if len(got.RetrievedContext[0].Text) != 500 {
		t.Fatalf("expected review hit text truncated to 500 chars, got %d", len(got.RetrievedContext[0].Text))
	}
	if got.RetrievedContext[0].Path != "eval/ab/kb/rag_pipeline.md" {
		t.Fatalf("expected review hit metadata preserved, got %+v", got.RetrievedContext[0])
	}
}

func TestMaybeRefreshCoderContextFetchesFollowupHits(t *testing.T) {
	ctx := context.Background()
	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/search" || r.Method != http.MethodPost {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"hits": []map[string]any{{"id": "coder-refresh-1", "path": "docs/eino-agent-loop.md", "heading": "RAG", "start": 5, "end": 15, "text": "follow-up context"}},
		})
	}))
	defer srv.Close()

	e := NewEngine(EngineDeps{Store: store, KB: kbpkg.NewClient(srv.URL)})
	st := &loopSession{
		RunID:          "run_demo",
		Spec:           model.RunSpec{Goal: "根据知识库规范更新 docs/eino-agent-loop.md", RetrievalMode: model.RetrievalModePrefetch},
		Decision:       model.LoopDecisionRequestChanges,
		PreviousReview: "请根据知识库补充 docs/eino-agent-loop.md 中的 RAG 定义。",
		RetrievedQuery: "根据知识库规范更新 docs/eino-agent-loop.md",
		RetrievedHits:  []kbpkg.SearchHit{{ID: "base-1", Path: "eval/ab/kb/rag_pipeline.md", Start: 1, End: 4, Text: "base"}},
	}
	got, refreshed := e.maybeRefreshCoderContext(ctx, st, 2, buildCoderInput(st, "diff --git a/docs/eino-agent-loop.md b/docs/eino-agent-loop.md"))
	if !refreshed {
		t.Fatal("expected coder context refresh")
	}
	if got.RetrievedQuery == "" || !strings.Contains(got.RetrievedQuery, "coder follow-up") {
		t.Fatalf("expected coder retrieval follow-up query, got %q", got.RetrievedQuery)
	}
	if got.RetrievedContext[0].ID != "base-1" || len(got.RetrievedContext) != 2 || got.RetrievedContext[1].ID != "coder-refresh-1" {
		t.Fatalf("expected merged retrieved hits, got %+v", got.RetrievedContext)
	}
	if st.KBSearchCalls != 1 {
		t.Fatalf("expected kb search calls incremented, got %d", st.KBSearchCalls)
	}
	if st.RetrievedQuery == "根据知识库规范更新 docs/eino-agent-loop.md" {
		t.Fatalf("expected stored retrieved query updated, got %q", st.RetrievedQuery)
	}
	if len(st.RetrievedHits) != 2 || st.RetrievedHits[1].ID != "coder-refresh-1" {
		t.Fatalf("expected session hits merged, got %+v", st.RetrievedHits)
	}
}

func TestMaybeRefreshCoderContextSkipsWhenNotNeeded(t *testing.T) {
	ctx := context.Background()
	e := NewEngine(EngineDeps{})
	base := &loopSession{
		Spec:           model.RunSpec{Goal: "根据知识库规范更新文档", RetrievalMode: model.RetrievalModePrefetch},
		Decision:       model.LoopDecisionRequestChanges,
		PreviousReview: "需要补充上下文",
		RetrievedQuery: "base query",
		RetrievedHits:  []kbpkg.SearchHit{{ID: "base-1", Path: "eval/ab/kb/rag_pipeline.md", Start: 1, End: 4, Text: "base"}},
	}
	cases := []struct {
		name      string
		iteration int
		mutate    func(*loopSession)
	}{
		{name: "first iteration", iteration: 1},
		{name: "mode off", iteration: 2, mutate: func(st *loopSession) { st.Spec.RetrievalMode = model.RetrievalModeOff }},
		{name: "no previous review", iteration: 2, mutate: func(st *loopSession) { st.PreviousReview = "" }},
		{name: "previous decision not request changes", iteration: 2, mutate: func(st *loopSession) { st.Decision = model.LoopDecisionComplete }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			stCopy := *base
			stCopy.RetrievedHits = append([]kbpkg.SearchHit(nil), base.RetrievedHits...)
			if tc.mutate != nil {
				tc.mutate(&stCopy)
			}
			in := buildCoderInput(&stCopy, "diff --git a/docs/eino-agent-loop.md b/docs/eino-agent-loop.md")
			got, refreshed := e.maybeRefreshCoderContext(ctx, &stCopy, tc.iteration, in)
			if refreshed {
				t.Fatalf("did not expect coder context refresh")
			}
			if got.RetrievedQuery != in.RetrievedQuery {
				t.Fatalf("expected retrieved query unchanged, got %q want %q", got.RetrievedQuery, in.RetrievedQuery)
			}
			if len(got.RetrievedContext) != len(in.RetrievedContext) {
				t.Fatalf("expected retrieved context unchanged, got %+v want %+v", got.RetrievedContext, in.RetrievedContext)
			}
		})
	}
}

func TestMaybeRefreshReviewerContextFetchesSupplementalHits(t *testing.T) {
	ctx := context.Background()
	dbPath := filepath.Join(t.TempDir(), "state.db")
	store, err := sqlite.New(dbPath)
	if err != nil {
		t.Fatalf("sqlite.New: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/search" || r.Method != http.MethodPost {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"hits": []map[string]any{
				{"id": "refresh-1", "path": "eval/ab/kb/api_conventions.md", "heading": "Errors", "start": 10, "end": 20, "text": "return structured errors"},
			},
		})
	}))
	defer srv.Close()

	e := NewEngine(EngineDeps{
		Store: store,
		KB:    kbpkg.NewClient(srv.URL),
	})
	st := &loopSession{
		RunID: "run_demo",
		Spec: model.RunSpec{
			Goal:          "根据知识库规范修改 HTTP API。",
			RetrievalMode: model.RetrievalModePrefetch,
		},
	}
	in := agentpkg.ReviewInput{
		Goal:          st.Spec.Goal,
		RepoRoot:      "/tmp/repo",
		Diff:          "diff --git a/internal/http/server.go b/internal/http/server.go",
		CommandOutput: "PASS",
		RetrievalMode: model.RetrievalModePrefetch,
	}
	out := agentpkg.ReviewOutput{
		Decision: "request_changes",
		Summary:  "Required kb_search call evidence missing for this KB task.",
		Markdown: "missing kb_search",
	}
	got, refreshed := e.maybeRefreshReviewerContext(ctx, st, 1, in, out)
	if !refreshed {
		t.Fatal("expected reviewer context refresh")
	}
	if got.KBSearchCalls != 1 {
		t.Fatalf("expected kb search calls incremented, got %d", got.KBSearchCalls)
	}
	if got.RetrievedQuery == "" {
		t.Fatal("expected reviewer retrieval query")
	}
	if len(got.RetrievedContext) != 1 || got.RetrievedContext[0].ID != "refresh-1" {
		t.Fatalf("expected supplemental hits, got %+v", got.RetrievedContext)
	}
}

func TestKBSearchQueryFromGoalIncludesTestingHintForTestTasks(t *testing.T) {
	goal := "根据知识库中的配置校验规则，在 internal/config/config.go 中增加校验：DBPath 必须以 .db 结尾，否则返回错误。同时在 internal/config/config_test.go 中添加一个测试用例验证该校验。校验规则和错误信息需通过 kb_search 获取。"

	got := kbSearchQueryFromGoal(goal)

	if !strings.Contains(strings.ToLower(got), "testing standards") {
		t.Fatalf("expected testing standards hint in query, got %q", got)
	}
}

func TestKBSearchQueryFromGoalLeavesNonTestTasksWithoutTestingHint(t *testing.T) {
	goal := "根据知识库中的 HTTP API 规范，修改 internal/http/server.go 中的 writeErr 函数，使错误响应同时包含 error 和 code 两个字段。需先调用 kb_search 查询 API 规范。"

	got := kbSearchQueryFromGoal(goal)

	if strings.Contains(strings.ToLower(got), "testing standards") {
		t.Fatalf("did not expect testing standards hint for non-test task, got %q", got)
	}
}

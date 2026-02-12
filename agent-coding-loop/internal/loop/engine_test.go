package loop

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	agentpkg "github.com/kina/agent-coding-loop/internal/agent"
	gitpkg "github.com/kina/agent-coding-loop/internal/git"
	ghpkg "github.com/kina/agent-coding-loop/internal/github"
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

func mustRun(t *testing.T, r *tools.Runner, repo, cmd string) {
	t.Helper()
	_, stderr, err := r.Run(context.Background(), cmd, repo)
	if err != nil {
		t.Fatalf("cmd failed: %s err=%v stderr=%s", cmd, err, stderr)
	}
}

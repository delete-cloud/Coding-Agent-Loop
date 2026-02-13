//go:build !eino

package loop

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"

	agentpkg "github.com/kina/agent-coding-loop/internal/agent"
	gitpkg "github.com/kina/agent-coding-loop/internal/git"
	ghpkg "github.com/kina/agent-coding-loop/internal/github"
	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/skills"
	sqlite "github.com/kina/agent-coding-loop/internal/store/sqlite"
	"github.com/kina/agent-coding-loop/internal/tools"
)

type EngineDeps struct {
	Store      *sqlite.Store
	Runner     *tools.Runner
	Git        *gitpkg.Client
	GitHub     *ghpkg.Client
	Coder      *agentpkg.Coder
	Reviewer   *agentpkg.Reviewer
	Skills     *skills.Registry
	Artifacts  string
	DoomThresh int
}

type Engine struct {
	store      *sqlite.Store
	runner     *tools.Runner
	git        *gitpkg.Client
	github     *ghpkg.Client
	coder      *agentpkg.Coder
	reviewer   *agentpkg.Reviewer
	skills     *skills.Registry
	artifacts  string
	doomThresh int
}

func NewEngine(deps EngineDeps) *Engine {
	threshold := deps.DoomThresh
	if threshold < 1 {
		threshold = 3
	}
	if deps.Artifacts == "" {
		deps.Artifacts = ".agent-loop-artifacts"
	}
	return &Engine{
		store:      deps.Store,
		runner:     deps.Runner,
		git:        deps.Git,
		github:     deps.GitHub,
		coder:      deps.Coder,
		reviewer:   deps.Reviewer,
		skills:     deps.Skills,
		artifacts:  deps.Artifacts,
		doomThresh: threshold,
	}
}

func (e *Engine) Run(ctx context.Context, spec model.RunSpec) (model.RunResult, error) {
	return e.run(ctx, spec, "")
}

func (e *Engine) Resume(ctx context.Context, runID string) (model.RunResult, error) {
	run, err := e.store.GetRun(ctx, runID)
	if err != nil {
		return model.RunResult{Status: model.RunStatusFailed}, err
	}
	var spec model.RunSpec
	if err := json.Unmarshal([]byte(run.SpecJSON), &spec); err != nil {
		return model.RunResult{Status: model.RunStatusFailed}, err
	}
	return e.run(ctx, spec, runID)
}

func (e *Engine) run(ctx context.Context, spec model.RunSpec, existingRunID string) (model.RunResult, error) {
	if err := spec.Validate(); err != nil {
		return model.RunResult{Status: model.RunStatusFailed}, err
	}
	repo := spec.Repo
	if strings.TrimSpace(repo) == "" {
		wd, err := os.Getwd()
		if err != nil {
			return model.RunResult{Status: model.RunStatusFailed}, err
		}
		repo = wd
	}
	repoAbs, err := filepath.Abs(repo)
	if err != nil {
		return model.RunResult{Status: model.RunStatusFailed}, err
	}
	commands, err := tools.ResolveCommands(spec, repoAbs)
	if err != nil {
		return model.RunResult{Status: model.RunStatusFailed}, err
	}
	if err := e.git.EnsureRepo(ctx, repoAbs); err != nil {
		return model.RunResult{Status: model.RunStatusFailed}, err
	}
	if e.skills != nil {
		_ = e.skills.Load()
	}
	baselineStatus, _ := e.git.StatusShort(ctx, repoAbs)

	runID := existingRunID
	if runID == "" {
		runID, err = e.store.CreateRun(ctx, spec, model.RunStatusQueued)
		if err != nil {
			return model.RunResult{Status: model.RunStatusFailed}, err
		}
	}
	if err := e.store.UpdateRunStatus(ctx, runID, model.RunStatusRunning, "run started"); err != nil {
		return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
	}

	runRecord, err := e.store.GetRun(ctx, runID)
	if err != nil {
		_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "load run failed")
		return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
	}
	branch := runRecord.Branch
	if strings.TrimSpace(branch) == "" {
		branch, err = e.git.CreateFeatureBranch(ctx, repoAbs)
		if err != nil {
			_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "create branch failed")
			return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
		}
		_ = e.store.UpdateRunMeta(ctx, runID, branch, runRecord.CommitHash, runRecord.PRURL)
	} else {
		_ = e.git.CheckoutBranch(ctx, repoAbs, branch)
	}

	detector := NewDoomLoopDetector(e.doomThresh)
	skillsSummary := e.renderSkillsSummary()
	previousReview := ""
	var commandOutput strings.Builder
	offset, _ := e.store.CountSteps(ctx, runID)
	for n := 1; n <= spec.MaxIterations; n++ {
		iteration := offset + n
		started := time.Now().UnixMilli()
		coderIn := agentpkg.CoderInput{
			Goal:           spec.Goal,
			RepoSummary:    repoAbs,
			PreviousReview: previousReview,
			Diff:           mustDiff(ctx, e.git, repoAbs),
			TestOutput:     commandOutput.String(),
			Commands:       mergeCommands(commands),
			SkillsSummary:  skillsSummary,
		}
		coderOut, err := e.coder.Generate(ctx, coderIn)
		if err != nil {
			_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "coder failed")
			return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
		}

		if strings.TrimSpace(coderOut.Patch) != "" {
			if detector.Observe("git_apply", coderOut.Patch) {
				_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusBlocked, "doom-loop detected on git_apply")
				return model.RunResult{RunID: runID, Status: model.RunStatusBlocked}, nil
			}
			err := e.git.ApplyPatch(ctx, repoAbs, coderOut.Patch)
			status := "completed"
			output := "patch applied"
			if err != nil {
				status = "error"
				output = err.Error()
			}
			_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{RunID: runID, Iteration: iteration, Tool: "git_apply", Input: coderOut.Patch, Output: output, Status: status, CreatedAt: time.Now().UnixMilli()})
			if err != nil {
				_ = e.store.InsertStep(ctx, sqlite.StepRecord{RunID: runID, Iteration: iteration, Agent: "coder", Decision: string(model.LoopDecisionRequestChanges), Status: string(model.RunStatusNeedsChange), StartedAt: started, EndedAt: time.Now().UnixMilli()})
				previousReview = "Patch apply failed: " + err.Error()
				continue
			}
		}

		cmds := coderOut.Commands
		if len(cmds) == 0 {
			cmds = mergeCommands(commands)
		}
		commandOutput.Reset()
		failed := false
		for _, cmd := range cmds {
			if detector.Observe("run_command", cmd) {
				_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusBlocked, "doom-loop detected on run_command")
				return model.RunResult{RunID: runID, Status: model.RunStatusBlocked}, nil
			}
			stdout, stderr, err := e.runner.Run(ctx, cmd, repoAbs)
			status := "completed"
			combined := strings.TrimSpace(stdout + "\n" + stderr)
			if err != nil {
				status = "error"
				combined = strings.TrimSpace(combined + "\n" + err.Error())
				failed = true
			}
			if combined != "" {
				commandOutput.WriteString("$ " + cmd + "\n" + combined + "\n")
			}
			_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{RunID: runID, Iteration: iteration, Tool: "run_command", Input: cmd, Output: combined, Status: status, CreatedAt: time.Now().UnixMilli()})
		}

		reviewOut, err := e.reviewer.Review(ctx, agentpkg.ReviewInput{
			Goal:          spec.Goal,
			RepoRoot:      repoAbs,
			Diff:          mustDiff(ctx, e.git, repoAbs),
			CommandOutput: commandOutput.String(),
			SkillsSummary: skillsSummary,
		})
		if err != nil {
			_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "reviewer failed")
			return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
		}
		findings, _ := json.Marshal(reviewOut.Findings)
		_ = e.store.InsertReview(ctx, sqlite.ReviewRecord{
			RunID:        runID,
			Iteration:    iteration,
			Decision:     reviewOut.Decision,
			Summary:      reviewOut.Summary,
			FindingsJSON: string(findings),
			CreatedAt:    time.Now().UnixMilli(),
		})

		if reviewOut.Decision == string(model.ReviewDecisionRequestChanges) || failed {
			_ = e.store.InsertStep(ctx, sqlite.StepRecord{RunID: runID, Iteration: iteration, Agent: "reviewer", Decision: string(model.LoopDecisionRequestChanges), Status: string(model.RunStatusNeedsChange), StartedAt: started, EndedAt: time.Now().UnixMilli()})
			_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusNeedsChange, reviewOut.Summary)
			previousReview = reviewOut.Summary
			continue
		}

		_ = e.store.InsertStep(ctx, sqlite.StepRecord{RunID: runID, Iteration: iteration, Agent: "reviewer", Decision: string(model.LoopDecisionComplete), Status: string(model.RunStatusRunning), StartedAt: started, EndedAt: time.Now().UnixMilli()})
		return e.finishRun(ctx, runID, repoAbs, branch, spec.PRMode, baselineStatus, reviewOut)
	}

	_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "max iterations reached")
	return model.RunResult{RunID: runID, Status: model.RunStatusFailed, Branch: branch, Summary: "max iterations reached"}, nil
}

func (e *Engine) finishRun(ctx context.Context, runID, repo, branch string, requestedPRMode model.PRMode, baselineStatus string, review agentpkg.ReviewOutput) (model.RunResult, error) {
	status, err := e.git.StatusShort(ctx, repo)
	if err != nil {
		_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "git status failed")
		return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
	}
	commitHash := ""
	paths := statusDeltaPaths(baselineStatus, status)
	if len(paths) > 0 {
		commitHash, err = e.git.CommitPaths(ctx, repo, paths, "feat: agent loop generated update")
		if err != nil {
			_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "git commit failed")
			return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
		}
	}

	remoteURL, _ := e.git.RemoteURL(ctx, repo)
	effectiveMode := e.github.ResolvePRMode(ctx, requestedPRMode, remoteURL)
	prURL := ""
	artifactsDir := ""

	reviewMD := review.Markdown
	if strings.TrimSpace(reviewMD) == "" {
		reviewMD = review.Summary
	}
	if effectiveMode == model.PRModeLive && strings.TrimSpace(commitHash) != "" {
		if err := e.git.Push(ctx, repo, branch); err != nil {
			_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "git push failed")
			return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
		}
		prURL, err = e.github.CreatePR(ctx, repo, "feat: agent loop run "+runID, "Automated by agent-loop", branch, "main")
		if err != nil {
			_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "gh pr create failed")
			return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
		}
		path := filepath.Join(e.artifacts, runID, "review.md")
		_ = os.MkdirAll(filepath.Dir(path), 0o755)
		_ = os.WriteFile(path, []byte(reviewMD), 0o644)
		_ = e.github.SubmitReview(ctx, repo, model.ReviewDecisionComment, path)
		artifactsDir = filepath.Dir(path)
	} else {
		artifactsDir, err = ghpkg.WriteDryRunArtifacts(repo, runID, "feat: agent loop run "+runID, "Automated by agent-loop", reviewMD)
		if err != nil {
			return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
		}
		_ = e.store.InsertArtifact(ctx, sqlite.ArtifactRecord{RunID: runID, Kind: "pr_dry_run", Path: artifactsDir, Content: reviewMD, CreatedAt: time.Now().UnixMilli()})
	}

	_ = e.store.UpdateRunMeta(ctx, runID, branch, commitHash, prURL)
	_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusCompleted, review.Summary)
	return model.RunResult{
		RunID:         runID,
		Status:        model.RunStatusCompleted,
		Branch:        branch,
		Commit:        commitHash,
		PRURL:         prURL,
		ReviewOutcome: model.ReviewDecision(review.Decision),
		ArtifactsDir:  artifactsDir,
		Summary:       review.Summary,
	}, nil
}

func (e *Engine) renderSkillsSummary() string {
	if e.skills == nil {
		return ""
	}
	list := e.skills.List()
	if len(list) == 0 {
		return ""
	}
	b := strings.Builder{}
	for _, s := range list {
		b.WriteString("- " + s.Name + ": " + s.Description + "\n")
	}
	return b.String()
}

func mergeCommands(set model.CommandSet) []string {
	out := make([]string, 0, len(set.Test)+len(set.Lint)+len(set.Build))
	out = append(out, set.Test...)
	out = append(out, set.Lint...)
	out = append(out, set.Build...)
	return out
}

func mustDiff(ctx context.Context, g *gitpkg.Client, repo string) string {
	diff, err := g.Diff(ctx, repo)
	if err != nil {
		return ""
	}
	return diff
}

func statusDeltaPaths(baseline, current string) []string {
	baseSet := make(map[string]struct{})
	for _, line := range strings.Split(strings.TrimSpace(baseline), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		baseSet[line] = struct{}{}
	}
	out := make([]string, 0, 8)
	seen := make(map[string]struct{})
	for _, line := range strings.Split(strings.TrimSpace(current), "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			continue
		}
		if _, ok := baseSet[trimmed]; ok {
			continue
		}
		path := statusLinePath(trimmed)
		if path == "" {
			continue
		}
		if _, ok := seen[path]; ok {
			continue
		}
		seen[path] = struct{}{}
		out = append(out, path)
	}
	return out
}

func statusLinePath(line string) string {
	if len(line) < 4 {
		return ""
	}
	part := strings.TrimSpace(line[3:])
	if strings.Contains(part, " -> ") {
		items := strings.Split(part, " -> ")
		return strings.TrimSpace(items[len(items)-1])
	}
	return part
}

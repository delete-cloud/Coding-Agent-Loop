//go:build eino

package loop

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/cloudwego/eino/compose"
	"github.com/cloudwego/eino/schema"
	agentpkg "github.com/kina/agent-coding-loop/internal/agent"
	gitpkg "github.com/kina/agent-coding-loop/internal/git"
	ghpkg "github.com/kina/agent-coding-loop/internal/github"
	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/skills"
	sqlite "github.com/kina/agent-coding-loop/internal/store/sqlite"
	"github.com/kina/agent-coding-loop/internal/tools"
)

func init() {
	// Needed for Eino checkpoint serializer when storing graph input/output state.
	schema.Register[*loopSession]()
}

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

	checkpoints compose.CheckPointStore
}

type loopSession struct {
	RunID          string
	Spec           model.RunSpec
	RepoAbs        string
	Branch         string
	BaselineStatus string
	Commands       model.CommandSet
	SkillsSummary  string
	PreviousReview string
	CommandOutput  string
	Iteration      int
	Offset         int

	DoomLastTool  string
	DoomLastInput string
	DoomCount     int

	Decision model.LoopDecision
	Status   model.RunStatus
	Summary  string
	Review   agentpkg.ReviewOutput
	Result   model.RunResult
}

type memoryCheckpointStore struct {
	mu   sync.RWMutex
	data map[string][]byte
}

func newMemoryCheckpointStore() *memoryCheckpointStore {
	return &memoryCheckpointStore{data: map[string][]byte{}}
}

func (m *memoryCheckpointStore) Get(_ context.Context, checkPointID string) ([]byte, bool, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	b, ok := m.data[checkPointID]
	if !ok {
		return nil, false, nil
	}
	out := make([]byte, len(b))
	copy(out, b)
	return out, true, nil
}

func (m *memoryCheckpointStore) Set(_ context.Context, checkPointID string, checkPoint []byte) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]byte, len(checkPoint))
	copy(out, checkPoint)
	m.data[checkPointID] = out
	return nil
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
		store:       deps.Store,
		runner:      deps.Runner,
		git:         deps.Git,
		github:      deps.GitHub,
		coder:       deps.Coder,
		reviewer:    deps.Reviewer,
		skills:      deps.Skills,
		artifacts:   deps.Artifacts,
		doomThresh:  threshold,
		checkpoints: newMemoryCheckpointStore(),
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

	offset, _ := e.store.CountSteps(ctx, runID)
	flowInput := &loopSession{
		RunID:          runID,
		Spec:           spec,
		RepoAbs:        repoAbs,
		Branch:         branch,
		BaselineStatus: baselineStatus,
		Commands:       commands,
		SkillsSummary:  e.renderSkillsSummary(),
		Offset:         offset,
		Status:         model.RunStatusRunning,
	}

	runner, err := e.buildLoopRunner(ctx)
	if err != nil {
		_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "compile eino loop failed")
		return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
	}

	output, err := runner.Invoke(
		ctx,
		flowInput,
		compose.WithCheckPointID(runID),
		compose.WithRuntimeMaxSteps(maxRuntimeSteps(spec.MaxIterations)),
	)
	if err != nil {
		_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "eino invoke failed: "+err.Error())
		return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
	}
	if output == nil {
		_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "eino loop returned nil output")
		return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, fmt.Errorf("eino loop returned nil output")
	}
	if output.Result.RunID == "" {
		output.Result.RunID = runID
		output.Result.Status = output.Status
		output.Result.Branch = output.Branch
		output.Result.Summary = output.Summary
	}
	return output.Result, nil
}

func (e *Engine) buildLoopRunner(ctx context.Context) (compose.Runnable[*loopSession, *loopSession], error) {
	g := compose.NewGraph[*loopSession, *loopSession]()
	if err := g.AddLambdaNode("turn", compose.InvokableLambda(e.turnNode)); err != nil {
		return nil, err
	}
	if err := g.AddLambdaNode("finish", compose.InvokableLambda(e.finishNode)); err != nil {
		return nil, err
	}
	if err := g.AddLambdaNode("failed", compose.InvokableLambda(e.failedNode)); err != nil {
		return nil, err
	}
	if err := g.AddLambdaNode("blocked", compose.InvokableLambda(e.blockedNode)); err != nil {
		return nil, err
	}
	if err := g.AddEdge(compose.START, "turn"); err != nil {
		return nil, err
	}
	if err := g.AddBranch("turn", compose.NewGraphBranch(e.branchAfterTurn, map[string]bool{
		"turn":    true,
		"finish":  true,
		"failed":  true,
		"blocked": true,
	})); err != nil {
		return nil, err
	}
	if err := g.AddEdge("finish", compose.END); err != nil {
		return nil, err
	}
	if err := g.AddEdge("failed", compose.END); err != nil {
		return nil, err
	}
	if err := g.AddEdge("blocked", compose.END); err != nil {
		return nil, err
	}
	return g.Compile(ctx, compose.WithCheckPointStore(e.checkpoints), compose.WithGraphName("agent_loop_eino"))
}

func (e *Engine) turnNode(ctx context.Context, st *loopSession) (*loopSession, error) {
	if st == nil {
		return nil, fmt.Errorf("loop state is nil")
	}
	if st.Iteration >= st.Spec.MaxIterations {
		st.Decision = model.LoopDecisionAbort
		st.Status = model.RunStatusFailed
		st.Summary = "max iterations reached"
		return st, nil
	}

	st.Iteration++
	iteration := st.Offset + st.Iteration
	started := time.Now().UnixMilli()

	coderIn := agentpkg.CoderInput{
		Goal:           st.Spec.Goal,
		RepoSummary:    st.RepoAbs,
		PreviousReview: st.PreviousReview,
		Diff:           mustDiff(ctx, e.git, st.RepoAbs),
		TestOutput:     st.CommandOutput,
		Commands:       mergeCommands(st.Commands),
		SkillsSummary:  st.SkillsSummary,
	}
	coderOut, err := e.coder.Generate(ctx, coderIn)
	if err != nil {
		st.Decision = model.LoopDecisionAbort
		st.Status = model.RunStatusFailed
		st.Summary = "coder failed"
		_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusFailed, st.Summary)
		return st, nil
	}

	if strings.TrimSpace(coderOut.Patch) != "" {
		if e.observeDoom(st, "git_apply", coderOut.Patch) {
			st.Decision = model.LoopDecisionAbort
			st.Status = model.RunStatusBlocked
			st.Summary = "doom-loop detected on git_apply"
			_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusBlocked, st.Summary)
			return st, nil
		}
		err := e.git.ApplyPatch(ctx, st.RepoAbs, coderOut.Patch)
		callStatus := "completed"
		callOutput := "patch applied"
		if err != nil {
			callStatus = "error"
			callOutput = err.Error()
		}
		_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
			RunID:     st.RunID,
			Iteration: iteration,
			Tool:      "git_apply",
			Input:     coderOut.Patch,
			Output:    callOutput,
			Status:    callStatus,
			CreatedAt: time.Now().UnixMilli(),
		})
		if err != nil {
			_ = e.store.InsertStep(ctx, sqlite.StepRecord{
				RunID:     st.RunID,
				Iteration: iteration,
				Agent:     "coder",
				Decision:  string(model.LoopDecisionRequestChanges),
				Status:    string(model.RunStatusNeedsChange),
				StartedAt: started,
				EndedAt:   time.Now().UnixMilli(),
			})
			st.Decision = model.LoopDecisionRequestChanges
			st.PreviousReview = "Patch apply failed: " + err.Error()
			st.Summary = st.PreviousReview
			_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusNeedsChange, st.Summary)
			return st, nil
		}
	}

	cmds := coderOut.Commands
	if len(cmds) == 0 {
		cmds = mergeCommands(st.Commands)
	}
	var commandOutput strings.Builder
	commandFailed := false
	for _, cmd := range cmds {
		if e.observeDoom(st, "run_command", cmd) {
			st.Decision = model.LoopDecisionAbort
			st.Status = model.RunStatusBlocked
			st.Summary = "doom-loop detected on run_command"
			_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusBlocked, st.Summary)
			return st, nil
		}
		stdout, stderr, err := e.runner.Run(ctx, cmd, st.RepoAbs)
		callStatus := "completed"
		combined := strings.TrimSpace(stdout + "\n" + stderr)
		if err != nil {
			callStatus = "error"
			combined = strings.TrimSpace(combined + "\n" + err.Error())
			commandFailed = true
		}
		if combined != "" {
			commandOutput.WriteString("$ " + cmd + "\n" + combined + "\n")
		}
		_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
			RunID:     st.RunID,
			Iteration: iteration,
			Tool:      "run_command",
			Input:     cmd,
			Output:    combined,
			Status:    callStatus,
			CreatedAt: time.Now().UnixMilli(),
		})
	}

	reviewOut, err := e.reviewer.Review(ctx, agentpkg.ReviewInput{
		Goal:          st.Spec.Goal,
		RepoRoot:      st.RepoAbs,
		Diff:          mustDiff(ctx, e.git, st.RepoAbs),
		CommandOutput: commandOutput.String(),
		SkillsSummary: st.SkillsSummary,
	})
	if err != nil {
		st.Decision = model.LoopDecisionAbort
		st.Status = model.RunStatusFailed
		st.Summary = "reviewer failed"
		_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusFailed, st.Summary)
		return st, nil
	}
	findings, _ := json.Marshal(reviewOut.Findings)
	_ = e.store.InsertReview(ctx, sqlite.ReviewRecord{
		RunID:        st.RunID,
		Iteration:    iteration,
		Decision:     reviewOut.Decision,
		Summary:      reviewOut.Summary,
		FindingsJSON: string(findings),
		CreatedAt:    time.Now().UnixMilli(),
	})

	st.Review = reviewOut
	st.CommandOutput = commandOutput.String()

	if reviewOut.Decision == string(model.ReviewDecisionRequestChanges) || commandFailed {
		_ = e.store.InsertStep(ctx, sqlite.StepRecord{
			RunID:     st.RunID,
			Iteration: iteration,
			Agent:     "reviewer",
			Decision:  string(model.LoopDecisionRequestChanges),
			Status:    string(model.RunStatusNeedsChange),
			StartedAt: started,
			EndedAt:   time.Now().UnixMilli(),
		})
		_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusNeedsChange, reviewOut.Summary)
		st.Decision = model.LoopDecisionRequestChanges
		st.PreviousReview = reviewOut.Summary
		st.Summary = reviewOut.Summary
		st.Status = model.RunStatusNeedsChange
		return st, nil
	}

	_ = e.store.InsertStep(ctx, sqlite.StepRecord{
		RunID:     st.RunID,
		Iteration: iteration,
		Agent:     "reviewer",
		Decision:  string(model.LoopDecisionComplete),
		Status:    string(model.RunStatusRunning),
		StartedAt: started,
		EndedAt:   time.Now().UnixMilli(),
	})
	st.Decision = model.LoopDecisionComplete
	st.Status = model.RunStatusRunning
	st.Summary = reviewOut.Summary
	return st, nil
}

func (e *Engine) branchAfterTurn(_ context.Context, st *loopSession) (string, error) {
	if st == nil {
		return "failed", nil
	}
	if st.Status == model.RunStatusBlocked {
		return "blocked", nil
	}
	if st.Status == model.RunStatusFailed {
		return "failed", nil
	}
	switch st.Decision {
	case model.LoopDecisionComplete:
		return "finish", nil
	case model.LoopDecisionRequestChanges:
		if st.Iteration >= st.Spec.MaxIterations {
			return "failed", nil
		}
		return "turn", nil
	case model.LoopDecisionAbort:
		return "failed", nil
	default:
		if st.Iteration >= st.Spec.MaxIterations {
			return "failed", nil
		}
		return "turn", nil
	}
}

func (e *Engine) finishNode(ctx context.Context, st *loopSession) (*loopSession, error) {
	if st == nil {
		return nil, fmt.Errorf("loop state is nil")
	}
	result, err := e.finishRun(ctx, st.RunID, st.RepoAbs, st.Branch, st.Spec.PRMode, st.BaselineStatus, st.Review)
	if err != nil {
		st.Status = model.RunStatusFailed
		st.Summary = err.Error()
		st.Result = model.RunResult{
			RunID:   st.RunID,
			Status:  model.RunStatusFailed,
			Branch:  st.Branch,
			Summary: st.Summary,
		}
		return st, nil
	}
	st.Status = result.Status
	st.Summary = result.Summary
	st.Result = result
	return st, nil
}

func (e *Engine) blockedNode(ctx context.Context, st *loopSession) (*loopSession, error) {
	if st == nil {
		return nil, fmt.Errorf("loop state is nil")
	}
	if st.Status != model.RunStatusBlocked {
		st.Status = model.RunStatusBlocked
		if st.Summary == "" {
			st.Summary = "run blocked"
		}
		_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusBlocked, st.Summary)
	}
	st.Result = model.RunResult{
		RunID:   st.RunID,
		Status:  model.RunStatusBlocked,
		Branch:  st.Branch,
		Summary: st.Summary,
	}
	return st, nil
}

func (e *Engine) failedNode(ctx context.Context, st *loopSession) (*loopSession, error) {
	if st == nil {
		return nil, fmt.Errorf("loop state is nil")
	}
	if strings.TrimSpace(st.Summary) == "" {
		if st.Iteration >= st.Spec.MaxIterations {
			st.Summary = "max iterations reached"
		} else {
			st.Summary = "run failed"
		}
	}
	st.Status = model.RunStatusFailed
	_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusFailed, st.Summary)
	st.Result = model.RunResult{
		RunID:   st.RunID,
		Status:  model.RunStatusFailed,
		Branch:  st.Branch,
		Summary: st.Summary,
	}
	return st, nil
}

func (e *Engine) observeDoom(st *loopSession, tool string, input any) bool {
	serialized := fmt.Sprintf("%v", input)
	if st.DoomLastTool == tool && st.DoomLastInput == serialized {
		st.DoomCount++
	} else {
		st.DoomLastTool = tool
		st.DoomLastInput = serialized
		st.DoomCount = 1
	}
	return st.DoomCount >= e.doomThresh
}

func maxRuntimeSteps(maxIterations int) int {
	if maxIterations < 1 {
		maxIterations = 5
	}
	// Each loop turn has one main processing node, plus terminal nodes.
	return maxIterations*3 + 8
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

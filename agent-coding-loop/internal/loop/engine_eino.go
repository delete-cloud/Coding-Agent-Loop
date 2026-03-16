package loop

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/cloudwego/eino/compose"
	"github.com/cloudwego/eino/schema"
	agentpkg "github.com/kina/agent-coding-loop/internal/agent"
	gitpkg "github.com/kina/agent-coding-loop/internal/git"
	ghpkg "github.com/kina/agent-coding-loop/internal/github"
	kbpkg "github.com/kina/agent-coding-loop/internal/kb"
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
	Store           *sqlite.Store
	Runner          *tools.Runner
	Git             *gitpkg.Client
	GitHub          *ghpkg.Client
	KB              *kbpkg.Client
	Coder           *agentpkg.Coder
	Reviewer        *agentpkg.Reviewer
	Skills          *skills.Registry
	Artifacts       string
	DoomThresh      int
	ReviewerTimeout time.Duration
}

type Engine struct {
	store           *sqlite.Store
	runner          *tools.Runner
	git             *gitpkg.Client
	github          *ghpkg.Client
	kb              *kbpkg.Client
	coder           *agentpkg.Coder
	reviewer        *agentpkg.Reviewer
	skills          *skills.Registry
	artifacts       string
	doomThresh      int
	reviewerTimeout time.Duration

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
	KBPrefetched   bool
	KBSearchCalls  int
	RetrievedHits  []kbpkg.SearchHit
	RetrievedQuery string

	DoomLastTool  string
	DoomLastInput string
	DoomCount     int

	Decision model.LoopDecision
	Status   model.RunStatus
	Summary  string
	Review   agentpkg.ReviewOutput
	Result   model.RunResult
}

type runOptions struct {
	initialIteration *int
	forceNewRun      bool
}

const (
	promptRetrievedContextMaxHits = 4
	promptRetrievedTextMaxChars   = 500
	coderRefreshReviewMaxChars    = 160
	defaultReviewerTimeout        = 60 * time.Second
)

var goalPathHintRE = regexp.MustCompile(`[A-Za-z0-9_./\-]+\.[A-Za-z0-9_+-]+`)

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

type fileCheckpointStore struct {
	mu  sync.RWMutex
	dir string
}

func newFileCheckpointStore(dir string) *fileCheckpointStore {
	return &fileCheckpointStore{dir: dir}
}

func (f *fileCheckpointStore) Get(_ context.Context, checkPointID string) ([]byte, bool, error) {
	path := f.pathForID(checkPointID)
	f.mu.RLock()
	defer f.mu.RUnlock()
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) || errors.Is(err, fs.ErrNotExist) {
			return nil, false, nil
		}
		return nil, false, err
	}
	out := make([]byte, len(b))
	copy(out, b)
	return out, true, nil
}

func (f *fileCheckpointStore) Set(_ context.Context, checkPointID string, checkPoint []byte) error {
	path := f.pathForID(checkPointID)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp := path + fmt.Sprintf(".tmp.%d", time.Now().UnixNano())
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := os.WriteFile(tmp, checkPoint, 0o644); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

func (f *fileCheckpointStore) pathForID(checkPointID string) string {
	sum := sha256.Sum256([]byte(checkPointID))
	name := hex.EncodeToString(sum[:])
	return filepath.Join(f.dir, name+".bin")
}

func NewEngine(deps EngineDeps) *Engine {
	threshold := deps.DoomThresh
	if threshold < 1 {
		threshold = 3
	}
	if deps.Artifacts == "" {
		deps.Artifacts = ".agent-loop-artifacts"
	}
	reviewerTimeout := deps.ReviewerTimeout
	if reviewerTimeout <= 0 {
		reviewerTimeout = defaultReviewerTimeout
	}
	checkpoints := compose.CheckPointStore(newMemoryCheckpointStore())
	checkpointDir := filepath.Join(deps.Artifacts, "checkpoints")
	if err := os.MkdirAll(checkpointDir, 0o755); err == nil {
		checkpoints = newFileCheckpointStore(checkpointDir)
	}
	return &Engine{
		store:           deps.Store,
		runner:          deps.Runner,
		git:             deps.Git,
		github:          deps.GitHub,
		kb:              deps.KB,
		coder:           deps.Coder,
		reviewer:        deps.Reviewer,
		skills:          deps.Skills,
		artifacts:       deps.Artifacts,
		doomThresh:      threshold,
		reviewerTimeout: reviewerTimeout,
		checkpoints:     checkpoints,
	}
}

func (e *Engine) Run(ctx context.Context, spec model.RunSpec) (model.RunResult, error) {
	return e.run(ctx, spec, "", runOptions{})
}

func (e *Engine) RunWithID(ctx context.Context, runID string, spec model.RunSpec) (model.RunResult, error) {
	if strings.TrimSpace(runID) == "" {
		return model.RunResult{Status: model.RunStatusFailed}, fmt.Errorf("run id is required")
	}
	return e.run(ctx, spec, runID)
}

func (e *Engine) Resume(ctx context.Context, runID string) (model.RunResult, error) {
	run, err := e.store.GetRun(ctx, runID)
	if err != nil {
		return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
	}
	var spec model.RunSpec
	if err := json.Unmarshal([]byte(run.SpecJSON), &spec); err != nil {
		return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
	}
	if model.RunStatus(strings.TrimSpace(run.Status)) != model.RunStatusRunning {
		return model.RunResult{
			RunID:   runID,
			Status:  model.RunStatus(strings.TrimSpace(run.Status)),
			Summary: run.Summary,
		}, fmt.Errorf("resume only supports interrupted running runs; run %s is %s", runID, strings.TrimSpace(run.Status))
	}
	hasCheckpoint, err := e.hasCheckpoint(ctx, runID)
	if err != nil {
		return e.failClosedResume(ctx, runID, "resume failed closed: checkpoint lookup failed for interrupted running run", err)
	}
	if !hasCheckpoint {
		return e.failClosedResume(ctx, runID, "resume failed closed: checkpoint missing for interrupted running run", fmt.Errorf("checkpoint missing for interrupted running run %s", runID))
	}
	return e.run(ctx, spec, runID, runOptions{})
}

func (e *Engine) failClosedResume(ctx context.Context, runID, summary string, cause error) (model.RunResult, error) {
	if updateErr := e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, summary); updateErr != nil {
		joinedErr := errors.Join(cause, fmt.Errorf("failed to persist failed status: %w", updateErr))
		return model.RunResult{
			RunID:   runID,
			Status:  model.RunStatusFailed,
			Summary: summary,
		}, fmt.Errorf("%s: %w", summary, joinedErr)
	}
	return model.RunResult{
		RunID:   runID,
		Status:  model.RunStatusFailed,
		Summary: summary,
	}, fmt.Errorf("%s: %w", summary, cause)
}


func (e *Engine) hasCheckpoint(ctx context.Context, runID string) (bool, error) {
	if e.checkpoints == nil {
		return false, nil
	}
	_, ok, err := e.checkpoints.Get(ctx, runID)
	return ok, err
}

func (e *Engine) run(ctx context.Context, spec model.RunSpec, existingRunID string, opts runOptions) (model.RunResult, error) {
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
	reason := "fresh_run"
	if existingRunID != "" {
		reason = "resume"
	}
	e.emitProgress(ctx, runID, 0, model.ProgressEventRunStarted, model.ProgressStatusStarted, "run started", map[string]any{
		"reason": reason,
	})

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

	lastIteration, _ := e.store.MaxStepIteration(ctx, runID)
	iteration := lastIteration
	if opts.initialIteration != nil {
		iteration = *opts.initialIteration
	}
	flowInput := &loopSession{
		RunID:          runID,
		Spec:           spec,
		RepoAbs:        repoAbs,
		Branch:         branch,
		BaselineStatus: baselineStatus,
		Commands:       commands,
		SkillsSummary:  e.renderSkillsSummary(),
		Iteration:      iteration,
		Status:         model.RunStatusRunning,
	}

	runner, err := e.buildLoopRunner(ctx)
	if err != nil {
		_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "compile eino loop failed")
		e.emitProgress(ctx, runID, 0, model.ProgressEventRunFailed, model.ProgressStatusError, "compile eino loop failed", map[string]any{
			"error": "compile eino loop failed",
		})
		return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
	}

	invokeOpts := []compose.Option{
		compose.WithCheckPointID(runID),
		compose.WithRuntimeMaxSteps(maxRuntimeSteps(spec.MaxIterations)),
	}
	if opts.forceNewRun {
		invokeOpts = append(invokeOpts, compose.WithForceNewRun())
	}
	output, err := runner.Invoke(ctx, flowInput, invokeOpts...)
	if err != nil {
		_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "eino invoke failed: "+err.Error())
		e.emitProgress(ctx, runID, 0, model.ProgressEventRunFailed, model.ProgressStatusError, "eino invoke failed", map[string]any{
			"error": truncateString(err.Error(), 500),
		})
		return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
	}
	if output == nil {
		_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "eino loop returned nil output")
		e.emitProgress(ctx, runID, 0, model.ProgressEventRunFailed, model.ProgressStatusError, "eino loop returned nil output", map[string]any{
			"error": "eino loop returned nil output",
		})
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
	iteration := st.Iteration
	started := time.Now().UnixMilli()
	e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventIterationStarted, model.ProgressStatusStarted, fmt.Sprintf("iteration %d started", iteration), nil)
	e.maybePreflightKBSearch(ctx, st, iteration)

	currentDiff := mustDiff(ctx, e.git, st.RepoAbs)
	coderIn := buildCoderInput(st, currentDiff)
	if refreshedIn, refreshed := e.maybeRefreshCoderContext(ctx, st, iteration, coderIn); refreshed {
		coderIn = refreshedIn
	}
	_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
		RunID:     st.RunID,
		Iteration: iteration,
		Tool:      "coder_start",
		Input:     truncateString(st.Spec.Goal, 500),
		Output:    "starting coder generate",
		Status:    "started",
		CreatedAt: time.Now().UnixMilli(),
	})
	coderCtx := agentpkg.WithAgentStageRecorder(ctx, func(stage string) {
		stage = strings.TrimSpace(stage)
		if stage == "" {
			return
		}
		_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
			RunID:     st.RunID,
			Iteration: iteration,
			Tool:      "coder_stage",
			Input:     "",
			Output:    stage,
			Status:    "progress",
			CreatedAt: time.Now().UnixMilli(),
		})
	})
	e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventCoderGenerating, model.ProgressStatusStarted, "coder generating", nil)
	coderOut, err := e.coder.Generate(coderCtx, coderIn)
	if err != nil {
		e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventCoderGenerating, model.ProgressStatusError, "coder failed", map[string]any{
			"error": truncateString(err.Error(), 500),
		})
		_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
			RunID:     st.RunID,
			Iteration: iteration,
			Tool:      "coder_generate",
			Input:     truncateString(st.Spec.Goal, 500),
			Output:    truncateString(err.Error(), 4000),
			Status:    "error",
			CreatedAt: time.Now().UnixMilli(),
		})
		_ = e.store.InsertStep(ctx, sqlite.StepRecord{
			RunID:     st.RunID,
			Iteration: iteration,
			Agent:     "coder",
			Decision:  string(model.LoopDecisionAbort),
			Status:    string(model.RunStatusFailed),
			StartedAt: started,
			EndedAt:   time.Now().UnixMilli(),
		})
		st.Decision = model.LoopDecisionAbort
		st.Status = model.RunStatusFailed
		st.Summary = truncateString("coder failed: "+err.Error(), 500)
		_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusFailed, st.Summary)
		return st, nil
	}
	e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventCoderGenerating, model.ProgressStatusCompleted, "coder generated patch", nil)
	coderMetaJSON := mustJSON(map[string]any{
		"used_fallback":   coderOut.UsedFallback,
		"fallback_source": strings.TrimSpace(coderOut.FallbackSource),
		"citations":       coderOut.Citations,
		"notes":           strings.TrimSpace(coderOut.Notes),
		"patch_empty":     strings.TrimSpace(coderOut.Patch) == "",
		"patch_touches_target": func() bool {
			targets := loopExtractGoalTargetFiles(st.Spec.Goal)
			if len(targets) == 0 {
				return false
			}
			return loopPatchTouchesTargets(coderOut.Patch, targets, len(targets) > 1)
		}(),
	})
	_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
		RunID:     st.RunID,
		Iteration: iteration,
		Tool:      "coder_meta",
		Input:     "",
		Output:    coderMetaJSON,
		Status:    "completed",
		CreatedAt: time.Now().UnixMilli(),
	})

	if strings.TrimSpace(coderOut.Patch) != "" {
		if e.observeDoom(st, "git_apply", coderOut.Patch) {
			st.Decision = model.LoopDecisionAbort
			st.Status = model.RunStatusBlocked
			st.Summary = "doom-loop detected on git_apply"
			_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusBlocked, st.Summary)
			return st, nil
		}
		e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventPatchApplying, model.ProgressStatusStarted, "applying patch", nil)
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
			e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventPatchFailed, model.ProgressStatusError, "patch apply failed", map[string]any{
				"error":  truncateString(err.Error(), 500),
				"reason": "will_retry",
			})
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
			e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventIterationComplete, model.ProgressStatusCompleted, "iteration completed: request changes", map[string]any{
				"decision": string(model.LoopDecisionRequestChanges),
			})
			return st, nil
		}
		e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventPatchApplying, model.ProgressStatusCompleted, "patch applied", nil)
	}

	cmds := coderOut.Commands
	cmds = sanitizeShellCommands(cmds)
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
		e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventCommandRunning, model.ProgressStatusStarted, "running command: "+cmd, map[string]any{
			"command_kind": progressCommandKind(st.Commands, cmd),
			"command":      cmd,
		})
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
		progressStatus := model.ProgressStatusCompleted
		progressSummary := "command completed: " + cmd
		progressDetail := map[string]any{
			"command_kind": progressCommandKind(st.Commands, cmd),
			"command":      cmd,
		}
		if err != nil {
			progressStatus = model.ProgressStatusError
			progressSummary = "command failed: " + cmd
			progressDetail["error"] = truncateString(err.Error(), 500)
		}
		e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventCommandRunning, progressStatus, progressSummary, progressDetail)
	}
	statusShort, _ := e.git.StatusShort(ctx, st.RepoAbs)
	reviewIn := buildReviewInput(st, mustDiff(ctx, e.git, st.RepoAbs), statusShort, coderOut.Patch, commandOutput.String())
	_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
		RunID:     st.RunID,
		Iteration: iteration,
		Tool:      "reviewer_start",
		Input:     truncateString(st.Spec.Goal, 500),
		Output:    "starting reviewer review",
		Status:    "started",
		CreatedAt: time.Now().UnixMilli(),
	})
	e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventReviewerReviewing, model.ProgressStatusStarted, "reviewer reviewing", nil)
	reviewCtx, cancelReview := context.WithTimeout(ctx, e.reviewerTimeout)
	reviewOut, err := e.reviewer.Review(reviewCtx, reviewIn)
	cancelReview()
	if err != nil {
		e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventReviewerReviewing, model.ProgressStatusError, "reviewer failed", map[string]any{
			"error": truncateString(err.Error(), 500),
		})
		_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
			RunID:     st.RunID,
			Iteration: iteration,
			Tool:      "reviewer_review",
			Input:     truncateString(st.Spec.Goal, 500),
			Output:    truncateString(err.Error(), 4000),
			Status:    "error",
			CreatedAt: time.Now().UnixMilli(),
		})
		_ = e.store.InsertStep(ctx, sqlite.StepRecord{
			RunID:     st.RunID,
			Iteration: iteration,
			Agent:     "reviewer",
			Decision:  string(model.LoopDecisionAbort),
			Status:    string(model.RunStatusFailed),
			StartedAt: started,
			EndedAt:   time.Now().UnixMilli(),
		})
		st.Decision = model.LoopDecisionAbort
		st.Status = model.RunStatusFailed
		st.Summary = truncateString("reviewer failed: "+err.Error(), 500)
		_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusFailed, st.Summary)
		return st, nil
	}
	if refreshedIn, refreshed := e.maybeRefreshReviewerContext(ctx, st, iteration, reviewIn, reviewOut); refreshed {
		reviewIn = refreshedIn
		reviewCtx, cancelReview = context.WithTimeout(ctx, e.reviewerTimeout)
		reviewOut, err = e.reviewer.Review(reviewCtx, reviewIn)
		cancelReview()
		if err != nil {
			e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventReviewerReviewing, model.ProgressStatusError, "reviewer failed after refresh", map[string]any{
				"error": truncateString(err.Error(), 500),
			})
			_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
				RunID:     st.RunID,
				Iteration: iteration,
				Tool:      "reviewer_review",
				Input:     truncateString(st.Spec.Goal, 500),
				Output:    truncateString(err.Error(), 4000),
				Status:    "error",
				CreatedAt: time.Now().UnixMilli(),
			})
			_ = e.store.InsertStep(ctx, sqlite.StepRecord{
				RunID:     st.RunID,
				Iteration: iteration,
				Agent:     "reviewer",
				Decision:  string(model.LoopDecisionAbort),
				Status:    string(model.RunStatusFailed),
				StartedAt: started,
				EndedAt:   time.Now().UnixMilli(),
			})
			st.Decision = model.LoopDecisionAbort
			st.Status = model.RunStatusFailed
			st.Summary = truncateString("reviewer failed after refresh: "+err.Error(), 500)
			_ = e.store.UpdateRunStatus(ctx, st.RunID, model.RunStatusFailed, st.Summary)
			return st, nil
		}
	}
	e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventReviewerReviewing, model.ProgressStatusCompleted, "reviewer completed", map[string]any{
		"decision": strings.TrimSpace(reviewOut.Decision),
	})
	reviewerMetaJSON := mustJSON(map[string]any{
		"used_fallback":   reviewOut.UsedFallback,
		"fallback_source": strings.TrimSpace(reviewOut.FallbackSource),
		"decision":        strings.TrimSpace(reviewOut.Decision),
	})
	_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
		RunID:     st.RunID,
		Iteration: iteration,
		Tool:      "reviewer_meta",
		Input:     "",
		Output:    reviewerMetaJSON,
		Status:    "completed",
		CreatedAt: time.Now().UnixMilli(),
	})
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
		e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventIterationComplete, model.ProgressStatusCompleted, "iteration completed: request changes", map[string]any{
			"decision": string(model.LoopDecisionRequestChanges),
		})
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
	e.emitProgress(ctx, st.RunID, iteration, model.ProgressEventIterationComplete, model.ProgressStatusCompleted, "iteration completed: complete", map[string]any{
		"decision": string(model.LoopDecisionComplete),
	})
	return st, nil
}

func buildCoderInput(st *loopSession, diff string) agentpkg.CoderInput {
	if st == nil {
		return agentpkg.CoderInput{}
	}
	return agentpkg.CoderInput{
		Goal:             st.Spec.Goal,
		RepoSummary:      st.RepoAbs,
		PreviousReview:   st.PreviousReview,
		Diff:             diff,
		TestOutput:       st.CommandOutput,
		Commands:         mergeCommands(st.Commands),
		SkillsSummary:    st.SkillsSummary,
		RetrievedContext: compactRetrievedContext(st.RetrievedHits),
		RetrievedQuery:   st.RetrievedQuery,
	}
}

func buildReviewInput(st *loopSession, diff, statusShort, patch, commandOutput string) agentpkg.ReviewInput {
	if st == nil {
		return agentpkg.ReviewInput{}
	}
	return agentpkg.ReviewInput{
		Goal:             st.Spec.Goal,
		RepoRoot:         st.RepoAbs,
		Diff:             diff,
		StatusShort:      statusShort,
		AppliedPatch:     patch,
		CommandOutput:    commandOutput,
		SkillsSummary:    st.SkillsSummary,
		KBSearchCalls:    st.KBSearchCalls,
		RetrievalMode:    st.Spec.RetrievalMode,
		RetrievedContext: compactRetrievedContext(st.RetrievedHits),
		RetrievedQuery:   st.RetrievedQuery,
	}
}

func compactRetrievedContext(hits []kbpkg.SearchHit) []kbpkg.SearchHit {
	if len(hits) == 0 {
		return nil
	}
	out := make([]kbpkg.SearchHit, 0, min(len(hits), promptRetrievedContextMaxHits))
	seen := make(map[string]struct{}, len(hits))
	for _, hit := range hits {
		key := retrievedContextChunkKey(hit)
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		hit.Text = truncateString(hit.Text, promptRetrievedTextMaxChars)
		out = append(out, hit)
		if len(out) == promptRetrievedContextMaxHits {
			break
		}
	}
	return out
}

func retrievedContextChunkKey(hit kbpkg.SearchHit) string {
	return fmt.Sprintf("%s:%d:%d", hit.Path, hit.Start, hit.End)
}

func shouldRefreshCoderContext(st *loopSession, iteration int) bool {
	if st == nil {
		return false
	}
	if iteration < 2 {
		return false
	}
	if st.Spec.RetrievalMode != model.RetrievalModePrefetch {
		return false
	}
	if st.Decision != model.LoopDecisionRequestChanges {
		return false
	}
	if strings.TrimSpace(st.PreviousReview) == "" {
		return false
	}
	return true
}

func (e *Engine) maybeRefreshCoderContext(ctx context.Context, st *loopSession, iteration int, in agentpkg.CoderInput) (agentpkg.CoderInput, bool) {
	if st == nil || e.kb == nil || strings.TrimSpace(e.kb.BaseURL) == "" {
		return in, false
	}
	if !shouldRefreshCoderContext(st, iteration) {
		return in, false
	}
	query := coderRefreshQuery(in)
	if query == "" {
		return in, false
	}
	searchCtx, cancel := context.WithTimeout(ctx, 12*time.Second)
	defer cancel()
	resp, err := e.kb.Search(searchCtx, kbpkg.SearchRequest{
		Query: query,
		TopK:  6,
	})
	status := "completed"
	output := formatKBSearchPrefetchOutput(resp)
	if err != nil {
		status = "error"
		output = truncateString(err.Error(), 4000)
	} else {
		st.RetrievedQuery = query
		st.RetrievedHits = mergeRetrievedHits(st.RetrievedHits, resp.Hits)
		st.KBSearchCalls++
		in = buildCoderInput(st, in.Diff)
	}
	if e.store != nil {
		_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
			RunID:     st.RunID,
			Iteration: iteration,
			Tool:      "coder_retrieval_refresh",
			Input:     query,
			Output:    output,
			Status:    status,
			CreatedAt: time.Now().UnixMilli(),
		})
	}
	return in, status == "completed"
}

func coderRefreshQuery(in agentpkg.CoderInput) string {
	base := strings.TrimSpace(in.RetrievedQuery)
	if base == "" {
		base = kbSearchQueryFromGoal(in.Goal)
	}
	parts := []string{base, "coder follow-up"}
	if review := truncateString(strings.TrimSpace(in.PreviousReview), coderRefreshReviewMaxChars); review != "" {
		parts = append(parts, review)
	}
	if hints := collectCoderPathHints(in); len(hints) > 0 {
		parts = append(parts, strings.Join(hints, " "))
	}
	return trimQueryLength(strings.Join(parts, " "))
}

func collectCoderPathHints(in agentpkg.CoderInput) []string {
	seen := map[string]struct{}{}
	out := make([]string, 0, 6)
	add := func(path string) {
		path = normalizeHintPath(path)
		if path == "" || !strings.Contains(path, ".") {
			return
		}
		if _, ok := seen[path]; ok {
			return
		}
		seen[path] = struct{}{}
		out = append(out, path)
	}
	for _, line := range strings.Split(in.Diff, "\n") {
		if strings.HasPrefix(line, "diff --git ") {
			parts := strings.Fields(line)
			if len(parts) >= 4 {
				add(parts[3])
			}
			continue
		}
		if strings.HasPrefix(line, "+++ ") {
			add(strings.TrimPrefix(line, "+++ "))
		}
	}
	for _, path := range collectGoalPathHints(in.Goal) {
		add(path)
	}
	sort.Strings(out)
	if len(out) > 3 {
		out = out[:3]
	}
	return out
}

func collectGoalPathHints(goal string) []string {
	raw := goalPathHintRE.FindAllString(goal, -1)
	if len(raw) == 0 {
		return nil
	}
	allowedExt := map[string]struct{}{
		".md": {}, ".go": {}, ".py": {}, ".rs": {}, ".ts": {}, ".tsx": {}, ".js": {}, ".jsx": {},
		".json": {}, ".yaml": {}, ".yml": {}, ".toml": {}, ".txt": {}, ".sql": {}, ".proto": {},
		".java": {}, ".kt": {}, ".swift": {}, ".c": {}, ".cc": {}, ".cpp": {}, ".h": {}, ".hpp": {},
		".sh": {},
	}
	seen := map[string]struct{}{}
	out := make([]string, 0, len(raw))
	for _, token := range raw {
		path := normalizeHintPath(token)
		if path == "" {
			continue
		}
		ext := strings.ToLower(filepath.Ext(path))
		if _, ok := allowedExt[ext]; !ok {
			continue
		}
		base := strings.ToLower(filepath.Base(path))
		if !strings.Contains(path, "/") && !(base == "readme.md" || strings.HasPrefix(base, "readme.")) {
			continue
		}
		if _, ok := seen[path]; ok {
			continue
		}
		seen[path] = struct{}{}
		out = append(out, path)
	}
	sort.Strings(out)
	return out
}

func shouldRefreshReviewerContext(in agentpkg.ReviewInput, out agentpkg.ReviewOutput) bool {
	if in.RetrievalMode != model.RetrievalModePrefetch {
		return false
	}
	if len(in.RetrievedContext) == 0 {
		return true
	}
	if in.KBSearchCalls <= 0 {
		return true
	}
	return reviewOutputSuggestsContextGap(out)
}

func reviewOutputSuggestsContextGap(out agentpkg.ReviewOutput) bool {
	if reviewOutputMentionsMissingKBSearch(out) {
		return true
	}
	low := strings.ToLower(strings.TrimSpace(out.Summary + "\n" + out.Markdown))
	patterns := []string{
		"retrieved_context",
		"context gap",
		"context missing",
		"insufficient context",
		"上下文不足",
		"检索上下文不足",
	}
	for _, pattern := range patterns {
		if strings.Contains(low, pattern) {
			return true
		}
	}
	return false
}

func reviewOutputMentionsMissingKBSearch(out agentpkg.ReviewOutput) bool {
	low := strings.ToLower(strings.TrimSpace(out.Summary + "\n" + out.Markdown))
	patterns := []string{
		"未按要求先调用 kb_search",
		"缺少 kb_search 调用证据",
		"必须先通过 kb_search",
		"missing kb_search",
		"must call kb_search",
	}
	for _, pattern := range patterns {
		if strings.Contains(low, pattern) {
			return true
		}
	}
	return false
}

func (e *Engine) maybeRefreshReviewerContext(ctx context.Context, st *loopSession, iteration int, in agentpkg.ReviewInput, out agentpkg.ReviewOutput) (agentpkg.ReviewInput, bool) {
	if st == nil || e.kb == nil || strings.TrimSpace(e.kb.BaseURL) == "" {
		return in, false
	}
	if !shouldRefreshReviewerContext(in, out) {
		return in, false
	}
	query := reviewerRefreshQuery(in)
	if query == "" {
		return in, false
	}
	searchCtx, cancel := context.WithTimeout(ctx, 12*time.Second)
	defer cancel()
	resp, err := e.kb.Search(searchCtx, kbpkg.SearchRequest{
		Query: query,
		TopK:  6,
	})
	status := "completed"
	output := formatKBSearchPrefetchOutput(resp)
	if err != nil {
		status = "error"
		output = truncateString(err.Error(), 4000)
	} else {
		st.RetrievedQuery = query
		st.RetrievedHits = mergeRetrievedHits(st.RetrievedHits, resp.Hits)
		st.KBSearchCalls++
		in.KBSearchCalls = st.KBSearchCalls
		in.RetrievedQuery = st.RetrievedQuery
		in.RetrievedContext = compactRetrievedContext(st.RetrievedHits)
	}
	if e.store != nil {
		_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
			RunID:     st.RunID,
			Iteration: iteration,
			Tool:      "reviewer_retrieval_refresh",
			Input:     query,
			Output:    output,
			Status:    status,
			CreatedAt: time.Now().UnixMilli(),
		})
	}
	return in, status == "completed"
}

func reviewerRefreshQuery(in agentpkg.ReviewInput) string {
	base := strings.TrimSpace(in.RetrievedQuery)
	if base == "" {
		base = kbSearchQueryFromGoal(in.Goal)
	}
	hints := collectReviewPathHints(in)
	if len(hints) == 0 {
		return trimQueryLength(strings.TrimSpace(base + " reviewer follow-up"))
	}
	return trimQueryLength(strings.TrimSpace(base + " reviewer follow-up " + strings.Join(hints, " ")))
}

func collectReviewPathHints(in agentpkg.ReviewInput) []string {
	seen := map[string]struct{}{}
	out := make([]string, 0, 6)
	add := func(path string) {
		path = normalizeHintPath(path)
		if path == "" {
			return
		}
		if _, ok := seen[path]; ok {
			return
		}
		seen[path] = struct{}{}
		out = append(out, path)
	}
	for _, text := range []string{in.Diff, in.AppliedPatch} {
		for _, line := range strings.Split(text, "\n") {
			if strings.HasPrefix(line, "diff --git ") {
				parts := strings.Fields(line)
				if len(parts) >= 4 {
					add(parts[3])
				}
				continue
			}
			if strings.HasPrefix(line, "+++ ") {
				add(strings.TrimPrefix(line, "+++ "))
			}
		}
	}
	for _, line := range strings.Split(in.StatusShort, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if idx := strings.Index(line, " -> "); idx >= 0 {
			add(line[idx+4:])
			continue
		}
		fields := strings.Fields(line)
		if len(fields) > 0 {
			add(fields[len(fields)-1])
		}
	}
	sort.Strings(out)
	if len(out) > 3 {
		out = out[:3]
	}
	return out
}

func normalizeHintPath(path string) string {
	path = strings.TrimSpace(path)
	path = strings.TrimPrefix(path, "a/")
	path = strings.TrimPrefix(path, "b/")
	path = strings.TrimPrefix(path, "./")
	if path == "" || path == "/dev/null" {
		return ""
	}
	return path
}

func trimQueryLength(query string) string {
	query = strings.TrimSpace(strings.ReplaceAll(query, "\n", " "))
	if query == "" {
		return "project review context"
	}
	runes := []rune(query)
	if len(runes) > 240 {
		return string(runes[:240])
	}
	return query
}

func mergeRetrievedHits(base, extra []kbpkg.SearchHit) []kbpkg.SearchHit {
	if len(base) == 0 && len(extra) == 0 {
		return nil
	}
	seen := map[string]struct{}{}
	out := make([]kbpkg.SearchHit, 0, len(base)+len(extra))
	appendHit := func(hit kbpkg.SearchHit) {
		key := hit.ID
		if key == "" {
			key = fmt.Sprintf("%s:%d:%d", hit.Path, hit.Start, hit.End)
		}
		if _, ok := seen[key]; ok {
			return
		}
		seen[key] = struct{}{}
		out = append(out, hit)
	}
	for _, hit := range base {
		appendHit(hit)
	}
	for _, hit := range extra {
		appendHit(hit)
	}
	return out
}

func (e *Engine) maybePreflightKBSearch(ctx context.Context, st *loopSession, iteration int) {
	if st == nil || st.KBPrefetched {
		return
	}
	if st.Spec.RetrievalMode != model.RetrievalModePrefetch {
		return
	}
	st.KBPrefetched = true
	if e.kb == nil || strings.TrimSpace(e.kb.BaseURL) == "" {
		return
	}
	query := kbSearchQueryFromGoal(st.Spec.Goal)
	st.RetrievedQuery = query
	searchCtx, cancel := context.WithTimeout(ctx, 12*time.Second)
	defer cancel()
	resp, err := e.kb.Search(searchCtx, kbpkg.SearchRequest{
		Query: query,
		TopK:  6,
	})
	status := "completed"
	output := formatKBSearchPrefetchOutput(resp)
	if err != nil {
		status = "error"
		output = truncateString(err.Error(), 4000)
	} else {
		st.RetrievedHits = resp.Hits
	}
	_ = e.store.InsertToolCall(ctx, sqlite.ToolCallRecord{
		RunID:     st.RunID,
		Iteration: iteration,
		Tool:      "retrieval_preflight",
		Input:     query,
		Output:    output,
		Status:    status,
		CreatedAt: time.Now().UnixMilli(),
	})
	if status == "completed" {
		st.KBSearchCalls++
	}
}

func kbSearchQueryFromGoal(goal string) string {
	q := strings.TrimSpace(goal)
	if idx := strings.Index(q, "\n\n约束"); idx > 0 {
		q = strings.TrimSpace(q[:idx])
	}
	q = strings.ReplaceAll(q, "\n", " ")
	q = strings.TrimSpace(q)
	if q == "" {
		return "project task context"
	}
	if goalSuggestsTestingKnowledge(q) {
		q = strings.TrimSpace(q + " testing standards table-driven positive negative cases")
	}
	runes := []rune(q)
	if len(runes) > 240 {
		return string(runes[:240])
	}
	return q
}

func goalSuggestsTestingKnowledge(goal string) bool {
	low := strings.ToLower(strings.TrimSpace(goal))
	if low == "" {
		return false
	}
	if strings.Contains(low, "_test.go") {
		return true
	}
	needles := []string{
		"测试",
		"test case",
		"unit test",
		"table-driven",
	}
	for _, needle := range needles {
		if strings.Contains(low, needle) {
			return true
		}
	}
	return false
}

func formatKBSearchPrefetchOutput(resp kbpkg.SearchResponse) string {
	if len(resp.Hits) == 0 {
		return "no hits"
	}
	var b strings.Builder
	limit := len(resp.Hits)
	if limit > 6 {
		limit = 6
	}
	for i := 0; i < limit; i++ {
		h := resp.Hits[i]
		ref := strings.TrimSpace(h.Path)
		if heading := strings.TrimSpace(h.Heading); heading != "" {
			ref = ref + "#" + heading
		}
		b.WriteString(fmt.Sprintf("[%d] %s (%d-%d)\n", i+1, ref, h.Start, h.End))
		txt := strings.TrimSpace(h.Text)
		if len(txt) > 280 {
			txt = txt[:280]
		}
		if txt != "" {
			b.WriteString(txt + "\n")
		}
	}
	return strings.TrimSpace(b.String())
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
		e.emitProgress(ctx, st.RunID, 0, model.ProgressEventRunFailed, model.ProgressStatusError, "run failed", map[string]any{
			"error": truncateString(err.Error(), 500),
		})
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
	e.emitProgress(ctx, st.RunID, 0, model.ProgressEventRunCompleted, model.ProgressStatusCompleted, "run completed", map[string]any{
		"branch":        result.Branch,
		"commit":        result.Commit,
		"pr_url":        result.PRURL,
		"artifacts_dir": result.ArtifactsDir,
	})
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
	e.emitProgress(ctx, st.RunID, 0, model.ProgressEventRunBlocked, model.ProgressStatusError, st.Summary, map[string]any{
		"reason":        "doom_loop",
		"blocked_tool":  st.DoomLastTool,
		"blocked_count": st.DoomCount,
	})
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
	e.emitProgress(ctx, st.RunID, 0, model.ProgressEventRunFailed, model.ProgressStatusError, st.Summary, map[string]any{
		"error": truncateString(st.Summary, 500),
	})
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
	remoteURL, _ := e.git.RemoteURL(ctx, repo)
	effectiveMode := e.github.ResolvePRMode(ctx, requestedPRMode, remoteURL)
	prURL := ""
	artifactsDir := ""

	reviewMD := review.Markdown
	if strings.TrimSpace(reviewMD) == "" {
		reviewMD = review.Summary
	}

	commitHash := ""
	paths := statusDeltaPaths(baselineStatus, status)
	if effectiveMode == model.PRModeLive && len(paths) > 0 {
		commitHash, err = e.git.CommitPaths(ctx, repo, paths, "feat: agent loop generated update")
		if err != nil {
			_ = e.store.UpdateRunStatus(ctx, runID, model.RunStatusFailed, "git commit failed")
			return model.RunResult{RunID: runID, Status: model.RunStatusFailed}, err
		}
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
		diff, _ := e.git.Diff(ctx, repo)
		if strings.TrimSpace(diff) != "" {
			_ = os.WriteFile(filepath.Join(artifactsDir, "diff.patch"), []byte(diff), 0o644)
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

func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return "{}"
	}
	return string(b)
}

func mergeCommands(set model.CommandSet) []string {
	out := make([]string, 0, len(set.Test)+len(set.Lint)+len(set.Build))
	out = append(out, set.Test...)
	out = append(out, set.Lint...)
	out = append(out, set.Build...)
	return out
}

func sanitizeShellCommands(in []string) []string {
	if len(in) == 0 {
		return nil
	}
	toolNames := map[string]struct{}{
		"repo_list":   {},
		"repo_read":   {},
		"repo_search": {},
		"git_diff":    {},
		"list_skills": {},
		"view_skill":  {},
		"run_command": {},
	}
	out := make([]string, 0, len(in))
	seen := make(map[string]struct{})
	for _, raw := range in {
		cmd := strings.TrimSpace(raw)
		if cmd == "" {
			continue
		}
		fields := strings.Fields(cmd)
		if len(fields) == 0 {
			continue
		}
		if _, ok := toolNames[strings.ToLower(fields[0])]; ok {
			continue
		}
		if _, ok := seen[cmd]; ok {
			continue
		}
		seen[cmd] = struct{}{}
		out = append(out, cmd)
	}
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

func truncateString(s string, max int) string {
	s = strings.TrimSpace(s)
	if max < 1 || len(s) <= max {
		return s
	}
	return s[:max]
}

var loopGoalFileTokenRE = regexp.MustCompile(`[A-Za-z0-9_./\\-]+\.[A-Za-z0-9_+-]+`)

func loopExtractGoalTargetFiles(goal string) []string {
	raw := loopGoalFileTokenRE.FindAllString(goal, -1)
	if len(raw) == 0 {
		return []string{}
	}
	allowedExt := map[string]struct{}{
		".md": {}, ".go": {}, ".py": {}, ".rs": {}, ".ts": {}, ".tsx": {}, ".js": {}, ".jsx": {},
		".json": {}, ".yaml": {}, ".yml": {}, ".toml": {}, ".txt": {}, ".sql": {}, ".proto": {},
		".java": {}, ".kt": {}, ".swift": {}, ".c": {}, ".cc": {}, ".cpp": {}, ".h": {}, ".hpp": {},
		".sh": {},
	}
	out := make([]string, 0, len(raw))
	seen := make(map[string]struct{}, len(raw))
	for _, token := range raw {
		p := loopNormalizePathForCompare(token)
		if p == "" {
			continue
		}
		if _, ok := allowedExt[strings.ToLower(filepath.Ext(p))]; !ok {
			continue
		}
		base := strings.ToLower(filepath.Base(p))
		if !strings.Contains(p, "/") && !(base == "readme.md" || strings.HasPrefix(base, "readme.")) {
			continue
		}
		if strings.Contains(strings.ToLower(p), "xxx.") {
			continue
		}
		if _, ok := seen[p]; ok {
			continue
		}
		seen[p] = struct{}{}
		out = append(out, p)
	}
	sort.Strings(out)
	return out
}

func loopPatchTouchesTargets(patch string, targets []string, requireAll bool) bool {
	if strings.TrimSpace(patch) == "" || len(targets) == 0 {
		return false
	}
	changed := loopExtractChangedFiles(patch)
	if len(changed) == 0 {
		return false
	}
	if requireAll {
		for _, target := range targets {
			if _, ok := changed[target]; !ok {
				return false
			}
		}
		return true
	}
	for _, target := range targets {
		if _, ok := changed[target]; ok {
			return true
		}
	}
	return false
}

func loopExtractChangedFiles(diff string) map[string]struct{} {
	out := make(map[string]struct{})
	for _, line := range strings.Split(strings.ReplaceAll(diff, "\r\n", "\n"), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "+++ ") {
			p := loopNormalizePathForCompare(strings.TrimSpace(strings.TrimPrefix(line, "+++ ")))
			if p != "" {
				out[p] = struct{}{}
			}
			continue
		}
		if strings.HasPrefix(line, "diff --git ") {
			fields := strings.Fields(line)
			if len(fields) >= 4 {
				p := loopNormalizePathForCompare(fields[3])
				if p != "" {
					out[p] = struct{}{}
				}
			}
		}
	}
	return out
}

func loopNormalizePathForCompare(path string) string {
	path = strings.TrimSpace(path)
	if path == "" || path == "/dev/null" {
		return ""
	}
	path = strings.Trim(path, "`'\"()[]{}<>.,;:!?，。；：！、）】》”")
	path = strings.ReplaceAll(path, "\\", "/")
	path = strings.TrimPrefix(path, "a/")
	path = strings.TrimPrefix(path, "b/")
	path = strings.TrimPrefix(path, "./")
	path = filepath.ToSlash(filepath.Clean(path))
	if path == "." || path == "/" {
		return ""
	}
	return path
}

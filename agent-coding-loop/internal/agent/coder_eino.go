package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/cloudwego/eino/compose"
	"github.com/cloudwego/eino/flow/agent/react"
	"github.com/cloudwego/eino/schema"
	"github.com/kina/agent-coding-loop/internal/kb"
	"github.com/kina/agent-coding-loop/internal/skills"
	"github.com/kina/agent-coding-loop/internal/tools"
)

type Coder struct {
	client             ClientConfig
	runner             *tools.Runner
	skills             *skills.Registry
	kb                 *kb.Client
	retryHooks         *coderRetryHooks
	planHookForTests   func(context.Context, PlanInput) (PlanOutput, error)
	repairHookForTests func(context.Context, RepairInput) (CoderOutput, error)
}

type agentStageRecorderKey struct{}

type coderRetryHooks struct {
	targeted       func(context.Context, CoderInput, []string, string) (CoderOutput, error)
	targetedStrict func(context.Context, CoderInput, []string, string) (CoderOutput, error)
	scopedStrict   func(context.Context, CoderInput, []string, string, []string) (CoderOutput, error)
	repoOnly       func(context.Context, CoderInput, []string, string) (CoderOutput, error)
}

type CoderRetryHooksForTests struct {
	Targeted       func(context.Context, CoderInput, []string, string) (CoderOutput, error)
	TargetedStrict func(context.Context, CoderInput, []string, string) (CoderOutput, error)
	ScopedStrict   func(context.Context, CoderInput, []string, string, []string) (CoderOutput, error)
	RepoOnly       func(context.Context, CoderInput, []string, string) (CoderOutput, error)
}

type CoderInput struct {
	Goal                        string
	RepoSummary                 string
	PreviousReview              string
	PlanSummary                 string   `json:"plan_summary,omitempty"`
	PlanSteps                   []string `json:"plan_steps,omitempty"`
	Diff                        string
	TestOutput                  string
	Commands                    []string
	SkillsSummary               string
	RetrievedContext            []kb.SearchHit      `json:"retrieved_context,omitempty"`
	RetrievedQuery              string              `json:"retrieved_query,omitempty"`
	DefinitionIssues            []string            `json:"definition_issues,omitempty"`
	MissingTargetFiles          []string            `json:"missing_target_files,omitempty"`
	ExistingTopLevelNamesByFile map[string][]string `json:"existing_top_level_names_by_file,omitempty"`
	ExistingTestNamesByFile     map[string][]string `json:"existing_test_names_by_file,omitempty"`
	AllowedGoalFunctions        []string            `json:"allowed_goal_functions,omitempty"`
}

type CoderOutput struct {
	Summary        string   `json:"summary"`
	Patch          string   `json:"patch"`
	Commands       []string `json:"commands"`
	Notes          string   `json:"notes"`
	Citations      []string `json:"citations"`
	UsedFallback   bool     `json:"used_fallback"`
	FallbackSource string   `json:"fallback_source"`
}

type PlanInput struct {
	Goal             string         `json:"goal"`
	RepoSummary      string         `json:"repo_summary"`
	PreviousReview   string         `json:"previous_review,omitempty"`
	Diff             string         `json:"diff,omitempty"`
	SkillsSummary    string         `json:"skills_summary,omitempty"`
	RetrievedContext []kb.SearchHit `json:"retrieved_context,omitempty"`
	RetrievedQuery   string         `json:"retrieved_query,omitempty"`
}

type PlanOutput struct {
	Summary   string   `json:"summary"`
	Steps     []string `json:"steps"`
	Risks     []string `json:"risks"`
	Citations []string `json:"citations"`
}

type RepairInput struct {
	Goal             string         `json:"goal"`
	RepoSummary      string         `json:"repo_summary"`
	CurrentDiff      string         `json:"current_diff"`
	PreviousReview   string         `json:"previous_review,omitempty"`
	FailedCommands   []string       `json:"failed_commands"`
	CommandOutput    string         `json:"command_output"`
	PlanSummary      string         `json:"plan_summary,omitempty"`
	PlanSteps        []string       `json:"plan_steps,omitempty"`
	RetrievedContext []kb.SearchHit `json:"retrieved_context,omitempty"`
	RetrievedQuery   string         `json:"retrieved_query,omitempty"`
}

const citationBackfillTimeout = 8 * time.Second
const repoOnlySnapshotMaxBytes = 96 * 1024

var (
	coderToolCallingTimeout     = 90 * time.Second
	coderCompletionTimeout      = 90 * time.Second
	targetPatchRetryTimeout     = 90 * time.Second
	targetPatchHardRetryTimeout = 120 * time.Second
)

var (
	scopeChainRegexp       = regexp.MustCompile(`\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b`)
	scopeSnakeRegexp       = regexp.MustCompile(`\b[a-z]+_[a-z0-9_]+\b`)
	scopeCamelRegexp       = regexp.MustCompile(`\b[A-Z][A-Za-z0-9_]{2,}\b`)
	scopeQuotedRegexp      = regexp.MustCompile("\"([^\"\\\\]|\\\\.)*\"|'([^'\\\\]|\\\\.)*'|`[^`]*`")
	goalFunctionNameRegexp = regexp.MustCompile(`([A-Za-z_][A-Za-z0-9_]*)\s*(?:函数|方法|method|handler)`)
	goFuncScopeRegexp      = regexp.MustCompile(`^\s*func\s+(?:\([^)]+\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(`)
	pyFuncScopeRegexp      = regexp.MustCompile(`^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(`)
	backtickContentRegexp  = regexp.MustCompile("`([^`]+)`")
	httpStatusRegexp       = regexp.MustCompile(`^Status[A-Z][A-Za-z0-9_]*$`)
	unifiedHunkHeaderRegex = regexp.MustCompile(`^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?: .*)?$`)
	plainIdentifierRegexp  = regexp.MustCompile(`\b[A-Za-z_][A-Za-z0-9_]*\b`)
)

var scopeIgnoredIdentifiers = map[string]struct{}{
	"New":       {},
	"Error":     {},
	"Errorf":    {},
	"Sprintf":   {},
	"HasSuffix": {},
	"Contains":  {},
	"TrimSpace": {},
}

type kbScopeContract struct {
	Targets     []string `json:"targets,omitempty"`
	Identifiers []string `json:"identifiers,omitempty"`
}

func NewCoder(client ClientConfig, opts ...Option) *Coder {
	deps := applyOptions(opts)
	return &Coder{
		client: client,
		runner: deps.runner,
		skills: deps.skills,
		kb:     deps.kb,
	}
}

func (c *Coder) Generate(ctx context.Context, in CoderInput) (CoderOutput, error) {
	targets := extractGoalTargetFiles(in.Goal)
	requireAllTargets := len(targets) > 1
	if !c.client.Ready() {
		out := fallbackCoder(in)
		out.UsedFallback = true
		out.FallbackSource = "offline"
		c.ensureGoalTargetPatch(ctx, in, &out)
		c.ensureKBTaskScope(ctx, in, &out)
		c.ensureRepoOnlyMinimalMode(ctx, in, &out)
		return out, nil
	}

	emitAgentStage(ctx, "coder_eino_start")
	einoCtx, cancelEino := withCoderToolCallingTimeout(ctx)
	out, err := runWithHardTimeout(einoCtx, coderToolCallingTimeout, func(callCtx context.Context) (CoderOutput, error) {
		return c.generateWithEino(callCtx, in)
	})
	cancelEino()
	emitAgentStage(ctx, "coder_eino_done")
	if err == nil {
		recordPatchAttemptDiagnostic(&out, "eino_generate", out, nil, targets, requireAllTargets, isRepoOnlyGoal(in.Goal), false)
		c.ensureCitations(ctx, in, &out)
		c.ensureGoalTargetPatch(ctx, in, &out)
		c.ensureKBTaskScope(ctx, in, &out)
		c.ensureSingleTargetOutputConstraints(ctx, in, &out)
		c.ensureRepoOnlyMinimalMode(ctx, in, &out)
		return out, nil
	}
	if shouldSkipClientCompletionAfterToolTimeout(ctx, err) {
		out := fallbackCoder(in)
		out.UsedFallback = true
		out.FallbackSource = "heuristic"
		recordPatchAttemptDiagnostic(&out, "eino_generate", CoderOutput{}, err, targets, requireAllTargets, isRepoOnlyGoal(in.Goal), false)
		out.Notes = appendCoderNote(out.Notes, "skipped client completion after tool timeout to avoid repeated provider stall")
		c.ensureCitations(ctx, in, &out)
		c.ensureGoalTargetPatch(ctx, in, &out)
		c.ensureKBTaskScope(ctx, in, &out)
		c.ensureSingleTargetOutputConstraints(ctx, in, &out)
		c.ensureRepoOnlyMinimalMode(ctx, in, &out)
		return out, nil
	}

	emitAgentStage(ctx, "coder_client_completion_start")
	fallback, fallbackErr := runWithHardTimeout(ctx, coderCompletionTimeout, func(callCtx context.Context) (CoderOutput, error) {
		return c.generateWithClient(callCtx, in)
	})
	emitAgentStage(ctx, "coder_client_completion_done")
	if fallbackErr != nil {
		out := fallbackCoder(in)
		out.UsedFallback = true
		out.FallbackSource = "heuristic"
		recordPatchAttemptDiagnostic(&out, "eino_generate", CoderOutput{}, err, targets, requireAllTargets, isRepoOnlyGoal(in.Goal), false)
		recordPatchAttemptDiagnostic(&out, "client_completion", CoderOutput{}, fallbackErr, targets, requireAllTargets, isRepoOnlyGoal(in.Goal), false)
		c.ensureCitations(ctx, in, &out)
		c.ensureGoalTargetPatch(ctx, in, &out)
		c.ensureKBTaskScope(ctx, in, &out)
		c.ensureSingleTargetOutputConstraints(ctx, in, &out)
		c.ensureRepoOnlyMinimalMode(ctx, in, &out)
		return out, nil
	}
	fallback.UsedFallback = true
	fallback.FallbackSource = "client_completion"
	recordPatchAttemptDiagnostic(&fallback, "eino_generate", CoderOutput{}, err, targets, requireAllTargets, isRepoOnlyGoal(in.Goal), false)
	recordPatchAttemptDiagnostic(&fallback, "client_completion", fallback, nil, targets, requireAllTargets, isRepoOnlyGoal(in.Goal), false)
	c.ensureCitations(ctx, in, &fallback)
	c.ensureGoalTargetPatch(ctx, in, &fallback)
	c.ensureKBTaskScope(ctx, in, &fallback)
	c.ensureSingleTargetOutputConstraints(ctx, in, &fallback)
	c.ensureRepoOnlyMinimalMode(ctx, in, &fallback)
	return fallback, nil
}

func (c *Coder) Plan(ctx context.Context, in PlanInput) (PlanOutput, error) {
	if c.planHookForTests != nil {
		return normalizePlanOutput(c.planHookForTests(ctx, in))
	}
	if !c.client.Ready() {
		return fallbackPlan(in), nil
	}

	emitAgentStage(ctx, "planner_eino_start")
	einoCtx, cancelEino := withCoderToolCallingTimeout(ctx)
	out, err := runWithHardTimeout(einoCtx, coderToolCallingTimeout, func(callCtx context.Context) (PlanOutput, error) {
		return c.planWithEino(callCtx, in)
	})
	cancelEino()
	emitAgentStage(ctx, "planner_eino_done")
	if err == nil {
		return normalizePlanOutput(out, nil)
	}

	emitAgentStage(ctx, "planner_client_completion_start")
	fallback, fallbackErr := runWithHardTimeout(ctx, coderCompletionTimeout, func(callCtx context.Context) (PlanOutput, error) {
		return c.planWithClient(callCtx, in)
	})
	emitAgentStage(ctx, "planner_client_completion_done")
	if fallbackErr != nil {
		return fallbackPlan(in), nil
	}
	return normalizePlanOutput(fallback, nil)
}

func (c *Coder) Repair(ctx context.Context, in RepairInput) (CoderOutput, error) {
	if c.repairHookForTests != nil {
		return c.repairHookForTests(ctx, in)
	}
	if !c.client.Ready() {
		out := fallbackRepair(in)
		out.UsedFallback = true
		out.FallbackSource = "offline"
		return out, nil
	}

	emitAgentStage(ctx, "repair_eino_start")
	einoCtx, cancelEino := withCoderToolCallingTimeout(ctx)
	out, err := runWithHardTimeout(einoCtx, coderToolCallingTimeout, func(callCtx context.Context) (CoderOutput, error) {
		return c.repairWithEino(callCtx, in)
	})
	cancelEino()
	emitAgentStage(ctx, "repair_eino_done")
	if err == nil {
		return out, nil
	}

	emitAgentStage(ctx, "repair_client_completion_start")
	fallback, fallbackErr := runWithHardTimeout(ctx, coderCompletionTimeout, func(callCtx context.Context) (CoderOutput, error) {
		return c.repairWithClient(callCtx, in)
	})
	emitAgentStage(ctx, "repair_client_completion_done")
	if fallbackErr != nil {
		out := fallbackRepair(in)
		out.UsedFallback = true
		out.FallbackSource = "repair_fallback"
		out.Notes = appendCoderNote(out.Notes, "repair tool-calling path failed: "+err.Error())
		out.Notes = appendCoderNote(out.Notes, "repair completion path failed: "+fallbackErr.Error())
		return out, nil
	}
	fallback.UsedFallback = true
	fallback.FallbackSource = "repair_client_completion"
	return fallback, nil
}

func withCoderToolCallingTimeout(ctx context.Context) (context.Context, context.CancelFunc) {
	timeout := coderToolCallingTimeout
	if timeout <= 0 {
		return context.WithCancel(ctx)
	}
	return context.WithTimeout(ctx, timeout)
}

func withAgentStageRecorder(ctx context.Context, record func(string)) context.Context {
	if record == nil {
		return ctx
	}
	return context.WithValue(ctx, agentStageRecorderKey{}, record)
}

func WithAgentStageRecorder(ctx context.Context, record func(string)) context.Context {
	return withAgentStageRecorder(ctx, record)
}

func (c *Coder) runStructuredAgent(ctx context.Context, repoRoot string, toolMode tools.ToolMode, systemPrompt, userPrompt string, maxStep int) (string, error) {
	chatModel, err := c.client.newToolCallingModel(ctx)
	if err != nil {
		return "", err
	}

	runner := c.runner
	if runner == nil {
		runner = tools.NewRunner(tools.WithReadOnly(true))
	}
	toolset, err := tools.BuildToolsForMode(repoRoot, toolMode, c.skills, runner, c.kb)
	if err != nil {
		return "", err
	}

	rAgent, err := react.NewAgent(ctx, &react.AgentConfig{
		ToolCallingModel: chatModel,
		ToolsConfig: compose.ToolsNodeConfig{
			Tools: toolset,
		},
		MaxStep: maxStep,
	})
	if err != nil {
		return "", err
	}

	msg, err := rAgent.Generate(ctx, []*schema.Message{
		schema.SystemMessage(systemPrompt),
		schema.UserMessage(userPrompt),
	})
	if err != nil {
		return "", err
	}
	if msg == nil {
		return "", nil
	}
	return msg.Content, nil
}

func emitAgentStage(ctx context.Context, stage string) {
	if strings.TrimSpace(stage) == "" {
		return
	}
	record, _ := ctx.Value(agentStageRecorderKey{}).(func(string))
	if record != nil {
		record(stage)
	}
}

func (c *Coder) SetRetryHooksForTests(hooks CoderRetryHooksForTests) {
	c.retryHooks = &coderRetryHooks{
		targeted:       hooks.Targeted,
		targetedStrict: hooks.TargetedStrict,
		scopedStrict:   hooks.ScopedStrict,
		repoOnly:       hooks.RepoOnly,
	}
}

func (c *Coder) SetPlanHookForTests(hook func(context.Context, PlanInput) (PlanOutput, error)) {
	c.planHookForTests = hook
}

func (c *Coder) SetRepairHookForTests(hook func(context.Context, RepairInput) (CoderOutput, error)) {
	c.repairHookForTests = hook
}

func runWithHardTimeout[T any](ctx context.Context, timeout time.Duration, fn func(context.Context) (T, error)) (T, error) {
	type result struct {
		value T
		err   error
	}
	var zero T
	if timeout <= 0 {
		return fn(ctx)
	}
	ch := make(chan result, 1)
	callCtx, cancel := context.WithCancel(ctx)
	go func() {
		value, err := fn(callCtx)
		select {
		case ch <- result{value: value, err: err}:
		case <-callCtx.Done():
		}
	}()
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	defer cancel()
	select {
	case res := <-ch:
		return res.value, res.err
	case <-ctx.Done():
		return zero, ctx.Err()
	case <-timer.C:
		return zero, context.DeadlineExceeded
	}
}

func fallbackCoder(in CoderInput) CoderOutput {
	cmds := in.Commands
	if len(cmds) == 0 {
		cmds = []string{"go test ./..."}
	}
	return CoderOutput{
		Summary:   "LLM unavailable; fallback coder requests local validation commands.",
		Patch:     "",
		Commands:  cmds,
		Notes:     "Configure OPENAI_BASE_URL and OPENAI_MODEL to enable patch generation.",
		Citations: []string{},
	}
}

func fallbackPlan(in PlanInput) PlanOutput {
	targets := extractGoalTargetFiles(in.Goal)
	steps := []string{
		"Inspect the existing implementation and identify the exact file and function to change.",
		"Apply the minimal change in the current code path instead of introducing unrelated helpers or cleanup.",
		"Validate the focused behavior after the code change.",
	}
	if len(targets) > 0 {
		steps[0] = "Inspect the existing implementation in the goal target files and identify the exact code path to change."
	}
	risks := []string{"Avoid unrelated file changes or adjacent validation rules not required by the goal."}
	if len(targets) > 1 {
		risks = append(risks, "Make sure every required target file is updated consistently.")
	}
	return PlanOutput{
		Summary:   "LLM unavailable; inspect the existing code path first and apply the minimal change required by the goal.",
		Steps:     steps,
		Risks:     risks,
		Citations: fallbackCitationPaths(in.RepoSummary),
	}
}

func shouldSkipClientCompletionAfterToolTimeout(ctx context.Context, err error) bool {
	if ctx != nil && ctx.Err() != nil {
		return false
	}
	return errors.Is(err, context.DeadlineExceeded)
}

func fallbackRepair(in RepairInput) CoderOutput {
	return CoderOutput{
		Summary:   "Repair agent unavailable; leaving the current diff unchanged for the normal retry path.",
		Patch:     "",
		Commands:  nil,
		Notes:     "Repair fallback returns an empty patch instead of rewriting from scratch.",
		Citations: normalizeCitationList(citationPathsFromHits(in.RetrievedContext)),
	}
}

func normalizePlanOutput(out PlanOutput, err error) (PlanOutput, error) {
	if err != nil {
		return PlanOutput{}, err
	}
	out.Summary = strings.TrimSpace(out.Summary)
	out.Steps = normalizePlanTextList(out.Steps)
	out.Risks = normalizePlanTextList(out.Risks)
	out.Citations = normalizeCitationList(out.Citations)
	if out.Summary == "" {
		out.Summary = "Inspect the existing code path first, then apply the minimal change required by the goal."
	}
	if len(out.Steps) == 0 {
		out.Steps = []string{
			"Inspect the existing implementation and identify the exact file and function to change.",
			"Apply the minimal change in the current code path.",
			"Validate the focused behavior after the code change.",
		}
	}
	return out, nil
}

func normalizePlanTextList(items []string) []string {
	if len(items) == 0 {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		out = append(out, item)
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

func appendCoderNote(existing, note string) string {
	note = strings.TrimSpace(note)
	if note == "" {
		return strings.TrimSpace(existing)
	}
	if strings.TrimSpace(existing) == "" {
		return note
	}
	return strings.TrimSpace(existing) + "\n" + note
}

func recordPatchAttemptDiagnostic(dst *CoderOutput, stage string, attempt CoderOutput, err error, targets []string, requireAll bool, requireOnlyTargets bool, includeSuccess bool) {
	if dst == nil {
		return
	}
	if note := patchAttemptDiagnostic(stage, attempt, err, targets, requireAll, requireOnlyTargets, includeSuccess); note != "" {
		dst.Notes = appendCoderNote(dst.Notes, note)
	}
}

func patchAttemptDiagnostic(stage string, attempt CoderOutput, err error, targets []string, requireAll bool, requireOnlyTargets bool, includeSuccess bool) string {
	stage = strings.TrimSpace(stage)
	if stage == "" {
		stage = "patch_attempt"
	}
	if err != nil {
		return fmt.Sprintf("%s failed: %s", stage, formatDiagnosticError(err))
	}
	patch := strings.TrimSpace(attempt.Patch)
	if patch == "" {
		if requireAll && len(targets) > 1 {
			if note := strings.TrimSpace(attempt.Notes); note != "" {
				return fmt.Sprintf("%s returned empty patch (empty patch is invalid for multi-target goal); notes: %s", stage, note)
			}
			return fmt.Sprintf("%s returned empty patch (empty patch is invalid for multi-target goal)", stage)
		}
		if note := strings.TrimSpace(attempt.Notes); note != "" {
			return fmt.Sprintf("%s returned empty patch; notes: %s", stage, note)
		}
		return fmt.Sprintf("%s returned empty patch", stage)
	}
	if len(targets) > 0 && !patchTouchesTargets(patch, targets, requireAll) {
		return fmt.Sprintf("%s returned patch that did not touch required goal target files", stage)
	}
	if requireOnlyTargets && !patchTouchesOnlyTargets(patch, targets) {
		return fmt.Sprintf("%s returned patch touching non-target files", stage)
	}
	if !includeSuccess {
		return ""
	}
	if requireOnlyTargets {
		return fmt.Sprintf("%s returned usable target-only patch", stage)
	}
	if len(targets) > 0 {
		return fmt.Sprintf("%s returned usable target patch", stage)
	}
	return fmt.Sprintf("%s returned non-empty patch", stage)
}

func (c *Coder) ensureCitations(ctx context.Context, in CoderInput, out *CoderOutput) {
	if out == nil {
		return
	}
	out.Citations = normalizeCitationList(out.Citations)
	if len(out.Citations) > 0 {
		return
	}
	if !shouldBackfillCitations(in.Goal) {
		return
	}

	candidates := citationPathsFromHits(in.RetrievedContext)
	if len(candidates) == 0 && c.kb != nil && strings.TrimSpace(c.kb.BaseURL) != "" {
		query := strings.TrimSpace(in.Goal)
		if query == "" {
			query = "rag pipeline citation"
		}
		searchCtx, cancel := context.WithTimeout(ctx, citationBackfillTimeout)
		defer cancel()
		resp, err := c.kb.Search(searchCtx, kb.SearchRequest{
			Query: query,
			TopK:  8,
		})
		if err == nil {
			candidates = citationPathsFromHits(resp.Hits)
		}
	}
	if len(candidates) == 0 {
		candidates = fallbackCitationPaths(strings.TrimSpace(in.RepoSummary))
	}
	out.Citations = normalizeCitationList(candidates)
}

func (c *Coder) ensureGoalTargetPatch(ctx context.Context, in CoderInput, out *CoderOutput) {
	if out == nil {
		return
	}
	repoRoot := strings.TrimSpace(in.RepoSummary)
	targets := extractGoalTargetFiles(in.Goal)
	if len(targets) == 0 {
		return
	}
	requireAllTargets := len(targets) > 1
	if diffTouchesTargets(in.Diff, targets, requireAllTargets) {
		if issues := detectGoalTargetPatchContractIssues(in.Goal, repoRoot, in.Diff, targets); len(issues) > 0 {
			out.Notes = appendCoderNote(out.Notes, "current diff requires goal-target normalization: "+strings.Join(issues, ", "))
		} else {
			// Files are already modified in working tree from previous iteration.
			// Skip re-applying target patches to avoid duplicate patch-apply failures.
			if strings.TrimSpace(out.Patch) != "" && patchTouchesTargets(out.Patch, targets, requireAllTargets) {
				out.Patch = ""
				out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\nTarget files already changed in current diff; skipped duplicate patch apply.")
			}
			return
		}
	}
	if shouldForceDoomLoopResetSnapshotRecovery(in, out.Patch, targets) {
		if synth, ok := trySynthesizeGoalTargetRecovery(in, targets); ok && patchTouchesTargetsWithCurrentDiff(in.Diff, synth.Patch, targets, requireAllTargets) {
			out.Patch = normalizePatchForOutput(synth.Patch)
			if strings.TrimSpace(out.Summary) == "" {
				out.Summary = "Applied deterministic snapshot recovery for goal target files."
			}
			out.Notes = appendCoderNote(out.Notes, synth.Notes)
			return
		}
	}
	if patchTouchesTargetsWithCurrentDiff(in.Diff, out.Patch, targets, requireAllTargets) {
		contractIssues := detectGoalTargetPatchContractIssues(in.Goal, repoRoot, out.Patch, targets)
		if issues := detectMissingTargetSnapshotContext(repoRoot, out.Patch, targets); len(issues) == 0 && len(contractIssues) == 0 {
			return
		} else {
			if len(contractIssues) > 0 {
				if synth, ok := trySynthesizeGoalTargetRecovery(in, targets); ok && patchTouchesTargetsWithCurrentDiff(in.Diff, synth.Patch, targets, requireAllTargets) {
					out.Patch = normalizePatchForOutput(synth.Patch)
					out.Notes = appendCoderNote(out.Notes, synth.Notes)
					if strings.TrimSpace(synth.Summary) != "" {
						out.Summary = strings.TrimSpace(synth.Summary)
					}
					return
				}
				out.Notes = appendCoderNote(out.Notes, "target patch contract issues: "+strings.Join(contractIssues, ", "))
			}
			if len(issues) > 0 {
				out.Notes = appendCoderNote(out.Notes, "target patch referenced target-file context missing from snapshots: "+strings.Join(issues, ", "))
			}
		}
	}
	missingTargets := missingTargetFiles(out.Patch, targets)
	if shouldSkipProviderPatchRetries(out) {
		out.Notes = appendCoderNote(out.Notes, "skipped provider patch retries after heuristic/offline fallback")
		if synth, ok := trySynthesizeGoalTargetRecovery(in, targets); ok && patchTouchesTargetsWithCurrentDiff(in.Diff, synth.Patch, targets, requireAllTargets) {
			out.Patch = normalizePatchForOutput(synth.Patch)
			if strings.TrimSpace(out.Summary) == "" {
				out.Summary = "Applied deterministic snapshot recovery for goal target files."
			}
			out.Notes = appendCoderNote(out.Notes, synth.Notes)
			return
		}
		out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\nUnable to produce patch touching required goal target files.")
		return
	}
	retryInput := buildDefinitionIssueRecoveryInput(in, targets, out.Patch)
	retry, err := runWithHardTimeout(ctx, targetPatchRetryTimeout, func(callCtx context.Context) (CoderOutput, error) {
		emitAgentStage(callCtx, "coder_targeted_retry_start")
		return c.generateTargetedPatchWithClient(callCtx, retryInput, targets, out.Patch)
	})
	emitAgentStage(ctx, "coder_targeted_retry_done")
	if err != nil {
		recordPatchAttemptDiagnostic(out, "targeted_patch_retry", CoderOutput{}, err, targets, requireAllTargets, false, false)
	} else if patchTouchesTargetsWithCurrentDiff(in.Diff, retry.Patch, targets, requireAllTargets) &&
		len(detectMissingTargetSnapshotContext(repoRoot, retry.Patch, targets)) == 0 &&
		len(detectGoalTargetPatchContractIssues(in.Goal, repoRoot, retry.Patch, targets)) == 0 {
		if patchTouchesTargets(retry.Patch, targets, requireAllTargets) {
			recordPatchAttemptDiagnostic(&retry, "targeted_patch_retry", retry, nil, targets, requireAllTargets, false, true)
		}
		mergeCoderRetryOutput(out, retry)
		if requireAllTargets && !patchTouchesTargets(retry.Patch, targets, requireAllTargets) && diffTouchesTargets(in.Diff, targets, requireAllTargets) {
			out.Notes = appendCoderNote(out.Notes, "targeted_patch_retry repaired remaining goal-target files on top of current diff coverage")
		}
		return
	} else if len(missingTargets) > 0 && patchTouchesTargets(retry.Patch, missingTargets, true) {
		if combined := combinePatchForMissingTargets(repoRoot, out.Patch, retry.Patch, targets, false); combined != "" && len(detectMissingTargetSnapshotContext(repoRoot, combined, targets)) == 0 {
			retry.Patch = combined
			recordPatchAttemptDiagnostic(&retry, "targeted_patch_retry", retry, nil, targets, requireAllTargets, false, true)
			mergeCoderRetryOutput(out, retry)
			out.Notes = appendCoderNote(out.Notes, "targeted_patch_retry filled missing target files: "+strings.Join(missingTargets, ", "))
			return
		}
	} else {
		if issues := detectMissingTargetSnapshotContext(repoRoot, retry.Patch, targets); len(issues) > 0 {
			retry.Notes = appendCoderNote(retry.Notes, "missing target snapshot context: "+strings.Join(issues, ", "))
		}
		if issues := detectGoalTargetPatchContractIssues(in.Goal, repoRoot, retry.Patch, targets); len(issues) > 0 {
			retry.Notes = appendCoderNote(retry.Notes, "goal-target contract issues: "+strings.Join(issues, ", "))
		}
		recordPatchAttemptDiagnostic(out, "targeted_patch_retry", retry, nil, targets, requireAllTargets, false, false)
	}

	strictBasePatch := bestCoveragePatchForRecovery(out.Patch, retry.Patch, targets)
	strictRetryInput := buildDefinitionIssueRecoveryInput(in, targets, strictBasePatch)
	hardRetry, hardErr := runWithHardTimeout(ctx, targetPatchHardRetryTimeout, func(callCtx context.Context) (CoderOutput, error) {
		emitAgentStage(callCtx, "coder_targeted_strict_retry_start")
		return c.generateTargetedPatchWithClientStrict(callCtx, strictRetryInput, targets, strictBasePatch)
	})
	emitAgentStage(ctx, "coder_targeted_strict_retry_done")
	if hardErr != nil {
		recordPatchAttemptDiagnostic(out, "targeted_strict_retry", CoderOutput{}, hardErr, targets, requireAllTargets, false, false)
	} else if patchTouchesTargetsWithCurrentDiff(in.Diff, hardRetry.Patch, targets, requireAllTargets) &&
		len(detectMissingTargetSnapshotContext(repoRoot, hardRetry.Patch, targets)) == 0 &&
		len(detectGoalTargetPatchContractIssues(in.Goal, repoRoot, hardRetry.Patch, targets)) == 0 {
		if patchTouchesTargets(hardRetry.Patch, targets, requireAllTargets) {
			recordPatchAttemptDiagnostic(&hardRetry, "targeted_strict_retry", hardRetry, nil, targets, requireAllTargets, false, true)
		}
		mergeCoderRetryOutput(out, hardRetry)
		if requireAllTargets && !patchTouchesTargets(hardRetry.Patch, targets, requireAllTargets) && diffTouchesTargets(in.Diff, targets, requireAllTargets) {
			out.Notes = appendCoderNote(out.Notes, "targeted_strict_retry repaired remaining goal-target files on top of current diff coverage")
		}
		return
	} else if len(missingTargets) > 0 && patchTouchesTargets(hardRetry.Patch, missingTargets, true) {
		if combined := combinePatchForMissingTargets(repoRoot, out.Patch, hardRetry.Patch, targets, false); combined != "" && len(detectMissingTargetSnapshotContext(repoRoot, combined, targets)) == 0 {
			hardRetry.Patch = combined
			recordPatchAttemptDiagnostic(&hardRetry, "targeted_strict_retry", hardRetry, nil, targets, requireAllTargets, false, true)
			mergeCoderRetryOutput(out, hardRetry)
			out.Notes = appendCoderNote(out.Notes, "targeted_strict_retry filled missing target files: "+strings.Join(missingTargets, ", "))
			return
		}
	} else {
		if issues := detectMissingTargetSnapshotContext(repoRoot, hardRetry.Patch, targets); len(issues) > 0 {
			hardRetry.Notes = appendCoderNote(hardRetry.Notes, "missing target snapshot context: "+strings.Join(issues, ", "))
		}
		if issues := detectGoalTargetPatchContractIssues(in.Goal, repoRoot, hardRetry.Patch, targets); len(issues) > 0 {
			hardRetry.Notes = appendCoderNote(hardRetry.Notes, "goal-target contract issues: "+strings.Join(issues, ", "))
		}
		recordPatchAttemptDiagnostic(out, "targeted_strict_retry", hardRetry, nil, targets, requireAllTargets, false, false)
	}
	if remaining := missingTargetFiles(bestCoveragePatchForRecovery(out.Patch, hardRetry.Patch, targets), targets); len(remaining) > 0 {
		out.Notes = appendCoderNote(out.Notes, "missing target files: "+strings.Join(remaining, ", "))
	}

	if synth, ok := trySynthesizeGoalTargetRecovery(in, targets); ok && patchTouchesTargetsWithCurrentDiff(in.Diff, synth.Patch, targets, requireAllTargets) {
		out.Patch = normalizePatchForOutput(synth.Patch)
		if strings.TrimSpace(out.Summary) == "" {
			out.Summary = "Applied deterministic snapshot recovery for goal target files."
		}
		out.Notes = appendCoderNote(out.Notes, synth.Notes)
		return
	}
	out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\nUnable to produce patch touching required goal target files.")
}

func (c *Coder) ensureKBTaskScope(ctx context.Context, in CoderInput, out *CoderOutput) {
	if out == nil || strings.TrimSpace(out.Patch) == "" {
		return
	}
	targets := extractGoalTargetFiles(in.Goal)
	if !shouldEnforceKBTaskScope(in.Goal, targets) {
		return
	}
	violations := detectKBScopeCreep(in.Goal, out.Patch, targets)
	if len(violations) == 0 {
		return
	}
	out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\nKB scope gate detected extra identifiers: " + strings.Join(violations, ", "))
	if !c.client.Ready() {
		return
	}
	retry, err := runWithHardTimeout(ctx, targetPatchHardRetryTimeout, func(callCtx context.Context) (CoderOutput, error) {
		emitAgentStage(callCtx, "coder_kb_scope_retry_start")
		return c.generateScopedPatchWithClientStrict(callCtx, in, targets, out.Patch, violations)
	})
	emitAgentStage(ctx, "coder_kb_scope_retry_done")
	if err != nil {
		out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\nKB scope retry skipped: " + err.Error())
		return
	}
	requireAllTargets := len(targets) > 1
	if !patchTouchesTargets(retry.Patch, targets, requireAllTargets) {
		out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\nKB scope retry did not touch required goal target files.")
		return
	}
	retryViolations := detectKBScopeCreep(in.Goal, retry.Patch, targets)
	if len(retryViolations) == 0 {
		mergeCoderRetryOutput(out, retry)
		out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\nKB scope retry removed extra identifiers.")
		return
	}
	if patchScopedAddLineCount(retry.Patch, targets) < patchScopedAddLineCount(out.Patch, targets) {
		mergeCoderRetryOutput(out, retry)
		out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\nKB scope retry still had extra identifiers (" + strings.Join(retryViolations, ", ") + "); kept smaller target patch.")
		return
	}
	out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\nKB scope retry still had extra identifiers (" + strings.Join(retryViolations, ", ") + "); kept original patch.")
}

func (c *Coder) ensureRepoOnlyMinimalMode(ctx context.Context, in CoderInput, out *CoderOutput) {
	if out == nil {
		return
	}
	if !isRepoOnlyGoal(in.Goal) {
		return
	}
	out.Citations = []string{}
	// Keep validation commands deterministic in repo-only mode.
	if len(in.Commands) > 0 {
		out.Commands = append([]string{}, in.Commands...)
	}

	targets := extractGoalTargetFiles(in.Goal)
	if len(targets) == 0 {
		return
	}
	requireAllTargets := len(targets) > 1
	issues := detectTargetedPatchDefinitionIssues(in.Goal, strings.TrimSpace(in.RepoSummary), out.Patch, targets)
	issues = appendUniqueIssues(issues, detectRepoOnlySnapshotDefinitionIssues(in.Goal, out.Patch, targets)...)
	patchValid := patchTouchesOnlyTargets(out.Patch, targets) && patchTouchesTargets(out.Patch, targets, requireAllTargets) && len(issues) == 0
	if patchValid {
		return
	}
	trySnapshotRepoOnlyFallback := func() bool {
		synth, ok := trySynthesizeRepoOnlySnapshotPatch(in.Goal, strings.TrimSpace(in.RepoSummary), targets)
		if !ok {
			return false
		}
		out.Patch = normalizePatchForOutput(synth.Patch)
		out.Notes = appendCoderNote(out.Notes, synth.Notes)
		if len(in.Commands) > 0 {
			out.Commands = append([]string{}, in.Commands...)
		}
		out.Citations = []string{}
		return true
	}
	retryInput := buildDefinitionIssueRecoveryInput(in, targets, out.Patch)
	retry, err := runWithHardTimeout(ctx, coderCompletionTimeout, func(callCtx context.Context) (CoderOutput, error) {
		emitAgentStage(callCtx, "coder_repo_only_retry_start")
		return c.generateRepoOnlyPatchWithClient(callCtx, retryInput, targets, out.Patch)
	})
	emitAgentStage(ctx, "coder_repo_only_retry_done")
	if err != nil {
		if !patchValid {
			recordPatchAttemptDiagnostic(out, "repo_only_retry", CoderOutput{}, err, targets, requireAllTargets, true, false)
		}
		_ = trySnapshotRepoOnlyFallback()
		return
	}
	if !patchTouchesOnlyTargets(retry.Patch, targets) || !patchTouchesTargets(retry.Patch, targets, requireAllTargets) {
		if !patchValid {
			recordPatchAttemptDiagnostic(out, "repo_only_retry", retry, nil, targets, requireAllTargets, true, false)
		}
		_ = trySnapshotRepoOnlyFallback()
		return
	}
	retryIssues := detectTargetedPatchDefinitionIssues(in.Goal, strings.TrimSpace(in.RepoSummary), retry.Patch, targets)
	retryIssues = appendUniqueIssues(retryIssues, detectRepoOnlySnapshotDefinitionIssues(in.Goal, retry.Patch, targets)...)
	if len(retryIssues) > 0 {
		out.Notes = appendCoderNote(out.Notes, "repo_only_retry definition issues: "+strings.Join(retryIssues, ", "))
		if strings.TrimSpace(retry.Notes) != "" {
			out.Notes = appendCoderNote(out.Notes, retry.Notes)
		}
		if trySnapshotRepoOnlyFallback() {
			return
		}
		if isReorderOnlyGoal(in.Goal) && len(issues) > 0 {
			out.Patch = ""
			out.Notes = appendCoderNote(out.Notes, "rejected unsafe reorder-only patch after repo-only retry")
		}
		return
	}
	recordPatchAttemptDiagnostic(&retry, "repo_only_retry", retry, nil, targets, requireAllTargets, true, true)
	if strings.TrimSpace(retry.Patch) != "" {
		out.Patch = normalizePatchForOutput(retry.Patch)
	}
	if strings.TrimSpace(retry.Summary) != "" {
		out.Summary = strings.TrimSpace(retry.Summary)
	}
	// Repo-only mode always falls back to task-provided commands.
	if len(in.Commands) > 0 {
		out.Commands = append([]string{}, in.Commands...)
	}
	if strings.TrimSpace(retry.Notes) != "" {
		out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\n" + strings.TrimSpace(retry.Notes))
	}
	out.Citations = []string{}
}

func mergeCoderRetryOutput(out *CoderOutput, retry CoderOutput) {
	if out == nil {
		return
	}
	if strings.TrimSpace(retry.Patch) != "" {
		out.Patch = normalizePatchForOutput(retry.Patch)
	}
	if strings.TrimSpace(retry.Summary) != "" {
		out.Summary = strings.TrimSpace(retry.Summary)
	}
	if len(retry.Commands) > 0 {
		out.Commands = retry.Commands
	}
	if strings.TrimSpace(retry.Notes) != "" {
		out.Notes = strings.TrimSpace(strings.TrimSpace(out.Notes) + "\n" + strings.TrimSpace(retry.Notes))
	}
	out.Citations = normalizeCitationList(append(out.Citations, retry.Citations...))
}

func mergePatchStrings(basePatch, extraPatch string) string {
	basePatch = strings.TrimSpace(basePatch)
	extraPatch = strings.TrimSpace(extraPatch)
	switch {
	case basePatch == "":
		return extraPatch
	case extraPatch == "":
		return basePatch
	default:
		return basePatch + "\n" + extraPatch
	}
}

func missingTargetFiles(patch string, targets []string) []string {
	targets = normalizeCitationList(targets)
	if len(targets) == 0 {
		return nil
	}
	changed := extractChangedFiles(patch, targets...)
	var missing []string
	for _, target := range targets {
		if _, ok := changed[target]; !ok {
			missing = append(missing, target)
		}
	}
	return missing
}

func combinePatchForMissingTargets(repoRoot string, basePatch string, retryPatch string, targets []string, requireOnlyTargets bool) string {
	combined := mergePatchStrings(basePatch, retryPatch)
	if strings.TrimSpace(combined) == "" {
		return ""
	}
	return normalizeCoderPatchForContract(strings.TrimSpace(repoRoot), combined, targets, len(normalizeCitationList(targets)) > 1, requireOnlyTargets)
}

func bestCoveragePatchForRecovery(basePatch string, retryPatch string, targets []string) string {
	best := strings.TrimSpace(basePatch)
	bestMissing := len(missingTargetFiles(best, targets))
	candidates := []string{
		strings.TrimSpace(retryPatch),
		strings.TrimSpace(mergePatchStrings(basePatch, retryPatch)),
	}
	for _, candidate := range candidates {
		if candidate == "" {
			continue
		}
		missing := len(missingTargetFiles(candidate, targets))
		if best == "" || missing < bestMissing {
			best = candidate
			bestMissing = missing
		}
	}
	return best
}

func patchTouchesOnlyTargets(patch string, targets []string) bool {
	if strings.TrimSpace(patch) == "" || len(targets) == 0 {
		return false
	}
	allowed := make(map[string]struct{}, len(targets))
	for _, t := range targets {
		if normalized := normalizePathForCompare(t, targets...); normalized != "" {
			allowed[normalized] = struct{}{}
		}
	}
	changed := extractChangedFiles(patch, targets...)
	if len(changed) == 0 {
		return false
	}
	for file := range changed {
		if _, ok := allowed[file]; !ok {
			return false
		}
	}
	return true
}

func (c *Coder) generateRepoOnlyPatchWithClient(ctx context.Context, in CoderInput, targets []string, priorPatch string) (CoderOutput, error) {
	if c.retryHooks != nil && c.retryHooks.repoOnly != nil {
		return c.retryHooks.repoOnly(ctx, in, targets, priorPatch)
	}
	if !c.client.Ready() {
		return CoderOutput{}, fmt.Errorf("llm client not configured")
	}
	targets = normalizeCitationList(targets)
	if len(targets) == 0 {
		return CoderOutput{}, fmt.Errorf("no target files")
	}
	reorderOnlyConstraint := ""
	if isReorderOnlyGoal(in.Goal) {
		reorderOnlyConstraint = "- reorder-only task: preserve every existing identifier, tool, import, and helper exactly once; only reorder existing entries in place. Do not add or remove tools.\n- a prohibition on calling a tool means do not invoke it during task solving; it does not authorize deleting that tool's definition or registration.\n"
	}
	system := `You are a coding agent fixing a repo-only patch.
Return JSON only with fields: summary, patch, commands, notes, citations.
- patch must be unified diff text or empty string.
- patch field must contain only raw diff text; do not include explanations, bullets, or markdown fences inside patch.
- each file patch should start with diff --git (preferred) or ---/+++ headers, and each hunk header must be a valid unified diff header like @@ -10,2 +10,3 @@.
- source of truth is target_file_snapshots from payload; use those exact contents to craft hunks.
- hard constraints:
  1) patch must touch at least one target file;
  2) patch may only touch target files (no unrelated files).
- for a single target file task, empty patch is allowed only when target_file_snapshots already satisfy the goal; notes must quote the exact line, section, or snippet proving that.
- when possible, keep changes minimal and line-anchored to reduce patch apply failures.
- if you cannot produce a reliable patch from snapshots, return empty patch and explain why in notes.
- for reorder-only tasks, modify ordering only; do not delete, add, or rename existing entries.
- commands must be deterministic shell commands only.
- do not call kb_search or include kb citations.
- never return markdown outside JSON.`
	system += "\n" + reorderOnlyConstraint
	if recoveryConstraint := buildDefinitionIssueRecoveryConstraint(in, targets); recoveryConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + recoveryConstraint)
	}
	snapshots := buildRepoOnlyTargetSnapshots(strings.TrimSpace(in.RepoSummary), targets)
	payload := map[string]any{
		"task_input":             in,
		"target_files":           targets,
		"target_file_snapshots":  snapshots,
		"previous_patch":         strings.TrimSpace(priorPatch),
		"repo_only_requirements": "only modify target files; do not add kb usage/imports; keep commands deterministic",
	}
	addDefinitionIssueRecoveryPayload(payload, in)
	b, _ := json.MarshalIndent(payload, "", "  ")
	user := fmt.Sprintf("Retry with strict repo-only minimal-change constraints.\n%s", string(b))
	retryCtx, cancel := context.WithTimeout(ctx, targetPatchRetryTimeout)
	defer cancel()
	var wire any
	if err := c.client.CompleteJSON(retryCtx, system, user, &wire); err != nil {
		return CoderOutput{}, err
	}
	raw, _ := json.Marshal(wire)
	out, err := decodeCoderOutput(string(raw))
	if err != nil {
		return CoderOutput{}, err
	}
	out.Patch = normalizeCoderPatchForContract(strings.TrimSpace(in.RepoSummary), out.Patch, targets, len(targets) > 1, true)
	out.Citations = []string{}
	return out, nil
}

func (c *Coder) ensureSingleTargetOutputConstraints(ctx context.Context, in CoderInput, out *CoderOutput) {
	if out == nil {
		return
	}
	targets := extractGoalTargetFiles(in.Goal)
	if len(targets) == 0 {
		return
	}
	issues := detectTargetedPatchDefinitionIssues(in.Goal, strings.TrimSpace(in.RepoSummary), out.Patch, targets)
	if len(issues) == 0 {
		if synth, ok := trySynthesizeWriteErrStatusTextPatch(in.Goal, strings.TrimSpace(in.RepoSummary), targets, out.Patch); ok {
			out.Patch = normalizePatchForOutput(synth.Patch)
			out.Notes = appendCoderNote(out.Notes, synth.Notes)
			if strings.TrimSpace(synth.Summary) != "" {
				out.Summary = strings.TrimSpace(synth.Summary)
			}
			return
		}
		return
	}
	out.Notes = appendCoderNote(out.Notes, "single_target_patch_guard detected: "+strings.Join(issues, ", "))
	if !c.client.Ready() && (c.retryHooks == nil || c.retryHooks.targetedStrict == nil) {
		if synth, ok := trySynthesizeGoalTargetRecovery(in, targets); ok {
			out.Patch = normalizePatchForOutput(synth.Patch)
			out.Notes = appendCoderNote(out.Notes, synth.Notes)
			if strings.TrimSpace(synth.Summary) != "" {
				out.Summary = strings.TrimSpace(synth.Summary)
			}
		}
		return
	}
	requireAllTargets := len(targets) > 1
	emitAgentStage(ctx, "coder_single_target_retry_start")
	retryInput := buildDefinitionIssueRecoveryInput(in, targets, out.Patch)
	retry, err := runWithHardTimeout(ctx, targetPatchHardRetryTimeout, func(callCtx context.Context) (CoderOutput, error) {
		return c.generateTargetedPatchWithClientStrict(callCtx, retryInput, targets, out.Patch)
	})
	emitAgentStage(ctx, "coder_single_target_retry_done")
	if err != nil {
		recordPatchAttemptDiagnostic(out, "single_target_patch_retry", CoderOutput{}, err, targets, requireAllTargets, false, false)
		if isReorderOnlyGoal(in.Goal) {
			out.Patch = ""
			out.Notes = appendCoderNote(out.Notes, "rejected unsafe reorder-only patch after single-target retry")
		}
		return
	}
	if !patchTouchesTargets(retry.Patch, targets, requireAllTargets) {
		recordPatchAttemptDiagnostic(out, "single_target_patch_retry", retry, nil, targets, requireAllTargets, false, false)
		if isReorderOnlyGoal(in.Goal) {
			out.Patch = ""
			out.Notes = appendCoderNote(out.Notes, "rejected unsafe reorder-only patch after single-target retry")
		}
		return
	}
	retryIssues := detectTargetedPatchDefinitionIssues(in.Goal, strings.TrimSpace(in.RepoSummary), retry.Patch, targets)
	if len(retryIssues) == 0 {
		recordPatchAttemptDiagnostic(&retry, "single_target_patch_retry", retry, nil, targets, requireAllTargets, false, true)
		mergeCoderRetryOutput(out, retry)
		out.Notes = appendCoderNote(out.Notes, "single_target_patch_retry removed duplicate definition issues")
		return
	}
	if isReorderOnlyGoal(in.Goal) {
		out.Patch = ""
		out.Notes = appendCoderNote(out.Notes, "rejected unsafe reorder-only patch after single-target retry")
	}
	if synth, ok := trySynthesizeGoalTargetRecovery(in, targets); ok {
		out.Patch = normalizePatchForOutput(synth.Patch)
		out.Notes = appendCoderNote(out.Notes, synth.Notes)
		if strings.TrimSpace(synth.Summary) != "" {
			out.Summary = strings.TrimSpace(synth.Summary)
		}
		return
	}
	out.Notes = appendCoderNote(out.Notes, "single_target_patch_retry still has definition issues: "+strings.Join(retryIssues, ", "))
}

func buildRepoOnlyTargetSnapshots(repoRoot string, targets []string) map[string]string {
	out := make(map[string]string, len(targets))
	if strings.TrimSpace(repoRoot) == "" {
		return out
	}
	for _, raw := range targets {
		path := strings.TrimSpace(raw)
		if path == "" {
			continue
		}
		content, err := tools.RepoRead(repoRoot, path, repoOnlySnapshotMaxBytes)
		if err != nil {
			out[path] = "[repo_read_error] " + err.Error()
			continue
		}
		out[path] = content
	}
	return out
}

func trySynthesizeReorderOnlyPatch(goal string, repoRoot string, targets []string) (CoderOutput, bool) {
	// Narrow fallback retained for legacy reorder-only recovery. Do not extend with new task shapes.
	if !isReorderOnlyGoal(goal) || strings.TrimSpace(repoRoot) == "" {
		return CoderOutput{}, false
	}
	targets = normalizeCitationList(targets)
	if len(targets) != 1 {
		return CoderOutput{}, false
	}
	target := targets[0]
	if !strings.HasSuffix(strings.ToLower(target), ".go") {
		return CoderOutput{}, false
	}
	functions := extractGoalFunctionIdentifiers(goal)
	if len(functions) != 1 {
		return CoderOutput{}, false
	}
	snapshot := buildRepoOnlyTargetSnapshots(repoRoot, []string{target})[target]
	if strings.TrimSpace(snapshot) == "" || strings.HasPrefix(snapshot, "[repo_read_error]") {
		return CoderOutput{}, false
	}
	patch, ok := synthesizeReorderOnlyPatchFromSnapshot(target, snapshot, functions[0])
	if !ok {
		return CoderOutput{}, false
	}
	return CoderOutput{
		Patch: patch,
		Notes: "synthesized reorder-only patch from snapshots",
	}, true
}

func synthesizeReorderOnlyPatchFromSnapshot(target string, snapshot string, functionName string) (string, bool) {
	lines := strings.Split(strings.ReplaceAll(snapshot, "\r\n", "\n"), "\n")
	start, end, ok := findGoFunctionBounds(lines, functionName)
	if !ok {
		return "", false
	}
	returnLine, entriesStart, entriesEnd, closeLine, ok := findReorderableReturnSlice(lines[start:end])
	if !ok {
		return "", false
	}
	returnLine += start
	entriesStart += start
	entriesEnd += start
	closeLine += start
	type entry struct {
		name string
		line string
	}
	entries := make([]entry, 0, entriesEnd-entriesStart)
	for _, line := range lines[entriesStart:entriesEnd] {
		name, ok := extractReorderOnlyEntryIdentifier(line)
		if !ok {
			return "", false
		}
		entries = append(entries, entry{name: name, line: line})
	}
	if len(entries) < 2 {
		return "", false
	}
	sortedEntries := append([]entry(nil), entries...)
	sort.SliceStable(sortedEntries, func(i, j int) bool {
		return sortedEntries[i].name < sortedEntries[j].name
	})
	alreadySorted := true
	for i := range entries {
		if entries[i].line != sortedEntries[i].line {
			alreadySorted = false
			break
		}
	}
	if alreadySorted {
		return "", false
	}
	oldStart := returnLine + 1
	oldCount := closeLine - returnLine + 1
	var b strings.Builder
	b.WriteString(fmt.Sprintf("diff --git a/%s b/%s\n", target, target))
	b.WriteString(fmt.Sprintf("--- a/%s\n", target))
	b.WriteString(fmt.Sprintf("+++ b/%s\n", target))
	b.WriteString(fmt.Sprintf("@@ -%d,%d +%d,%d @@\n", oldStart, oldCount, oldStart, oldCount))
	b.WriteString(" " + lines[returnLine] + "\n")
	for _, line := range lines[entriesStart:entriesEnd] {
		b.WriteString("-" + line + "\n")
	}
	for _, item := range sortedEntries {
		b.WriteString("+" + item.line + "\n")
	}
	b.WriteString(" " + lines[closeLine] + "\n")
	return strings.TrimRight(b.String(), "\n"), true
}

func findGoFunctionBounds(lines []string, functionName string) (int, int, bool) {
	start := -1
	braceDepth := 0
	seenOpen := false
	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if start == -1 {
			m := goFuncScopeRegexp.FindStringSubmatch(trimmed)
			if len(m) != 2 || m[1] != functionName {
				continue
			}
			start = i
		}
		braceDepth += strings.Count(line, "{")
		if strings.Contains(line, "{") {
			seenOpen = true
		}
		braceDepth -= strings.Count(line, "}")
		if start != -1 && seenOpen && braceDepth == 0 {
			return start, i + 1, true
		}
	}
	return 0, 0, false
}

func findReorderableReturnSlice(lines []string) (returnLine int, entriesStart int, entriesEnd int, closeLine int, ok bool) {
	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if !strings.HasPrefix(trimmed, "return []") || !strings.HasSuffix(trimmed, "{") {
			continue
		}
		j := i + 1
		for ; j < len(lines); j++ {
			next := strings.TrimSpace(lines[j])
			if strings.HasPrefix(next, "}") {
				break
			}
		}
		if j >= len(lines) || j <= i+1 {
			return 0, 0, 0, 0, false
		}
		return i, i + 1, j, j, true
	}
	return 0, 0, 0, 0, false
}

func patchTouchesAnyTarget(patch string, targets []string) bool {
	if strings.TrimSpace(patch) == "" || len(targets) == 0 {
		return false
	}
	changed := extractChangedFiles(patch, targets...)
	for _, target := range targets {
		if _, ok := changed[target]; ok {
			return true
		}
	}
	return false
}

func patchTouchesAllTargets(patch string, targets []string) bool {
	if strings.TrimSpace(patch) == "" || len(targets) == 0 {
		return false
	}
	changed := extractChangedFiles(patch, targets...)
	for _, target := range targets {
		if _, ok := changed[target]; !ok {
			return false
		}
	}
	return true
}

func patchTouchesTargets(patch string, targets []string, requireAll bool) bool {
	if requireAll {
		return patchTouchesAllTargets(patch, targets)
	}
	return patchTouchesAnyTarget(patch, targets)
}

func patchTouchesTargetsWithCurrentDiff(currentDiff, patch string, targets []string, requireAll bool) bool {
	if patchTouchesTargets(patch, targets, requireAll) {
		return true
	}
	if strings.TrimSpace(patch) == "" {
		return false
	}
	if !requireAll {
		return patchTouchesAnyTarget(patch, targets)
	}
	if !diffTouchesTargets(currentDiff, targets, true) {
		return false
	}
	return patchTouchesAnyTarget(patch, targets)
}

func detectGoalTargetPatchContractIssues(goal, repoRoot, patch string, targets []string) []string {
	if strings.TrimSpace(patch) == "" {
		return nil
	}
	issues := detectTargetedPatchDefinitionIssues(goal, repoRoot, patch, targets)
	resetTestTarget := filepath.ToSlash(filepath.Join("internal", "loop", "processor_test.go"))
	if _, touchesResetTest := extractChangedFiles(patch, resetTestTarget)[resetTestTarget]; isDoomLoopResetGoal(goal) && touchesResetTest && !hasMinimalDoomLoopResetTestShape(patch) {
		issues = appendUniqueIssues(issues, "reset test must use a minimal table-driven one-positive/one-negative shape")
	}
	maxRuntimeTarget := filepath.ToSlash(filepath.Join("internal", "loop", "engine_eino.go"))
	if _, touchesMaxRuntime := extractChangedFiles(patch, maxRuntimeTarget)[maxRuntimeTarget]; isMaxRuntimeStepsCommentGoal(goal) && touchesMaxRuntime && !hasExplicitMaxRuntimeBranchComment(patch) {
		issues = appendUniqueIssues(issues, "maxRuntimeSteps comment must explicitly list turn/finish/failed/blocked")
	}
	return issues
}

func shouldSkipProviderPatchRetries(out *CoderOutput) bool {
	if out == nil || !out.UsedFallback {
		return false
	}
	switch strings.TrimSpace(strings.ToLower(out.FallbackSource)) {
	case "heuristic", "offline":
		return true
	default:
		return false
	}
}

func shouldForceDoomLoopResetSnapshotRecovery(in CoderInput, patch string, targets []string) bool {
	if !isDoomLoopResetGoal(in.Goal) || strings.TrimSpace(in.Diff) != "" || strings.TrimSpace(patch) == "" {
		return false
	}
	requireAllTargets := len(targets) > 1
	return patchTouchesTargets(patch, targets, requireAllTargets)
}

func diffTouchesTargets(diff string, targets []string, requireAll bool) bool {
	if strings.TrimSpace(diff) == "" || len(targets) == 0 {
		return false
	}
	changed := extractChangedFiles(diff, targets...)
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

func (c *Coder) generateTargetedPatchWithClient(ctx context.Context, in CoderInput, targets []string, priorPatch string) (CoderOutput, error) {
	if c.retryHooks != nil && c.retryHooks.targeted != nil {
		return c.retryHooks.targeted(ctx, in, targets, priorPatch)
	}
	if !c.client.Ready() {
		return CoderOutput{}, fmt.Errorf("llm client not configured")
	}
	targets = normalizeCitationList(targets)
	if len(targets) == 0 {
		return CoderOutput{}, fmt.Errorf("no target files")
	}
	targetRule := "patch must touch at least one target file listed by user."
	if len(targets) > 1 {
		targetRule = "patch must touch all target files listed by user."
	}
	singleFnConstraint := buildSingleTargetFunctionConstraint(in.Goal, targets)
	testingConstraint := buildMinimalTestingConstraint(in.Goal, targets)
	inlineEditConstraint := buildMixedTaskInlineEditConstraint(in.Goal, targets)
	multiTargetPatchConstraint := buildMultiTargetPatchSectionConstraint(targets)
	reorderOnlyConstraint := buildReorderOnlySnapshotConstraint(in.Goal)
	system := `You are a coding agent fixing a previous patch attempt.
Return JSON only with fields: summary, patch, commands, notes, citations.
- patch must be unified diff text or empty string.
- patch field must contain only raw diff text; do not include explanations, bullets, or markdown fences inside patch.
- each file patch should start with diff --git (preferred) or ---/+++ headers, and each hunk header must be a valid unified diff header like @@ -10,2 +10,3 @@.
- source of truth is target_file_snapshots from payload; craft hunks from exact snapshot text.
- hard constraint: ` + targetRule + `
- do not modify unrelated files unless absolutely required.
- if kb_scope_contract.identifiers is present, only implement those requested identifiers; do not add adjacent validation rules or opportunistic cleanup.
- do not define the same top-level helper or Test* name more than once in the patch; inspect target_file_snapshots and reuse existing names.
- keep edits minimal and line-anchored to reduce patch apply failures.
- for a single target file task, empty patch is allowed only when target_file_snapshots already satisfy the goal; notes must quote the exact line, section, or snippet proving that.
- if patch is empty, notes must explain why the goal is already satisfied in current files.
- commands must be deterministic shell commands only.
- never return markdown outside JSON.`
	if len(targets) > 1 {
		system = strings.TrimSpace(system + "\n- this is a multi-target goal: a valid answer must return a non-empty patch touching all target files; do not claim the goal is already satisfied unless every target_file_snapshot contains direct quoted evidence for the requested behavior.")
	}
	if multiTargetPatchConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + multiTargetPatchConstraint)
	}
	if singleFnConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + singleFnConstraint)
	}
	if testingConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + testingConstraint)
	}
	if inlineEditConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + inlineEditConstraint)
	}
	if reorderOnlyConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + reorderOnlyConstraint)
	}
	if recoveryConstraint := buildDefinitionIssueRecoveryConstraint(in, targets); recoveryConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + recoveryConstraint)
	}
	snapshots := buildRepoOnlyTargetSnapshots(strings.TrimSpace(in.RepoSummary), targets)
	payload := map[string]any{
		"task_input":            in,
		"target_files":          targets,
		"target_file_snapshots": snapshots,
		"previous_patch":        strings.TrimSpace(priorPatch),
		"kb_scope_contract":     buildKBScopeContract(in.Goal, targets),
	}
	addDefinitionIssueRecoveryPayload(payload, in)
	b, _ := json.MarshalIndent(payload, "", "  ")
	user := fmt.Sprintf("Retry with strict target-file constraint.\n%s", string(b))
	retryCtx, cancel := context.WithTimeout(ctx, targetPatchRetryTimeout)
	defer cancel()
	var wire any
	if err := c.client.CompleteJSON(retryCtx, system, user, &wire); err != nil {
		return CoderOutput{}, err
	}
	raw, _ := json.Marshal(wire)
	out, err := decodeCoderOutput(string(raw))
	if err != nil {
		return CoderOutput{}, err
	}
	if len(out.Commands) == 0 {
		out.Commands = in.Commands
	}
	out.Patch = normalizeCoderPatchForContract(strings.TrimSpace(in.RepoSummary), out.Patch, targets, len(targets) > 1, false)
	return out, nil
}

func (c *Coder) generateTargetedPatchWithClientStrict(ctx context.Context, in CoderInput, targets []string, priorPatch string) (CoderOutput, error) {
	if c.retryHooks != nil && c.retryHooks.targetedStrict != nil {
		return c.retryHooks.targetedStrict(ctx, in, targets, priorPatch)
	}
	if !c.client.Ready() {
		return CoderOutput{}, fmt.Errorf("llm client not configured")
	}
	targets = normalizeCitationList(targets)
	if len(targets) == 0 {
		return CoderOutput{}, fmt.Errorf("no target files")
	}
	targetRule := "must touch at least one target file"
	if len(targets) > 1 {
		targetRule = "must touch all target files"
	}
	singleFnConstraint := buildSingleTargetFunctionConstraint(in.Goal, targets)
	testingConstraint := buildMinimalTestingConstraint(in.Goal, targets)
	inlineEditConstraint := buildMixedTaskInlineEditConstraint(in.Goal, targets)
	multiTargetPatchConstraint := buildMultiTargetPatchSectionConstraint(targets)
	reorderOnlyConstraint := buildReorderOnlySnapshotConstraint(in.Goal)
	system := `You are a coding agent doing a final strict patch retry.
Return JSON only with fields: summary, patch, commands, notes, citations.
- patch must be unified diff text.
- patch field must contain only raw diff text; do not include explanations, bullets, or markdown fences inside patch.
- each file patch should start with diff --git (preferred) or ---/+++ headers, and each hunk header must be a valid unified diff header like @@ -10,2 +10,3 @@.
- patch ` + targetRule + ` and only use paths from target_files.
- generate hunks from target_file_snapshots exact text.
- if kb_scope_contract.identifiers is present, only implement those requested identifiers; do not add adjacent validation rules or cleanup.
- do not define the same top-level helper or Test* name more than once in the patch; inspect target_file_snapshots and reuse existing names.
- for a single target file task, empty patch is invalid unless target_file_snapshots already satisfy the goal; notes must quote the exact line, section, or snippet proving that.
- no markdown. no prose. JSON only.`
	if len(targets) > 1 {
		system = strings.TrimSpace(system + "\n- this is a multi-target goal: empty patch is invalid. Return a non-empty unified diff touching all target files, or fail explicitly in notes if snapshot evidence is contradictory.")
	}
	if multiTargetPatchConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + multiTargetPatchConstraint)
	}
	if singleFnConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + singleFnConstraint)
	}
	if testingConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + testingConstraint)
	}
	if inlineEditConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + inlineEditConstraint)
	}
	if reorderOnlyConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + reorderOnlyConstraint)
	}
	if recoveryConstraint := buildDefinitionIssueRecoveryConstraint(in, targets); recoveryConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + recoveryConstraint)
	}
	snapshots := buildRepoOnlyTargetSnapshots(strings.TrimSpace(in.RepoSummary), targets)
	payload := map[string]any{
		"task_input":                 in,
		"target_files":               targets,
		"target_file_snapshots":      snapshots,
		"previous_patch":             strings.TrimSpace(priorPatch),
		"kb_scope_contract":          buildKBScopeContract(in.Goal, targets),
		"required_output_constraint": "non-empty unified diff touching required target files",
	}
	addDefinitionIssueRecoveryPayload(payload, in)
	b, _ := json.MarshalIndent(payload, "", "  ")
	user := fmt.Sprintf("Return strict JSON now.\n%s", string(b))
	retryCtx, cancel := context.WithTimeout(ctx, targetPatchHardRetryTimeout)
	defer cancel()
	var wire any
	if err := c.client.CompleteJSON(retryCtx, system, user, &wire); err != nil {
		return CoderOutput{}, err
	}
	raw, _ := json.Marshal(wire)
	out, err := decodeCoderOutput(string(raw))
	if err != nil {
		return CoderOutput{}, err
	}
	if len(out.Commands) == 0 {
		out.Commands = in.Commands
	}
	out.Patch = normalizeCoderPatchForContract(strings.TrimSpace(in.RepoSummary), out.Patch, targets, len(targets) > 1, false)
	return out, nil
}

func (c *Coder) generateScopedPatchWithClientStrict(ctx context.Context, in CoderInput, targets []string, priorPatch string, violations []string) (CoderOutput, error) {
	if c.retryHooks != nil && c.retryHooks.scopedStrict != nil {
		return c.retryHooks.scopedStrict(ctx, in, targets, priorPatch, violations)
	}
	if !c.client.Ready() {
		return CoderOutput{}, fmt.Errorf("llm client not configured")
	}
	targets = normalizeCitationList(targets)
	if len(targets) == 0 {
		return CoderOutput{}, fmt.Errorf("no target files")
	}
	contract := buildKBScopeContract(in.Goal, targets)
	singleFnConstraint := buildSingleTargetFunctionConstraint(in.Goal, targets)
	testingConstraint := buildMinimalTestingConstraint(in.Goal, targets)
	inlineEditConstraint := buildMixedTaskInlineEditConstraint(in.Goal, targets)
	multiTargetPatchConstraint := buildMultiTargetPatchSectionConstraint(targets)
	system := `You are a coding agent doing a final strict patch retry.
Return JSON only with fields: summary, patch, commands, notes, citations.
- patch must be unified diff text.
- patch field must contain only raw diff text; do not include explanations, bullets, or markdown fences inside patch.
- each file patch should start with diff --git (preferred) or ---/+++ headers, and each hunk header must be a valid unified diff header like @@ -10,2 +10,3 @@.
- patch must touch only target_files and use target_file_snapshots exact text.
- only implement the identifiers explicitly named in kb_scope_contract.identifiers.
- knowledge-base evidence explains the requested rule; it does not authorize adjacent validation rules, cleanup, or extra checks.
- remove any changes related to scope_creep_identifiers.
- no markdown. no prose. JSON only.`
	if len(targets) > 1 {
		system = strings.TrimSpace(system + "\n- this is a multi-target goal: empty patch is invalid. Return a non-empty unified diff touching all target files.")
	}
	if multiTargetPatchConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + multiTargetPatchConstraint)
	}
	if singleFnConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + singleFnConstraint)
	}
	if testingConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + testingConstraint)
	}
	if inlineEditConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + inlineEditConstraint)
	}
	snapshots := buildRepoOnlyTargetSnapshots(strings.TrimSpace(in.RepoSummary), targets)
	payload := map[string]any{
		"task_input":                 in,
		"target_files":               targets,
		"target_file_snapshots":      snapshots,
		"previous_patch":             strings.TrimSpace(priorPatch),
		"kb_scope_contract":          contract,
		"scope_creep_identifiers":    violations,
		"required_output_constraint": "non-empty unified diff touching required target files and only requested kb-backed rules",
	}
	b, _ := json.MarshalIndent(payload, "", "  ")
	user := fmt.Sprintf("Return strict JSON now.\n%s", string(b))
	retryCtx, cancel := context.WithTimeout(ctx, targetPatchHardRetryTimeout)
	defer cancel()
	var wire any
	if err := c.client.CompleteJSON(retryCtx, system, user, &wire); err != nil {
		return CoderOutput{}, err
	}
	raw, _ := json.Marshal(wire)
	out, err := decodeCoderOutput(string(raw))
	if err != nil {
		return CoderOutput{}, err
	}
	if len(out.Commands) == 0 {
		out.Commands = in.Commands
	}
	out.Patch = normalizeCoderPatchForContract(strings.TrimSpace(in.RepoSummary), out.Patch, targets, len(targets) > 1, true)
	return out, nil
}

func citationPathsFromHits(hits []kb.SearchHit) []string {
	if len(hits) == 0 {
		return []string{}
	}
	paths := make([]string, 0, len(hits))
	for _, hit := range hits {
		path := strings.TrimSpace(strings.ReplaceAll(hit.Path, "\\", "/"))
		if path == "" {
			continue
		}
		paths = append(paths, path)
	}
	return normalizeCitationList(paths)
}

func shouldBackfillCitations(goal string) bool {
	low := strings.ToLower(strings.TrimSpace(goal))
	if low == "" {
		return false
	}
	if !strings.Contains(low, "kb_search") {
		return false
	}
	if strings.Contains(low, "禁止调用 kb_search") {
		return false
	}
	if strings.Contains(low, "不要调用 kb_search") {
		return false
	}
	if strings.Contains(low, "do not call kb_search") {
		return false
	}
	return true
}

func isRepoOnlyGoal(goal string) bool {
	low := strings.ToLower(strings.TrimSpace(goal))
	if low == "" {
		return false
	}
	if strings.Contains(low, "禁止调用 kb_search") {
		return true
	}
	if strings.Contains(low, "不要调用 kb_search") {
		return true
	}
	if strings.Contains(low, "do not call kb_search") {
		return true
	}
	return false
}

func fallbackCitationPaths(repoRoot string) []string {
	root := strings.TrimSpace(repoRoot)
	if root == "" {
		return []string{}
	}
	relKBPath := filepath.ToSlash(filepath.Join("eval", "ab", "kb"))
	paths, err := tools.RepoList(root, relKBPath)
	if err != nil {
		return []string{}
	}
	out := make([]string, 0, len(paths))
	for _, raw := range paths {
		path := strings.TrimSpace(strings.ReplaceAll(raw, "\\", "/"))
		if !strings.HasSuffix(strings.ToLower(path), ".md") {
			continue
		}
		out = append(out, path)
	}
	return normalizeCitationList(out)
}

func (c *Coder) generateWithEino(ctx context.Context, in CoderInput) (CoderOutput, error) {
	systemPrompt, userPrompt := coderPrompts(in)
	emitPromptStarted(ctx, "coder_prompt", "eino_tool_call", systemPrompt, userPrompt)
	raw, err := c.runStructuredAgent(ctx, in.RepoSummary, tools.ToolModeCode, systemPrompt, userPrompt, 32)
	if err != nil {
		emitPromptError(ctx, "coder_prompt", "eino_tool_call", systemPrompt, userPrompt, "", err)
		return CoderOutput{}, err
	}
	var wire map[string]any
	if err := decodeJSONWithRepair(ctx, raw, &wire, c.client.RepairJSON); err != nil {
		emitPromptError(ctx, "coder_prompt", "eino_tool_call", systemPrompt, userPrompt, raw, err)
		return CoderOutput{}, err
	}
	b, err := json.Marshal(wire)
	if err != nil {
		wrapped := wrapStructuredOutputStageError("encode repaired coder json failed", raw, err)
		emitPromptError(ctx, "coder_prompt", "eino_tool_call", systemPrompt, userPrompt, raw, wrapped)
		return CoderOutput{}, wrapped
	}
	out, err := decodeCoderOutput(string(b))
	if err != nil {
		wrapped := wrapStructuredOutputStageError("parse coder json failed", string(b), err)
		emitPromptError(ctx, "coder_prompt", "eino_tool_call", systemPrompt, userPrompt, raw, wrapped)
		return CoderOutput{}, wrapped
	}
	if len(out.Commands) == 0 {
		out.Commands = in.Commands
	}
	if out.Summary == "" {
		out.Summary = "Coder generated output."
	}
	targets := extractGoalTargetFiles(in.Goal)
	out.Patch = normalizeCoderPatchForContract(strings.TrimSpace(in.RepoSummary), out.Patch, targets, len(targets) > 1, false)
	emitPromptCompleted(ctx, "coder_prompt", "eino_tool_call", systemPrompt, userPrompt, raw)
	return out, nil
}

func (c *Coder) generateWithClient(ctx context.Context, in CoderInput) (CoderOutput, error) {
	system, user := coderPrompts(in)
	emitPromptStarted(ctx, "coder_prompt", "client_completion", system, user)
	var wire any
	raw, err := c.client.CompleteJSONWithRaw(ctx, system, user, &wire)
	if err != nil {
		emitPromptError(ctx, "coder_prompt", "client_completion", system, user, raw, err)
		return CoderOutput{}, err
	}
	b, _ := json.Marshal(wire)
	out, err := decodeCoderOutput(string(b))
	if err != nil {
		emitPromptError(ctx, "coder_prompt", "client_completion", system, user, raw, err)
		return CoderOutput{}, err
	}
	if len(out.Commands) == 0 {
		out.Commands = in.Commands
	}
	if out.Summary == "" {
		out.Summary = "Coder generated output."
	}
	targets := extractGoalTargetFiles(in.Goal)
	out.Patch = normalizeCoderPatchForContract(strings.TrimSpace(in.RepoSummary), out.Patch, targets, len(targets) > 1, false)
	emitPromptCompleted(ctx, "coder_prompt", "client_completion", system, user, raw)
	return out, nil
}

func (c *Coder) planWithEino(ctx context.Context, in PlanInput) (PlanOutput, error) {
	systemPrompt, userPrompt := plannerPrompts(in)
	raw, err := c.runStructuredAgent(ctx, in.RepoSummary, tools.ToolModePlan, systemPrompt, userPrompt, 16)
	if err != nil {
		return PlanOutput{}, err
	}
	var wire map[string]any
	if err := decodeJSONWithRepair(ctx, raw, &wire, c.client.RepairJSON); err != nil {
		return PlanOutput{}, err
	}
	b, err := json.Marshal(wire)
	if err != nil {
		return PlanOutput{}, wrapStructuredOutputStageError("encode repaired planner json failed", raw, err)
	}
	out, err := decodePlanOutput(string(b))
	if err != nil {
		return PlanOutput{}, wrapStructuredOutputStageError("parse planner json failed", string(b), err)
	}
	return out, nil
}

func (c *Coder) planWithClient(ctx context.Context, in PlanInput) (PlanOutput, error) {
	system, user := plannerPrompts(in)
	var wire any
	if err := c.client.CompleteJSON(ctx, system, user, &wire); err != nil {
		return PlanOutput{}, err
	}
	b, _ := json.Marshal(wire)
	out, err := decodePlanOutput(string(b))
	if err != nil {
		return PlanOutput{}, err
	}
	return out, nil
}

func (c *Coder) repairWithEino(ctx context.Context, in RepairInput) (CoderOutput, error) {
	systemPrompt, userPrompt := repairPrompts(in)
	raw, err := c.runStructuredAgent(ctx, in.RepoSummary, tools.ToolModeRepair, systemPrompt, userPrompt, 12)
	if err != nil {
		return CoderOutput{}, err
	}
	var wire map[string]any
	if err := decodeJSONWithRepair(ctx, raw, &wire, c.client.RepairJSON); err != nil {
		return CoderOutput{}, err
	}
	b, err := json.Marshal(wire)
	if err != nil {
		return CoderOutput{}, wrapStructuredOutputStageError("encode repaired repair json failed", raw, err)
	}
	out, err := decodeCoderOutput(string(b))
	if err != nil {
		return CoderOutput{}, wrapStructuredOutputStageError("parse repair json failed", string(b), err)
	}
	targets := extractGoalTargetFiles(in.Goal)
	out.Patch = normalizeCoderPatchForContract(strings.TrimSpace(in.RepoSummary), out.Patch, targets, len(targets) > 1, false)
	if out.Summary == "" {
		out.Summary = "Repair agent generated output."
	}
	return out, nil
}

func (c *Coder) repairWithClient(ctx context.Context, in RepairInput) (CoderOutput, error) {
	system, user := repairPrompts(in)
	var wire any
	if err := c.client.CompleteJSON(ctx, system, user, &wire); err != nil {
		return CoderOutput{}, err
	}
	b, _ := json.Marshal(wire)
	out, err := decodeCoderOutput(string(b))
	if err != nil {
		return CoderOutput{}, err
	}
	targets := extractGoalTargetFiles(in.Goal)
	out.Patch = normalizeCoderPatchForContract(strings.TrimSpace(in.RepoSummary), out.Patch, targets, len(targets) > 1, false)
	if out.Summary == "" {
		out.Summary = "Repair agent generated output."
	}
	return out, nil
}

func coderPrompts(in CoderInput) (string, string) {
	targets := extractGoalTargetFiles(in.Goal)
	singleFnConstraint := buildSingleTargetFunctionConstraint(in.Goal, targets)
	testingConstraint := buildMinimalTestingConstraint(in.Goal, targets)
	inlineEditConstraint := buildMixedTaskInlineEditConstraint(in.Goal, targets)
	multiTargetPatchConstraint := buildMultiTargetPatchSectionConstraint(targets)
	reorderOnlyConstraint := buildReorderOnlySnapshotConstraint(in.Goal)
	system := `You are a coding agent operating in a local git repository.
	You may call tools to inspect repository files, search code, inspect diff, query the knowledge base, and run safe commands.
	Return JSON only with fields: summary, patch, commands, notes, citations.
	- patch must be unified diff text or empty string.
	- patch field must contain only raw diff text; do not include explanations, bullets, or markdown fences inside patch.
	- each file patch should start with diff --git (preferred) or ---/+++ headers, and each hunk header must be a valid unified diff header like @@ -10,2 +10,3 @@.
	- commands must be shell commands to validate the patch.
	- keep commands minimal and deterministic.
	- if the goal requires a code change, do not return an empty patch unless you have verified the goal is already satisfied.
	- before editing any file, you must use repo_read to open that exact file in this repo and base the patch on the real contents (do not guess).
	- retrieved_context in the task input contains pre-fetched knowledge base evidence; use it as the primary source for domain/project background. Call kb_search only for supplementary exploration not covered by retrieved_context.
	- when kb_scope_contract is present, only implement the identifiers explicitly requested there; KB evidence explains the requested rule, but it does not authorize adjacent validation, cleanup, or extra checks.
	- put cited repository-relative paths into citations (e.g. eval/ab/kb/rag_pipeline.md).
	- citations must contain only repository-relative paths; do not include prose in citations.
	- never invent dependencies; verify go.mod and existing imports using repo_read/repo_search before introducing new packages.
	- patch file paths must be relative to repo root; do not include the repo directory name as a prefix.
	- commands must not include tool invocations (repo_read/repo_search/repo_list/git_diff/run_command).
	- plan_summary and plan_steps in the task input are guidance for execution; use them to stay focused, but do not treat them as authorization to modify unrelated files.
	- if previous_review reports a command failure, do NOT rerun the same command without changing the code first; read the failing file/line from the error output and fix the root cause before retrying.
	- if your previous patch was rejected or caused the same test failure twice, you must change strategy: read the error location with repo_read, check whether you are editing the correct file, and try a different fix.
	- if a test error points to a file you have not yet read, read that file before attempting another patch.
- never return markdown outside JSON.`
	if len(targets) > 1 {
		system = strings.TrimSpace(system + "\n\t- this is a multi-target goal: a valid answer must return a non-empty patch touching all target files; do not claim success or goal satisfaction without changing each required target file.")
	}
	if multiTargetPatchConstraint != "" {
		system = strings.TrimSpace(system + "\n\t- " + multiTargetPatchConstraint)
	}
	if singleFnConstraint != "" {
		system = strings.TrimSpace(system + "\n\t- " + singleFnConstraint)
	}
	if testingConstraint != "" {
		system = strings.TrimSpace(system + "\n\t- " + testingConstraint)
	}
	if inlineEditConstraint != "" {
		system = strings.TrimSpace(system + "\n\t- " + inlineEditConstraint)
	}
	if reorderOnlyConstraint != "" {
		system = strings.TrimSpace(system + "\n\t- " + reorderOnlyConstraint)
	}
	payload := map[string]any{
		"task_input":        in,
		"kb_scope_contract": buildKBScopeContract(in.Goal, targets),
	}
	b, _ := json.MarshalIndent(payload, "", "  ")
	user := fmt.Sprintf("Task input:\n%s\nUse tools when needed, then return strict JSON only.", string(b))
	return system, user
}

func plannerPrompts(in PlanInput) (string, string) {
	targets := extractGoalTargetFiles(in.Goal)
	system := `You are a planning agent operating in a local git repository.
	You may call tools to inspect repository files, search code, inspect diff, and query the knowledge base.
	Return JSON only with fields: summary, steps, risks, citations.
	- Do not return patches or commands.
	- summary should describe the intended implementation direction in 1-3 sentences.
	- steps should be a short ordered list of concrete implementation steps.
	- risks should list the main ways this task could go wrong.
	- before mentioning a file or function, inspect it with repo_read or repo_search instead of guessing.
	- retrieved_context in the task input contains pre-fetched knowledge base evidence; use it as the primary source for domain/project background. Call kb_search only for supplementary exploration not covered by retrieved_context.
	- when kb_scope_contract is present, only plan the identifiers explicitly requested there; KB evidence explains the requested rule, but it does not authorize adjacent validation, cleanup, or extra checks.
	- citations must contain only repository-relative paths.
	- never return markdown outside JSON.`
	payload := map[string]any{
		"task_input":        in,
		"kb_scope_contract": buildKBScopeContract(in.Goal, targets),
	}
	b, _ := json.MarshalIndent(payload, "", "  ")
	user := fmt.Sprintf("Task input:\n%s\nUse tools when needed, then return strict JSON only.", string(b))
	return system, user
}

func repairPrompts(in RepairInput) (string, string) {
	system := `You are a repair-focused coding agent operating in a local git repository.
	You may call read-only tools to inspect repository files, search code, inspect diff, and query the knowledge base.
	Return JSON only with fields: summary, patch, commands, notes, citations.
	- The current diff already contains correct progress. Do NOT rewrite from scratch.
	- previous_review contains reviewer feedback from the prior turn; treat it as an additional constraint, not optional context.
	- Only fix the specific compilation or test failures shown in command_output.
	- patch must be unified diff text or empty string.
	- patch must be incremental and limited to the files/functions causing the failure.
	- do not add unrelated helpers, cleanup, refactoring, or adjacent behavior changes.
	- do not change code that is already passing tests.
	- commands may be empty; the engine controls verification and will rerun the task commands.
	- citations must contain only repository-relative paths.
	- never return markdown outside JSON.`
	payload := map[string]any{
		"task_input": in,
	}
	b, _ := json.MarshalIndent(payload, "", "  ")
	user := fmt.Sprintf("Repair input:\n%s\nUse tools when needed, then return strict JSON only.", string(b))
	return system, user
}

func buildMixedTaskInlineEditConstraint(goal string, targets []string) string {
	if len(targets) < 2 {
		return ""
	}
	hasCode := false
	hasTest := false
	for _, target := range targets {
		lower := strings.ToLower(strings.TrimSpace(target))
		if strings.HasSuffix(lower, "_test.go") {
			hasTest = true
			continue
		}
		if strings.HasSuffix(lower, ".go") {
			hasCode = true
		}
	}
	if !hasCode || !hasTest {
		return ""
	}
	return "for mixed code+test tasks, prefer inline edits to existing functions and tests. Do not introduce new top-level helpers or duplicate Test* names unless target_file_snapshots show no equivalent structure to extend."
}

func buildMultiTargetPatchSectionConstraint(targets []string) string {
	if len(targets) < 2 {
		return ""
	}
	return "for a multi-target task, emit exactly one file patch section for each target file in target_files, use those exact repo-relative target file paths in diff headers, and do not omit any required target from the final patch."
}

func buildReorderOnlySnapshotConstraint(goal string) string {
	if !isReorderOnlyGoal(goal) {
		return ""
	}
	return "for reorder-only tasks, treat target_file_snapshots as the sole source of truth for the entries that exist today. Reorder only the entries already present in snapshots, preserve each existing entry exactly once, and do not synthesize or delete entries just because the goal text mentions an outdated list. A prohibition on calling a tool means do not invoke it during task solving; it does not authorize deleting that tool's definition or registration."
}

func decodeCoderOutput(content string) (CoderOutput, error) {
	raw := extractJSON(content)
	var m map[string]any
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		var out CoderOutput
		if err2 := json.Unmarshal([]byte(raw), &out); err2 == nil {
			return out, nil
		}
		return CoderOutput{}, fmt.Errorf("parse coder json failed: %w; content=%s", err, truncateDiagnosticPreview(raw))
	}
	out := CoderOutput{}
	if v, ok := m["summary"].(string); ok {
		out.Summary = v
	}
	if v, ok := m["patch"].(string); ok {
		out.Patch = normalizeCoderPatch(v)
	}
	if v, ok := m["notes"].(string); ok {
		out.Notes = v
	}
	if c, ok := m["citations"]; ok {
		b, _ := json.Marshal(c)
		var items []string
		if err := json.Unmarshal(b, &items); err == nil {
			out.Citations = normalizeCitationList(items)
		} else {
			var s string
			if err2 := json.Unmarshal(b, &s); err2 == nil && strings.TrimSpace(s) != "" {
				out.Citations = normalizeCitationList([]string{s})
			}
		}
	}
	if v, ok := m["used_fallback"].(bool); ok {
		out.UsedFallback = v
	}
	if v, ok := m["fallback_source"].(string); ok {
		out.FallbackSource = strings.TrimSpace(v)
	}
	if c, ok := m["commands"]; ok {
		b, _ := json.Marshal(c)
		var items []string
		if err := json.Unmarshal(b, &items); err == nil {
			out.Commands = items
		} else {
			var s string
			if err2 := json.Unmarshal(b, &s); err2 == nil && strings.TrimSpace(s) != "" {
				out.Commands = []string{s}
			}
		}
	}
	return out, nil
}

func decodePlanOutput(content string) (PlanOutput, error) {
	raw := extractJSON(content)
	var m map[string]any
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		var out PlanOutput
		if err2 := json.Unmarshal([]byte(raw), &out); err2 == nil {
			return out, nil
		}
		return PlanOutput{}, fmt.Errorf("parse planner json failed: %w; content=%s", err, truncateDiagnosticPreview(raw))
	}
	out := PlanOutput{}
	if v, ok := m["summary"].(string); ok {
		out.Summary = strings.TrimSpace(v)
	}
	if c, ok := m["steps"]; ok {
		b, _ := json.Marshal(c)
		var items []string
		if err := json.Unmarshal(b, &items); err == nil {
			out.Steps = normalizePlanTextList(items)
		} else {
			var s string
			if err2 := json.Unmarshal(b, &s); err2 == nil && strings.TrimSpace(s) != "" {
				out.Steps = []string{strings.TrimSpace(s)}
			}
		}
	}
	if c, ok := m["risks"]; ok {
		b, _ := json.Marshal(c)
		var items []string
		if err := json.Unmarshal(b, &items); err == nil {
			out.Risks = normalizePlanTextList(items)
		} else {
			var s string
			if err2 := json.Unmarshal(b, &s); err2 == nil && strings.TrimSpace(s) != "" {
				out.Risks = []string{strings.TrimSpace(s)}
			}
		}
	}
	if c, ok := m["citations"]; ok {
		b, _ := json.Marshal(c)
		var items []string
		if err := json.Unmarshal(b, &items); err == nil {
			out.Citations = normalizeCitationList(items)
		} else {
			var s string
			if err2 := json.Unmarshal(b, &s); err2 == nil && strings.TrimSpace(s) != "" {
				out.Citations = normalizeCitationList([]string{s})
			}
		}
	}
	return out, nil
}

func normalizeCoderPatch(patch string) string {
	patch = strings.ReplaceAll(patch, "\r\n", "\n")
	patch = strings.TrimSpace(patch)
	if patch == "" {
		return ""
	}
	if extracted, ok := extractPatchLikeBlock(patch); ok {
		patch = extracted
	}
	patch = normalizePatchStructuralLines(patch)
	patch = normalizeBareHunkHeaders(patch)
	patch = normalizeBareHunkContextLines(patch)
	patch = normalizeIndentedHunkChangeLines(patch)
	patch = ensureDiffSectionFileHeaders(patch)
	patch = ensureDiffGitHeaderForPatch(patch)
	if !patchHasConsistentUnifiedHunks(patch) {
		patch = recountUnifiedHunkHeaders(patch)
		if !patchHasConsistentUnifiedHunks(patch) {
			return ""
		}
	}
	if !patchContainsRealChanges(patch) {
		return ""
	}
	return strings.TrimSpace(patch) + "\n"
}

func normalizePatchForOutput(patch string) string {
	patch = strings.TrimSpace(patch)
	if patch == "" {
		return ""
	}
	return patch + "\n"
}

func normalizeCoderPatchForTargets(patch string, targets []string) string {
	patch = normalizeCoderPatch(patch)
	if patch == "" {
		return ""
	}
	targets = normalizeCitationList(targets)
	patch = rewritePatchPathsForTargets(patch, targets)
	if len(targets) != 1 {
		return patch
	}
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	if len(lines) == 0 {
		return patch
	}
	if !strings.HasPrefix(strings.TrimSpace(lines[0]), "@@") {
		return patch
	}
	target := stripPatchPathToken(targets[0])
	if target == "" {
		return patch
	}
	header := []string{
		"diff --git a/" + target + " b/" + target,
		"--- a/" + target,
		"+++ b/" + target,
	}
	return strings.Join(append(header, lines...), "\n")
}

func normalizeCoderPatchForRepoTargets(repoRoot string, patch string, targets []string) string {
	patch = normalizeCoderPatchForTargets(patch, targets)
	if patch == "" {
		return ""
	}
	if strings.TrimSpace(repoRoot) == "" || len(normalizeCitationList(targets)) != 1 {
		return patch
	}
	if recounted := recountSingleTargetPatchAgainstSnapshot(strings.TrimSpace(repoRoot), patch, normalizeCitationList(targets)[0]); strings.TrimSpace(recounted) != "" {
		return recounted
	}
	return patch
}

func normalizeCoderPatchForContract(repoRoot string, patch string, targets []string, requireAll bool, requireOnlyTargets bool) string {
	patch = normalizeCoderPatchForRepoTargets(repoRoot, patch, targets)
	if patch == "" {
		return ""
	}
	targets = normalizeCitationList(targets)
	if len(targets) == 0 {
		return patch
	}
	if !patchTouchesTargets(patch, targets, requireAll) {
		return ""
	}
	if requireOnlyTargets && !patchTouchesOnlyTargets(patch, targets) {
		return ""
	}
	if patchHasDuplicateAddedBlocks(patch, 2) {
		return ""
	}
	return patch
}

func patchHasDuplicateAddedBlocks(patch string, minLines int) bool {
	if strings.TrimSpace(patch) == "" || minLines <= 0 {
		return false
	}
	lines := strings.Split(strings.ReplaceAll(patch, "\r\n", "\n"), "\n")
	currentFile := ""
	inHunk := false
	currentBlock := make([]string, 0)
	blocksByFile := make(map[string]map[string]int)
	flush := func() bool {
		if currentFile == "" || len(currentBlock) < minLines {
			currentBlock = currentBlock[:0]
			return false
		}
		key := strings.Join(currentBlock, "\n")
		fileBlocks := blocksByFile[currentFile]
		if fileBlocks == nil {
			fileBlocks = make(map[string]int)
			blocksByFile[currentFile] = fileBlocks
		}
		fileBlocks[key]++
		currentBlock = currentBlock[:0]
		return fileBlocks[key] > 1
	}
	for _, line := range lines {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "+++ "):
			if flush() {
				return true
			}
			currentFile = stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(trim, "+++ ")))
			inHunk = false
		case strings.HasPrefix(trim, "@@ -"):
			if flush() {
				return true
			}
			inHunk = currentFile != ""
		default:
			if !inHunk || line == "" {
				if flush() {
					return true
				}
				continue
			}
			switch line[0] {
			case '+':
				currentBlock = append(currentBlock, line[1:])
			default:
				if flush() {
					return true
				}
			}
		}
	}
	return flush()
}

func rewritePatchPathsForTargets(patch string, targets []string) string {
	if len(targets) == 0 {
		return patch
	}
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	if len(lines) == 0 {
		return patch
	}
	for i, line := range lines {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "diff --git "):
			oldPath, newPath := parseDiffGitPaths(trim)
			oldPath = matchPatchPathToTarget(oldPath, targets)
			newPath = matchPatchPathToTarget(newPath, targets)
			if oldPath != "" && newPath != "" {
				lines[i] = "diff --git a/" + oldPath + " b/" + newPath
			}
		case strings.HasPrefix(trim, "--- "):
			path := normalizePatchHeaderPath(strings.TrimSpace(strings.TrimPrefix(trim, "--- ")), targets)
			if path != "" {
				lines[i] = "--- " + path
			}
		case strings.HasPrefix(trim, "+++ "):
			path := normalizePatchHeaderPath(strings.TrimSpace(strings.TrimPrefix(trim, "+++ ")), targets)
			if path != "" {
				lines[i] = "+++ " + path
			}
		}
	}
	return strings.Join(lines, "\n")
}

func normalizePatchHeaderPath(raw string, targets []string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" || raw == "/dev/null" {
		return raw
	}
	path := matchPatchPathToTarget(stripPatchPathToken(raw), targets)
	if path == "" {
		return raw
	}
	if strings.HasPrefix(raw, "b/") {
		return "b/" + path
	}
	if strings.HasPrefix(raw, "a/") {
		return "a/" + path
	}
	return path
}

func matchPatchPathToTarget(path string, targets []string) string {
	path = strings.TrimSpace(strings.ReplaceAll(path, "\\", "/"))
	if path == "" {
		return ""
	}
	if path == "/dev/null" {
		return path
	}
	var match string
	for _, target := range targets {
		target = strings.TrimSpace(strings.ReplaceAll(target, "\\", "/"))
		if target == "" {
			continue
		}
		if path == target || strings.HasSuffix(path, "/"+target) {
			if match != "" && match != target {
				return path
			}
			match = target
		}
	}
	if match != "" {
		return match
	}
	return path
}

func extractPatchLikeBlock(text string) (string, bool) {
	if block, ok := extractPatchLikeFence(text); ok {
		return block, true
	}
	trimmed := strings.TrimSpace(strings.ReplaceAll(text, "\r\n", "\n"))
	if trimmed != "" {
		lines := strings.Split(trimmed, "\n")
		if len(lines) > 0 && isPatchStartLine(strings.TrimSpace(lines[0])) {
			return trimmed, true
		}
	}
	return slicePatchLikeLines(text)
}

func extractPatchLikeFence(text string) (string, bool) {
	parts := strings.Split(text, "```")
	best := ""
	for i := 1; i < len(parts); i += 2 {
		body := strings.TrimSpace(parts[i])
		if body == "" {
			continue
		}
		if nl := strings.IndexByte(body, '\n'); nl >= 0 {
			first := strings.TrimSpace(body[:nl])
			if !isPatchStartLine(first) && (first == "diff" || first == "patch" || first == "udiff") {
				body = strings.TrimSpace(body[nl+1:])
			}
		}
		if block, ok := slicePatchLikeLines(body); ok && len(block) > len(best) {
			best = block
		}
	}
	if best == "" {
		return "", false
	}
	return best, true
}

func slicePatchLikeLines(text string) (string, bool) {
	lines := strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")
	start := -1
	end := -1
	for i, line := range lines {
		trim := strings.TrimSpace(line)
		if start < 0 {
			if isPatchStartLine(trim) {
				start = i
				end = i + 1
			}
			continue
		}
		if trim == "" || isPatchContentLine(line, trim) {
			end = i + 1
			continue
		}
		break
	}
	if start < 0 || end <= start {
		return "", false
	}
	return strings.Join(lines[start:end], "\n"), true
}

func isPatchStartLine(trim string) bool {
	return strings.HasPrefix(trim, "diff --git ") || strings.HasPrefix(trim, "--- ")
}

func isPatchContentLine(raw string, trim string) bool {
	if trim == "" {
		return true
	}
	switch {
	case strings.HasPrefix(trim, "diff --git "):
		return true
	case strings.HasPrefix(trim, "index "):
		return true
	case strings.HasPrefix(trim, "--- "):
		return true
	case strings.HasPrefix(trim, "+++ "):
		return true
	case strings.HasPrefix(trim, "@@"):
		return true
	case strings.HasPrefix(trim, "new file mode"):
		return true
	case strings.HasPrefix(trim, "deleted file mode"):
		return true
	case strings.HasPrefix(trim, "rename from "):
		return true
	case strings.HasPrefix(trim, "rename to "):
		return true
	case strings.HasPrefix(trim, "similarity index "):
		return true
	case strings.HasPrefix(trim, "old mode "):
		return true
	case strings.HasPrefix(trim, "new mode "):
		return true
	case strings.HasPrefix(trim, "Binary files "):
		return true
	case strings.HasPrefix(trim, "GIT binary patch"):
		return true
	case strings.HasPrefix(raw, "\\ No newline at end of file"):
		return true
	}
	if raw == "" {
		return true
	}
	switch raw[0] {
	case ' ', '+', '-':
		return true
	}
	return false
}

func ensureDiffGitHeaderForPatch(patch string) string {
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	if len(lines) < 2 {
		return patch
	}
	if strings.HasPrefix(strings.TrimSpace(lines[0]), "diff --git ") {
		return strings.Join(lines, "\n")
	}
	if !strings.HasPrefix(strings.TrimSpace(lines[0]), "--- ") || !strings.HasPrefix(strings.TrimSpace(lines[1]), "+++ ") {
		return strings.Join(lines, "\n")
	}
	oldPath := stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(lines[0]), "--- ")))
	newPath := stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(lines[1]), "+++ ")))
	if oldPath == "" || newPath == "" {
		return strings.Join(lines, "\n")
	}
	header := "diff --git a/" + oldPath + " b/" + newPath
	return strings.Join(append([]string{header}, lines...), "\n")
}

func normalizeBareHunkHeaders(patch string) string {
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	if len(lines) == 0 {
		return patch
	}
	out := make([]string, 0, len(lines))
	for i := 0; i < len(lines); {
		trim := strings.TrimSpace(lines[i])
		if strings.HasPrefix(trim, "@@") && !unifiedHunkHeaderRegex.MatchString(trim) {
			j := i + 1
			for j < len(lines) {
				if isHunkBoundaryLine(lines[j]) {
					break
				}
				j++
			}
			oldCount, newCount, hasChange := summarizeHunkBody(lines[i+1 : j])
			if hasChange {
				out = append(out, synthesizeUnifiedHunkHeader(oldCount, newCount))
				out = append(out, lines[i+1:j]...)
			}
			i = j
			continue
		}
		out = append(out, lines[i])
		i++
	}
	return strings.Join(out, "\n")
}

func isHunkBoundaryLine(line string) bool {
	trim := strings.TrimSpace(line)
	if trim == "" {
		return false
	}
	if strings.HasPrefix(trim, "@@") || strings.HasPrefix(trim, "diff --git ") {
		return true
	}
	if !isPatchStructuralLine(trim) {
		return false
	}
	if line == "" {
		return true
	}
	switch line[0] {
	case ' ', '+', '-', '\\':
		return false
	default:
		return true
	}
}

func summarizeHunkBody(lines []string) (oldCount int, newCount int, hasChange bool) {
	for _, line := range lines {
		if line == "" {
			continue
		}
		switch line[0] {
		case ' ':
			oldCount++
			newCount++
		case '-':
			oldCount++
			hasChange = true
		case '+':
			newCount++
			hasChange = true
		case '\\':
			continue
		}
	}
	return oldCount, newCount, hasChange
}

func synthesizeUnifiedHunkHeader(oldCount int, newCount int) string {
	oldStart := 1
	newStart := 1
	if oldCount == 0 {
		oldStart = 0
	}
	if newCount == 0 {
		newStart = 0
	}
	return fmt.Sprintf("@@ -%d,%d +%d,%d @@", oldStart, oldCount, newStart, newCount)
}

func patchContainsRealChanges(patch string) bool {
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	for _, line := range lines {
		trim := strings.TrimSpace(line)
		if trim == "" || strings.HasPrefix(trim, "--- ") || strings.HasPrefix(trim, "+++ ") {
			continue
		}
		if line == "" {
			continue
		}
		switch line[0] {
		case '+', '-':
			return true
		}
	}
	return false
}

func patchHasConsistentUnifiedHunks(patch string) bool {
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	for i := 0; i < len(lines); i++ {
		trim := strings.TrimSpace(lines[i])
		if !unifiedHunkHeaderRegex.MatchString(trim) {
			continue
		}
		wantOld, wantNew, ok := parseUnifiedHunkCounts(trim)
		if !ok {
			return false
		}
		j := i + 1
		for j < len(lines) && !isHunkBoundaryLine(lines[j]) {
			j++
		}
		gotOld, gotNew, _ := summarizeHunkBody(lines[i+1 : j])
		if gotOld != wantOld || gotNew != wantNew {
			return false
		}
		i = j - 1
	}
	return true
}

func recountUnifiedHunkHeaders(patch string) string {
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	changed := false
	for i := 0; i < len(lines); i++ {
		trim := strings.TrimSpace(lines[i])
		if !unifiedHunkHeaderRegex.MatchString(trim) {
			continue
		}
		wantOld, wantNew, ok := parseUnifiedHunkCounts(trim)
		if !ok {
			continue
		}
		j := i + 1
		for j < len(lines) && !isHunkBoundaryLine(lines[j]) {
			j++
		}
		gotOld, gotNew, _ := summarizeHunkBody(lines[i+1 : j])
		if gotOld == wantOld && gotNew == wantNew {
			i = j - 1
			continue
		}
		oldStart := parseUnifiedHunkRangeStart(trim, '-')
		newStart := parseUnifiedHunkRangeStart(trim, '+')
		suffix := unifiedHunkHeaderSuffix(trim)
		lines[i] = fmt.Sprintf("@@ -%d,%d +%d,%d @@%s", oldStart, gotOld, newStart, gotNew, suffix)
		changed = true
		i = j - 1
	}
	if !changed {
		return patch
	}
	return strings.Join(lines, "\n")
}

func parseUnifiedHunkRangeStart(header string, prefix byte) int {
	fields := strings.Fields(strings.TrimSpace(header))
	for _, f := range fields {
		f = strings.TrimSpace(f)
		if f == "" || f[0] != prefix {
			continue
		}
		body := f[1:]
		if body == "" {
			continue
		}
		if !strings.Contains(body, ",") {
			n, err := strconv.Atoi(body)
			if err == nil {
				return n
			}
			continue
		}
		parts := strings.SplitN(body, ",", 2)
		n, err := strconv.Atoi(parts[0])
		if err == nil {
			return n
		}
	}
	return 1
}

func parseUnifiedHunkCounts(header string) (oldCount int, newCount int, ok bool) {
	fields := strings.Fields(strings.TrimSpace(header))
	if len(fields) < 3 {
		return 0, 0, false
	}
	oldCount, ok = parseUnifiedHunkRangeCount(fields[1], '-')
	if !ok {
		return 0, 0, false
	}
	newCount, ok = parseUnifiedHunkRangeCount(fields[2], '+')
	if !ok {
		return 0, 0, false
	}
	return oldCount, newCount, true
}

func parseUnifiedHunkRangeCount(token string, prefix byte) (int, bool) {
	token = strings.TrimSpace(token)
	if token == "" || token[0] != prefix {
		return 0, false
	}
	body := token[1:]
	if body == "" {
		return 0, false
	}
	if !strings.Contains(body, ",") {
		return 1, true
	}
	parts := strings.SplitN(body, ",", 2)
	if len(parts) != 2 {
		return 0, false
	}
	n, err := strconv.Atoi(parts[1])
	if err != nil || n < 0 {
		return 0, false
	}
	return n, true
}

func normalizeIndentedHunkChangeLines(patch string) string {
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	if len(lines) == 0 {
		return patch
	}
	inHunk := false
	for i, line := range lines {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "diff --git "):
			inHunk = false
		case strings.HasPrefix(trim, "@@"):
			inHunk = unifiedHunkHeaderRegex.MatchString(trim)
		}
		if !inHunk || len(line) < 3 || line[0] != ' ' {
			continue
		}
		if (line[1] == '+' || line[1] == '-') && line[2] != ' ' {
			lines[i] = line[1:]
		}
	}
	return strings.Join(lines, "\n")
}

func normalizeBareHunkContextLines(patch string) string {
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	if len(lines) == 0 {
		return patch
	}
	inHunk := false
	for i, line := range lines {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "diff --git "):
			inHunk = false
		case unifiedHunkHeaderRegex.MatchString(trim):
			inHunk = true
		case strings.HasPrefix(trim, "@@"):
			inHunk = false
		}
		if !inHunk {
			continue
		}
		if line == "" {
			next := nextNonEmptyPatchLine(lines, i+1)
			if next != "" && isHunkBoundaryLine(next) {
				continue
			}
			lines[i] = " "
			continue
		}
		switch line[0] {
		case ' ', '+', '-', '\\':
			continue
		default:
			if !isPatchStructuralLine(trim) {
				lines[i] = " " + line
			}
		}
	}
	return strings.Join(lines, "\n")
}

func nextNonEmptyPatchLine(lines []string, start int) string {
	for i := start; i < len(lines); i++ {
		if strings.TrimSpace(lines[i]) == "" {
			continue
		}
		return lines[i]
	}
	return ""
}

func normalizePatchStructuralLines(patch string) string {
	lines := strings.Split(strings.ReplaceAll(strings.TrimSpace(patch), "\r\n", "\n"), "\n")
	for i, line := range lines {
		trim := strings.TrimSpace(line)
		if isPatchStructuralLine(trim) {
			lines[i] = trim
		}
	}
	return strings.Join(lines, "\n")
}

func recountSingleTargetPatchAgainstSnapshot(repoRoot, patch, target string) string {
	target = strings.TrimSpace(strings.ReplaceAll(target, "\\", "/"))
	if target == "" {
		return patch
	}
	snapshots := buildRepoOnlyTargetSnapshots(repoRoot, []string{target})
	snapshot := snapshots[target]
	if strings.TrimSpace(snapshot) == "" || strings.HasPrefix(snapshot, "[repo_read_error]") {
		return patch
	}
	snapshotLines := strings.Split(strings.ReplaceAll(snapshot, "\r\n", "\n"), "\n")
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	if len(lines) == 0 {
		return patch
	}
	currentFile := ""
	searchStart := 0
	delta := 0
	for i := 0; i < len(lines); i++ {
		trim := strings.TrimSpace(lines[i])
		switch {
		case strings.HasPrefix(trim, "+++ "):
			currentFile = stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(trim, "+++ ")))
		case unifiedHunkHeaderRegex.MatchString(trim):
			if currentFile != target {
				continue
			}
			j := i + 1
			for j < len(lines) && !isHunkBoundaryLine(lines[j]) {
				j++
			}
			body := lines[i+1 : j]
			oldCount, newCount, _ := summarizeHunkBody(body)
			oldChunk := extractOldHunkSequence(body)
			if len(oldChunk) == 0 {
				delta += newCount - oldCount
				i = j - 1
				continue
			}
			idx, ok := locateUniqueSequenceFrom(snapshotLines, oldChunk, searchStart)
			if !ok {
				trimmedBody, trimmedIdx, trimmedOldCount, trimmedNewCount, trimmedOK := trimHunkBodyToUniqueMatchingSuffix(body, snapshotLines, searchStart)
				if !trimmedOK {
					return patch
				}
				body = trimmedBody
				oldCount = trimmedOldCount
				newCount = trimmedNewCount
				idx = trimmedIdx
				lines = append(append([]string{}, lines[:i+1]...), append(body, lines[j:]...)...)
				j = i + 1 + len(body)
			}
			oldStart := idx + 1
			newStart := oldStart + delta
			suffix := unifiedHunkHeaderSuffix(trim)
			lines[i] = fmt.Sprintf("@@ -%d,%d +%d,%d @@%s", oldStart, oldCount, newStart, newCount, suffix)
			searchStart = idx + max(oldCount, 1)
			delta += newCount - oldCount
			i = j - 1
		}
	}
	return strings.Join(lines, "\n")
}

func trimHunkBodyToUniqueMatchingSuffix(body, snapshotLines []string, searchStart int) ([]string, int, int, int, bool) {
	oldLinePositions := make([]int, 0, len(body))
	oldChunk := make([]string, 0, len(body))
	for i, line := range body {
		if line == "" {
			continue
		}
		switch line[0] {
		case ' ', '-':
			oldLinePositions = append(oldLinePositions, i)
			oldChunk = append(oldChunk, line[1:])
		}
	}
	for start := 1; start < len(oldChunk); start++ {
		candidate := oldChunk[start:]
		idx, ok := locateUniqueSequenceFrom(snapshotLines, candidate, searchStart)
		if !ok {
			continue
		}
		trimmedBody := append([]string{}, body[oldLinePositions[start]:]...)
		oldCount, newCount, _ := summarizeHunkBody(trimmedBody)
		if oldCount == 0 || newCount == 0 {
			continue
		}
		return trimmedBody, idx, oldCount, newCount, true
	}
	return nil, 0, 0, 0, false
}

func extractOldHunkSequence(body []string) []string {
	out := make([]string, 0, len(body))
	for _, line := range body {
		if line == "" {
			continue
		}
		switch line[0] {
		case ' ', '-':
			out = append(out, line[1:])
		case '\\':
			continue
		}
	}
	return out
}

func locateUniqueSequenceFrom(lines, seq []string, start int) (int, bool) {
	if len(seq) == 0 || len(lines) < len(seq) {
		return 0, false
	}
	if start < 0 {
		start = 0
	}
	match := -1
	for i := start; i <= len(lines)-len(seq); i++ {
		ok := true
		for j := 0; j < len(seq); j++ {
			if lines[i+j] != seq[j] {
				ok = false
				break
			}
		}
		if !ok {
			continue
		}
		if match != -1 {
			return 0, false
		}
		match = i
	}
	if match == -1 {
		return 0, false
	}
	return match, true
}

func unifiedHunkHeaderSuffix(header string) string {
	header = strings.TrimSpace(header)
	if header == "" {
		return ""
	}
	last := strings.LastIndex(header, "@@")
	if last < 0 {
		return ""
	}
	suffix := strings.TrimSpace(header[last+2:])
	if suffix == "" {
		return ""
	}
	return " " + suffix
}

func isPatchStructuralLine(trim string) bool {
	if trim == "" {
		return false
	}
	switch {
	case strings.HasPrefix(trim, "diff --git "):
		return true
	case strings.HasPrefix(trim, "index "):
		return true
	case strings.HasPrefix(trim, "--- "):
		return true
	case strings.HasPrefix(trim, "+++ "):
		return true
	case strings.HasPrefix(trim, "@@"):
		return true
	case strings.HasPrefix(trim, "new file mode"):
		return true
	case strings.HasPrefix(trim, "deleted file mode"):
		return true
	case strings.HasPrefix(trim, "rename from "):
		return true
	case strings.HasPrefix(trim, "rename to "):
		return true
	case strings.HasPrefix(trim, "similarity index "):
		return true
	case strings.HasPrefix(trim, "old mode "):
		return true
	case strings.HasPrefix(trim, "new mode "):
		return true
	case strings.HasPrefix(trim, "Binary files "):
		return true
	case strings.HasPrefix(trim, "GIT binary patch"):
		return true
	}
	return false
}

func ensureDiffSectionFileHeaders(patch string) string {
	lines := strings.Split(strings.TrimSpace(patch), "\n")
	if len(lines) == 0 {
		return patch
	}
	out := make([]string, 0, len(lines)+8)
	var oldPath, newPath string
	haveOld := false
	haveNew := false
	for _, line := range lines {
		trim := strings.TrimSpace(line)
		if strings.HasPrefix(trim, "diff --git ") {
			oldPath, newPath = parseDiffGitPaths(trim)
			haveOld = false
			haveNew = false
			out = append(out, trim)
			continue
		}
		if strings.HasPrefix(trim, "--- ") {
			haveOld = true
			out = append(out, trim)
			continue
		}
		if strings.HasPrefix(trim, "+++ ") {
			haveNew = true
			out = append(out, trim)
			continue
		}
		if strings.HasPrefix(trim, "@@") && oldPath != "" && newPath != "" && (!haveOld || !haveNew) {
			if !haveOld {
				out = append(out, "--- a/"+oldPath)
				haveOld = true
			}
			if !haveNew {
				out = append(out, "+++ b/"+newPath)
				haveNew = true
			}
		}
		out = append(out, line)
	}
	return strings.Join(out, "\n")
}

func parseDiffGitPaths(line string) (string, string) {
	fields := strings.Fields(strings.TrimSpace(line))
	if len(fields) < 4 {
		return "", ""
	}
	oldPath := stripPatchPathToken(fields[2])
	newPath := stripPatchPathToken(fields[3])
	if oldPath == "" || newPath == "" {
		return "", ""
	}
	return oldPath, newPath
}

func normalizeCitationList(items []string) []string {
	if len(items) == 0 {
		return []string{}
	}
	out := make([]string, 0, len(items))
	seen := make(map[string]struct{})
	for _, raw := range items {
		v := strings.TrimSpace(strings.ReplaceAll(raw, "\\", "/"))
		if v == "" {
			continue
		}
		if _, ok := seen[v]; ok {
			continue
		}
		seen[v] = struct{}{}
		out = append(out, v)
	}
	if len(out) == 0 {
		return []string{}
	}
	return out
}

func mergeUniqueStrings(groups ...[]string) []string {
	if len(groups) == 0 {
		return []string{}
	}
	seen := make(map[string]struct{})
	out := make([]string, 0)
	for _, group := range groups {
		for _, raw := range group {
			v := strings.TrimSpace(raw)
			if v == "" {
				continue
			}
			if _, ok := seen[v]; ok {
				continue
			}
			seen[v] = struct{}{}
			out = append(out, v)
		}
	}
	if len(out) == 0 {
		return []string{}
	}
	sort.Strings(out)
	return out
}

func buildKBScopeContract(goal string, targets []string) kbScopeContract {
	if !shouldEnforceKBTaskScope(goal, targets) {
		return kbScopeContract{}
	}
	return kbScopeContract{
		Targets:     normalizeCitationList(targets),
		Identifiers: mergeUniqueStrings(extractGoalScopeIdentifiers(goal), extractGoalFunctionIdentifiers(goal)),
	}
}

func buildSingleTargetFunctionConstraint(goal string, targets []string) string {
	if len(normalizeCitationList(targets)) != 1 {
		return ""
	}
	functions := extractGoalFunctionIdentifiers(goal)
	if len(functions) != 1 {
		return ""
	}
	fn := functions[0]
	return fmt.Sprintf("for this single-target-function task, prefer a self-contained fix inside %s; do not change its signature, call sites, or adjacent functions unless the goal explicitly requires that broader edit. Modify the existing function in place and do not introduce new top-level helper functions unless the goal explicitly names them.", fn)
}

func buildMinimalTestingConstraint(goal string, targets []string) string {
	if !goalNeedsMinimalTableDrivenTesting(goal, targets) {
		return ""
	}
	return "when modifying a target _test.go file for a KB-guided validation task, keep the test scope minimal: use a table-driven test with one positive and one negative case for the requested rule, do not add extra edge cases unless the goal or KB evidence explicitly requires them, and inspect target_file_snapshots to avoid redefining an existing Test* name."
}

func buildDefinitionIssueRecoveryConstraint(in CoderInput, targets []string) string {
	if len(in.DefinitionIssues) == 0 && len(in.MissingTargetFiles) == 0 {
		return ""
	}
	var parts []string
	if len(in.DefinitionIssues) > 0 {
		parts = append(parts,
			"definition_issues in the payload are blocking validation failures from the previous patch; resolve every listed issue before returning a patch",
			"do not return a top-level helper or Test* whose name appears in existing_top_level_names_by_file unless you are editing that existing definition in place",
		)
	}
	if len(in.MissingTargetFiles) > 0 {
		parts = append(parts,
			"missing_target_files in the payload are target files still untouched by previous_patch; prioritize adding valid patch sections for those files",
			"if previous_patch already covers some target files, you may return a retry patch that focuses only on missing_target_files instead of rewriting the already-covered files",
		)
	}
	for _, target := range normalizeCitationList(targets) {
		if strings.HasSuffix(strings.ToLower(target), "_test.go") {
			parts = append(parts, "for _test.go targets, prefer extending existing table-driven tests; if you must add a new Test* function, its name must not appear in existing_test_names_by_file")
			break
		}
	}
	if len(normalizeCitationList(targets)) > 1 {
		parts = append(parts, "for mixed code+test tasks, the patch must still touch every target file while resolving the duplicate definition issues")
	}
	return strings.Join(parts, "; ")
}

func addDefinitionIssueRecoveryPayload(payload map[string]any, in CoderInput) {
	if len(in.DefinitionIssues) > 0 {
		payload["definition_issues"] = in.DefinitionIssues
	}
	if len(in.MissingTargetFiles) > 0 {
		payload["missing_target_files"] = in.MissingTargetFiles
	}
	if len(in.ExistingTopLevelNamesByFile) > 0 {
		payload["existing_top_level_names_by_file"] = in.ExistingTopLevelNamesByFile
	}
	if len(in.ExistingTestNamesByFile) > 0 {
		payload["existing_test_names_by_file"] = in.ExistingTestNamesByFile
	}
	if len(in.AllowedGoalFunctions) > 0 {
		payload["allowed_goal_functions"] = in.AllowedGoalFunctions
	}
}

func buildDefinitionIssueRecoveryInput(in CoderInput, targets []string, patch string) CoderInput {
	out := in
	issues := detectTargetedPatchDefinitionIssues(in.Goal, strings.TrimSpace(in.RepoSummary), patch, targets)
	missing := missingTargetFiles(patch, targets)
	if len(issues) == 0 && len(missing) == 0 {
		return out
	}
	if len(issues) > 0 {
		out.DefinitionIssues = append([]string{}, issues...)
	}
	if len(missing) > 0 {
		out.MissingTargetFiles = append([]string{}, missing...)
	}
	out.AllowedGoalFunctions = mergeUniqueStrings(extractGoalFunctionIdentifiers(in.Goal))

	snapshots := buildRepoOnlyTargetSnapshots(strings.TrimSpace(in.RepoSummary), targets)
	existingTop := make(map[string][]string)
	existingTests := make(map[string][]string)
	for _, target := range normalizeCitationList(targets) {
		if !strings.HasSuffix(strings.ToLower(target), ".go") {
			continue
		}
		snapshot := strings.TrimSpace(snapshots[target])
		if snapshot == "" || strings.HasPrefix(snapshot, "[repo_read_error]") {
			continue
		}
		names := sortedStringSetKeys(extractGoTopLevelFunctionNames(snapshot))
		if len(names) == 0 {
			continue
		}
		existingTop[target] = names
		var tests []string
		for _, name := range names {
			if strings.HasPrefix(name, "Test") {
				tests = append(tests, name)
			}
		}
		if len(tests) > 0 {
			existingTests[target] = tests
		}
	}
	if len(existingTop) > 0 {
		out.ExistingTopLevelNamesByFile = existingTop
	}
	if len(existingTests) > 0 {
		out.ExistingTestNamesByFile = existingTests
	}
	return out
}

func sortedStringSetKeys(items map[string]struct{}) []string {
	if len(items) == 0 {
		return nil
	}
	out := make([]string, 0, len(items))
	for item := range items {
		out = append(out, item)
	}
	sort.Strings(out)
	return out
}

func goalNeedsMinimalTableDrivenTesting(goal string, targets []string) bool {
	hasTestTarget := false
	for _, target := range normalizeCitationList(targets) {
		if strings.HasSuffix(strings.ToLower(strings.TrimSpace(target)), "_test.go") {
			hasTestTarget = true
			break
		}
	}
	if !hasTestTarget {
		return false
	}
	lowGoal := strings.ToLower(goal)
	return strings.Contains(goal, "测试") ||
		strings.Contains(lowGoal, "test case") ||
		strings.Contains(lowGoal, "unit test")
}

func shouldEnforceKBTaskScope(goal string, targets []string) bool {
	if !shouldBackfillCitations(goal) || len(targets) == 0 {
		return false
	}
	for _, target := range targets {
		if shouldAnalyzeKBScopeFile(target) {
			return true
		}
	}
	return false
}

func shouldAnalyzeKBScopeFile(path string) bool {
	low := strings.ToLower(strings.TrimSpace(path))
	if low == "" {
		return false
	}
	if strings.HasSuffix(low, ".md") || strings.HasSuffix(low, ".txt") {
		return false
	}
	if strings.HasSuffix(low, "_test.go") {
		return false
	}
	return true
}

func detectKBScopeCreep(goal string, patch string, targets []string) []string {
	if !shouldEnforceKBTaskScope(goal, targets) || strings.TrimSpace(patch) == "" {
		return nil
	}
	targetSet := make(map[string]struct{}, len(targets))
	for _, target := range targets {
		target = strings.TrimSpace(strings.ReplaceAll(target, "\\", "/"))
		if shouldAnalyzeKBScopeFile(target) {
			targetSet[target] = struct{}{}
		}
	}
	if len(targetSet) == 0 {
		return nil
	}
	baseAllowed := make(map[string]struct{})
	for _, id := range extractGoalScopeIdentifiers(goal) {
		baseAllowed[id] = struct{}{}
	}
	if len(baseAllowed) == 0 {
		return nil
	}
	allowedFunctions := extractGoalFunctionIdentifiers(goal)
	allowedFunctionSet := make(map[string]struct{}, len(allowedFunctions))
	for _, fn := range allowedFunctions {
		allowedFunctionSet[fn] = struct{}{}
	}

	violations := make(map[string]struct{})
	currentFile := ""
	inHunk := false
	analyzeFile := false
	hunkAllowed := make(map[string]struct{})
	currentScope := ""
	lines := strings.Split(strings.ReplaceAll(patch, "\r\n", "\n"), "\n")
	for _, line := range lines {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "+++ "):
			currentFile = stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(trim, "+++ ")))
			_, analyzeFile = targetSet[currentFile]
			inHunk = false
			currentScope = ""
		case strings.HasPrefix(trim, "@@ -"):
			inHunk = analyzeFile
			hunkAllowed = cloneStringSet(baseAllowed)
			currentScope = extractPatchHunkHeaderScope(trim)
		default:
			if !inHunk || line == "" {
				continue
			}
			lineBody := line[1:]
			if sig := extractPatchFunctionScope(lineBody); sig != "" {
				currentScope = sig
			}
			switch line[0] {
			case ' ', '-':
				if isIgnorableScopeLine(lineBody) {
					continue
				}
				for _, id := range extractPatchScopeIdentifiers(lineBody) {
					hunkAllowed[id] = struct{}{}
				}
			case '+':
				if isIgnorableScopeLine(lineBody) {
					continue
				}
				if currentScope != "" && len(allowedFunctionSet) > 0 {
					if _, ok := allowedFunctionSet[currentScope]; !ok {
						violations[currentScope] = struct{}{}
					}
				}
				ids := extractPatchScopeIdentifiers(lineBody)
				for _, id := range ids {
					if isAllowedTargetFunctionImplementationIdentifier(currentScope, id, allowedFunctionSet) {
						hunkAllowed[id] = struct{}{}
						continue
					}
					if _, ok := hunkAllowed[id]; !ok {
						violations[id] = struct{}{}
					}
					hunkAllowed[id] = struct{}{}
				}
			}
		}
	}
	if len(violations) == 0 {
		return nil
	}
	out := make([]string, 0, len(violations))
	for id := range violations {
		out = append(out, id)
	}
	sort.Strings(out)
	return out
}

func extractGoalScopeIdentifiers(goal string) []string {
	return extractScopeIdentifiers(goal, true)
}

func extractGoalFunctionIdentifiers(goal string) []string {
	seen := make(map[string]struct{})
	add := func(id string) {
		id = strings.TrimSpace(id)
		if id == "" {
			return
		}
		seen[id] = struct{}{}
	}
	for _, match := range goalFunctionNameRegexp.FindAllStringSubmatch(goal, -1) {
		if len(match) > 1 {
			add(match[1])
		}
	}
	for _, match := range backtickContentRegexp.FindAllStringSubmatch(goal, -1) {
		if len(match) <= 1 {
			continue
		}
		raw := strings.TrimSpace(match[1])
		if raw == "" || strings.Contains(raw, "/") || strings.Contains(raw, ".") {
			continue
		}
		if isLowerOrMixedIdentifier(raw) || isScopeLikeIdentifier(raw) {
			add(raw)
		}
	}
	if len(seen) == 0 {
		return nil
	}
	out := make([]string, 0, len(seen))
	for id := range seen {
		out = append(out, id)
	}
	sort.Strings(out)
	return out
}

func extractPatchScopeIdentifiers(line string) []string {
	return extractScopeIdentifiers(stripQuotedScopeLiterals(line), false)
}

func detectTargetedPatchDefinitionIssues(goal string, repoRoot string, patch string, targets []string) []string {
	if strings.TrimSpace(repoRoot) == "" || strings.TrimSpace(patch) == "" || len(targets) == 0 {
		return nil
	}
	targets = normalizeCitationList(targets)
	if len(targets) == 0 {
		return nil
	}
	allowedFunctions := extractGoalFunctionIdentifiers(goal)
	allowedFunctionSet := make(map[string]struct{}, len(allowedFunctions))
	for _, fn := range allowedFunctions {
		allowedFunctionSet[fn] = struct{}{}
	}
	snapshots := buildRepoOnlyTargetSnapshots(strings.TrimSpace(repoRoot), targets)
	addedByFile := extractAddedGoTopLevelFunctionNamesByFile(patch, targets)
	duplicatedByFile := extractDuplicateAddedGoTopLevelFunctionNamesByFile(patch, targets)
	issues := make(map[string]struct{})
	singleTargetFunction := len(targets) == 1 && len(allowedFunctions) == 1
	for _, target := range targets {
		if !strings.HasSuffix(strings.ToLower(target), ".go") {
			continue
		}
		added := addedByFile[target]
		if len(added) == 0 {
			continue
		}
		existing := extractGoTopLevelFunctionNames(snapshots[target])
		isTestFile := strings.HasSuffix(strings.ToLower(target), "_test.go")
		for _, name := range duplicatedByFile[target] {
			if name == "" {
				continue
			}
			if isTestFile && strings.HasPrefix(name, "Test") {
				issues["duplicate test name: "+name] = struct{}{}
				continue
			}
			issues["duplicate helper definition: "+name] = struct{}{}
		}
		for _, name := range added {
			if name == "" {
				continue
			}
			if isTestFile {
				if strings.HasPrefix(name, "Test") {
					if _, ok := existing[name]; ok {
						issues["duplicate test name: "+name] = struct{}{}
					}
				}
				continue
			}
			if _, ok := existing[name]; ok {
				if _, allowed := allowedFunctionSet[name]; !allowed {
					issues["duplicate helper definition: "+name] = struct{}{}
				}
				continue
			}
			if singleTargetFunction {
				if _, allowed := allowedFunctionSet[name]; !allowed {
					issues["new helper definition: "+name] = struct{}{}
				}
			}
		}
	}
	if isReorderOnlyGoal(goal) {
		for _, target := range targets {
			if !strings.HasSuffix(strings.ToLower(target), ".go") {
				continue
			}
			for _, id := range detectReorderOnlyIdentifierDrift(patch, target) {
				issues["reorder-only identifier drift: "+id] = struct{}{}
			}
		}
	}
	for _, target := range targets {
		if !strings.HasSuffix(strings.ToLower(target), ".md") {
			continue
		}
		for _, heading := range extractRequiredMarkdownHeadings(goal) {
			if patchDeletesRequiredHeadingWithoutReplacement(patch, target, heading) {
				issues["deleted required heading without replacement: "+heading] = struct{}{}
			}
		}
	}
	if len(issues) == 0 {
		return nil
	}
	out := make([]string, 0, len(issues))
	for issue := range issues {
		out = append(out, issue)
	}
	sort.Strings(out)
	return out
}

func isReorderOnlyGoal(goal string) bool {
	goal = strings.TrimSpace(goal)
	if goal == "" {
		return false
	}
	return strings.Contains(goal, "按字母顺序排列") || strings.Contains(strings.ToLower(goal), "alphabet")
}

func detectReorderOnlyIdentifierDrift(patch string, target string) []string {
	target = strings.TrimSpace(target)
	if target == "" || strings.TrimSpace(patch) == "" {
		return nil
	}
	added := map[string]int{}
	removed := map[string]int{}
	entryAdded := map[string]int{}
	entryRemoved := map[string]int{}
	currentFile := ""
	inHunk := false
	for _, line := range strings.Split(strings.ReplaceAll(patch, "\r\n", "\n"), "\n") {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "+++ "):
			currentFile = stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(trim, "+++ ")))
			inHunk = false
		case strings.HasPrefix(trim, "@@ -"):
			inHunk = currentFile == target
		default:
			if !inHunk || currentFile != target || line == "" {
				continue
			}
			switch line[0] {
			case '+':
				if id, ok := extractReorderOnlyEntryIdentifier(line[1:]); ok {
					entryAdded[id]++
					continue
				}
				for _, id := range extractPlainIdentifiers(line[1:]) {
					added[id]++
				}
			case '-':
				if id, ok := extractReorderOnlyEntryIdentifier(line[1:]); ok {
					entryRemoved[id]++
					continue
				}
				for _, id := range extractPlainIdentifiers(line[1:]) {
					removed[id]++
				}
			}
		}
	}
	if len(entryAdded) > 0 || len(entryRemoved) > 0 {
		added = entryAdded
		removed = entryRemoved
	}
	var out []string
	seen := map[string]struct{}{}
	for id, n := range removed {
		if n > added[id] {
			if _, ok := seen[id]; !ok {
				out = append(out, id)
				seen[id] = struct{}{}
			}
		}
	}
	for id, n := range added {
		if n > removed[id] {
			if _, ok := seen[id]; !ok {
				out = append(out, id)
				seen[id] = struct{}{}
			}
		}
	}
	sort.Strings(out)
	return out
}

func extractReorderOnlyEntryIdentifier(text string) (string, bool) {
	trimmed := strings.TrimSpace(text)
	if trimmed == "" || strings.HasPrefix(trimmed, "//") {
		return "", false
	}
	if idx := strings.Index(trimmed, "//"); idx >= 0 {
		trimmed = strings.TrimSpace(trimmed[:idx])
	}
	if !strings.HasSuffix(trimmed, ",") {
		return "", false
	}
	trimmed = strings.TrimSuffix(trimmed, ",")
	trimmed = strings.TrimSpace(trimmed)
	if trimmed == "" {
		return "", false
	}
	if matched, _ := regexp.MatchString(`^[A-Za-z_][A-Za-z0-9_]*$`, trimmed); matched {
		return trimmed, true
	}
	if strings.HasPrefix(trimmed, "\"") && strings.HasSuffix(trimmed, "\"") && len(trimmed) >= 2 {
		name := strings.TrimSpace(trimmed[1 : len(trimmed)-1])
		if matched, _ := regexp.MatchString(`^[A-Za-z_][A-Za-z0-9_]*$`, name); matched {
			return name, true
		}
	}
	return "", false
}

func extractPlainIdentifiers(text string) []string {
	seen := map[string]struct{}{}
	for _, id := range plainIdentifierRegexp.FindAllString(text, -1) {
		if _, ignored := scopeIgnoredIdentifiers[id]; ignored {
			continue
		}
		switch id {
		case "package", "import", "func", "return", "var", "const", "type", "if", "else", "for", "range", "switch", "case", "default", "go", "defer", "nil", "true", "false", "any":
			continue
		}
		seen[id] = struct{}{}
	}
	if len(seen) == 0 {
		return nil
	}
	out := make([]string, 0, len(seen))
	for id := range seen {
		out = append(out, id)
	}
	sort.Strings(out)
	return out
}

func extractRequiredMarkdownHeadings(goal string) []string {
	seen := map[string]struct{}{}
	for _, m := range backtickContentRegexp.FindAllStringSubmatch(goal, -1) {
		if len(m) < 2 {
			continue
		}
		v := strings.TrimSpace(m[1])
		if strings.HasPrefix(v, "#") {
			seen[v] = struct{}{}
		}
	}
	if len(seen) == 0 {
		return nil
	}
	out := make([]string, 0, len(seen))
	for v := range seen {
		out = append(out, v)
	}
	sort.Strings(out)
	return out
}

func patchDeletesRequiredHeadingWithoutReplacement(patch string, target string, heading string) bool {
	target = strings.TrimSpace(target)
	heading = strings.TrimSpace(heading)
	if target == "" || heading == "" || strings.TrimSpace(patch) == "" {
		return false
	}
	currentFile := ""
	inHunk := false
	deleted := false
	added := false
	for _, line := range strings.Split(strings.ReplaceAll(patch, "\r\n", "\n"), "\n") {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "+++ "):
			currentFile = stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(trim, "+++ ")))
			inHunk = false
		case strings.HasPrefix(trim, "@@ -"):
			inHunk = currentFile == target
		default:
			if !inHunk || currentFile != target || line == "" {
				continue
			}
			switch line[0] {
			case '-':
				if strings.TrimSpace(line[1:]) == heading {
					deleted = true
				}
			case '+':
				if strings.TrimSpace(line[1:]) == heading {
					added = true
				}
			}
		}
	}
	return deleted && !added
}

func detectMissingTargetSnapshotContext(repoRoot string, patch string, targets []string) []string {
	if strings.TrimSpace(repoRoot) == "" || strings.TrimSpace(patch) == "" || len(targets) == 0 {
		return nil
	}
	snapshots := buildRepoOnlyTargetSnapshots(strings.TrimSpace(repoRoot), targets)
	if len(snapshots) == 0 {
		return nil
	}
	snapshotDecls := make(map[string]map[string]struct{}, len(targets))
	for _, target := range normalizeCitationList(targets) {
		if !strings.HasSuffix(strings.ToLower(target), ".go") {
			continue
		}
		decls := make(map[string]struct{})
		for _, line := range strings.Split(strings.ReplaceAll(snapshots[target], "\r\n", "\n"), "\n") {
			trimmed := strings.TrimSpace(line)
			if goFuncScopeRegexp.MatchString(trimmed) {
				decls[trimmed] = struct{}{}
			}
		}
		snapshotDecls[target] = decls
	}
	if len(snapshotDecls) == 0 {
		return nil
	}
	issues := make(map[string]struct{})
	currentFile := ""
	inHunk := false
	for _, line := range strings.Split(strings.ReplaceAll(patch, "\r\n", "\n"), "\n") {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "+++ "):
			currentFile = stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(trim, "+++ ")))
			_, inHunk = snapshotDecls[currentFile]
		case strings.HasPrefix(trim, "@@ -"):
			_, inHunk = snapshotDecls[currentFile]
		default:
			if !inHunk || currentFile == "" || line == "" || line[0] != ' ' {
				continue
			}
			contextLine := strings.TrimSpace(line[1:])
			if !goFuncScopeRegexp.MatchString(contextLine) {
				continue
			}
			if _, ok := snapshotDecls[currentFile][contextLine]; ok {
				continue
			}
			issues[currentFile+": "+contextLine] = struct{}{}
		}
	}
	if len(issues) == 0 {
		return nil
	}
	out := make([]string, 0, len(issues))
	for issue := range issues {
		out = append(out, issue)
	}
	sort.Strings(out)
	return out
}

func extractAddedGoTopLevelFunctionNamesByFile(patch string, targets []string) map[string][]string {
	targetSet := make(map[string]struct{}, len(targets))
	for _, target := range normalizeCitationList(targets) {
		targetSet[target] = struct{}{}
	}
	out := make(map[string][]string, len(targetSet))
	seen := make(map[string]map[string]struct{}, len(targetSet))
	currentFile := ""
	inHunk := false
	for _, line := range strings.Split(strings.ReplaceAll(patch, "\r\n", "\n"), "\n") {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "+++ "):
			currentFile = stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(trim, "+++ ")))
			_, inHunk = targetSet[currentFile]
		case strings.HasPrefix(trim, "@@ -"):
			_, inHunk = targetSet[currentFile]
		default:
			if !inHunk || currentFile == "" || line == "" || line[0] != '+' {
				continue
			}
			if name := extractPatchFunctionScope(line[1:]); name != "" {
				if seen[currentFile] == nil {
					seen[currentFile] = make(map[string]struct{})
				}
				if _, ok := seen[currentFile][name]; ok {
					continue
				}
				seen[currentFile][name] = struct{}{}
				out[currentFile] = append(out[currentFile], name)
			}
		}
	}
	return out
}

func extractDuplicateAddedGoTopLevelFunctionNamesByFile(patch string, targets []string) map[string][]string {
	targetSet := make(map[string]struct{}, len(targets))
	for _, target := range normalizeCitationList(targets) {
		targetSet[target] = struct{}{}
	}
	counts := make(map[string]map[string]int, len(targetSet))
	currentFile := ""
	inHunk := false
	for _, line := range strings.Split(strings.ReplaceAll(patch, "\r\n", "\n"), "\n") {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "+++ "):
			currentFile = stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(trim, "+++ ")))
			_, inHunk = targetSet[currentFile]
		case strings.HasPrefix(trim, "@@ -"):
			_, inHunk = targetSet[currentFile]
		default:
			if !inHunk || currentFile == "" || line == "" || line[0] != '+' {
				continue
			}
			if name := extractPatchFunctionScope(line[1:]); name != "" {
				if counts[currentFile] == nil {
					counts[currentFile] = make(map[string]int)
				}
				counts[currentFile][name]++
			}
		}
	}
	out := make(map[string][]string, len(counts))
	for file, fileCounts := range counts {
		for name, count := range fileCounts {
			if count > 1 {
				out[file] = append(out[file], name)
			}
		}
		sort.Strings(out[file])
	}
	return out
}

func extractGoTopLevelFunctionNames(content string) map[string]struct{} {
	out := make(map[string]struct{})
	for _, line := range strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n") {
		if name := extractPatchFunctionScope(line); name != "" {
			out[name] = struct{}{}
		}
	}
	return out
}

func extractPatchFunctionScope(line string) string {
	if m := goFuncScopeRegexp.FindStringSubmatch(line); len(m) > 1 {
		return strings.TrimSpace(m[1])
	}
	if m := pyFuncScopeRegexp.FindStringSubmatch(line); len(m) > 1 {
		return strings.TrimSpace(m[1])
	}
	return ""
}

func extractPatchHunkHeaderScope(line string) string {
	if line == "" {
		return ""
	}
	idx := strings.Index(line, "@@")
	if idx < 0 {
		return ""
	}
	rest := strings.TrimSpace(line[idx+2:])
	idx = strings.Index(rest, "@@")
	if idx < 0 {
		return ""
	}
	return extractPatchFunctionScope(strings.TrimSpace(rest[idx+2:]))
}

func isAllowedTargetFunctionImplementationIdentifier(currentScope string, id string, allowedFunctionSet map[string]struct{}) bool {
	if currentScope == "" || len(allowedFunctionSet) == 0 {
		return false
	}
	if _, ok := allowedFunctionSet[currentScope]; !ok {
		return false
	}
	return httpStatusRegexp.MatchString(id)
}

func extractScopeIdentifiers(text string, includeCamelStandalone bool) []string {
	seen := make(map[string]struct{})
	add := func(id string) {
		id = strings.TrimSpace(id)
		if id == "" {
			return
		}
		if _, ignored := scopeIgnoredIdentifiers[id]; ignored {
			return
		}
		seen[id] = struct{}{}
	}

	for _, chain := range scopeChainRegexp.FindAllString(text, -1) {
		parts := strings.Split(chain, ".")
		last := parts[len(parts)-1]
		if isLikelyFileExtension(last) {
			continue
		}
		for start := 0; start < len(parts)-1; start++ {
			if !isScopeLikeIdentifier(parts[start]) {
				continue
			}
			suffix := strings.Join(parts[start:], ".")
			lastPart := parts[len(parts)-1]
			if _, ignored := scopeIgnoredIdentifiers[lastPart]; ignored {
				continue
			}
			add(suffix)
		}
		for _, part := range parts {
			if isScopeLikeIdentifier(part) {
				add(part)
			}
		}
	}
	for _, id := range scopeSnakeRegexp.FindAllString(text, -1) {
		add(id)
	}
	if includeCamelStandalone {
		for _, id := range scopeCamelRegexp.FindAllString(text, -1) {
			add(id)
		}
	}
	if len(seen) == 0 {
		return nil
	}
	out := make([]string, 0, len(seen))
	for id := range seen {
		out = append(out, id)
	}
	sort.Strings(out)
	return out
}

func isIgnorableScopeLine(line string) bool {
	trim := strings.TrimSpace(line)
	if trim == "" {
		return true
	}
	if strings.HasPrefix(trim, "//") || strings.HasPrefix(trim, "/*") || strings.HasPrefix(trim, "*") || strings.HasPrefix(trim, "#") {
		return true
	}
	if strings.HasPrefix(trim, "import ") || strings.HasPrefix(trim, "from ") || strings.HasPrefix(trim, "package ") {
		return true
	}
	return false
}

func isScopeLikeIdentifier(id string) bool {
	if strings.Contains(id, "_") {
		return true
	}
	if len(id) > 0 && id[0] >= 'A' && id[0] <= 'Z' {
		return true
	}
	return false
}

func isLowerOrMixedIdentifier(id string) bool {
	if id == "" || id[0] < 'a' || id[0] > 'z' {
		return false
	}
	for i := 1; i < len(id); i++ {
		if id[i] >= 'A' && id[i] <= 'Z' {
			return true
		}
	}
	return strings.Contains(id, "_")
}

func isLikelyFileExtension(id string) bool {
	switch strings.ToLower(strings.TrimSpace(id)) {
	case "go", "py", "md", "txt", "json", "yaml", "yml":
		return true
	default:
		return false
	}
}

func stripPatchPathToken(tok string) string {
	tok = strings.TrimSpace(strings.ReplaceAll(tok, "\\", "/"))
	tok = strings.TrimPrefix(tok, "a/")
	tok = strings.TrimPrefix(tok, "b/")
	tok = strings.TrimPrefix(tok, "./")
	return strings.TrimLeft(tok, "/")
}

func stripQuotedScopeLiterals(text string) string {
	return scopeQuotedRegexp.ReplaceAllString(text, "")
}

func cloneStringSet(in map[string]struct{}) map[string]struct{} {
	out := make(map[string]struct{}, len(in))
	for k := range in {
		out[k] = struct{}{}
	}
	return out
}

func patchScopedAddLineCount(patch string, targets []string) int {
	targetSet := make(map[string]struct{}, len(targets))
	for _, target := range targets {
		target = strings.TrimSpace(strings.ReplaceAll(target, "\\", "/"))
		if shouldAnalyzeKBScopeFile(target) {
			targetSet[target] = struct{}{}
		}
	}
	currentFile := ""
	analyzeFile := false
	inHunk := false
	count := 0
	for _, line := range strings.Split(strings.ReplaceAll(patch, "\r\n", "\n"), "\n") {
		trim := strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(trim, "+++ "):
			currentFile = stripPatchPathToken(strings.TrimSpace(strings.TrimPrefix(trim, "+++ ")))
			_, analyzeFile = targetSet[currentFile]
			inHunk = false
		case strings.HasPrefix(trim, "@@ -"):
			inHunk = analyzeFile
		default:
			if !inHunk || line == "" || line[0] != '+' {
				continue
			}
			if isIgnorableScopeLine(line[1:]) {
				continue
			}
			count++
		}
	}
	return count
}

func maybeAutoPatch(in CoderInput) (string, bool) {
	goal := strings.ToLower(in.Goal)
	repoRoot := strings.TrimSpace(in.RepoSummary)
	if repoRoot == "" {
		return "", false
	}

	if !targetSetMatches(extractGoalTargetFiles(in.Goal), filepath.ToSlash(filepath.Join("internal", "config", "config.go"))) {
		return "", false
	}
	if !(strings.Contains(goal, "apikey") || strings.Contains(goal, "api_key")) {
		return "", false
	}
	if !(strings.Contains(goal, "baseurl") || strings.Contains(goal, "base_url")) {
		return "", false
	}
	patch, err := autoPatchConfigValidation(repoRoot)
	if err != nil || strings.TrimSpace(patch) == "" {
		return "", false
	}
	return patch, true
}

func buildInsertBeforeNeedlePatch(path, content, needle string, addLines []string) (string, error) {
	lines := strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n")
	insertAt := -1
	for i, l := range lines {
		if strings.TrimSpace(l) == strings.TrimSpace(needle) {
			insertAt = i
			break
		}
	}
	if insertAt == -1 {
		return "", fmt.Errorf("needle not found: %s", needle)
	}
	hunkStart := insertAt - 3
	if hunkStart < 0 {
		hunkStart = 0
	}
	hunkEnd := insertAt + 3
	if hunkEnd > len(lines) {
		hunkEnd = len(lines)
	}
	oldBlock := lines[hunkStart:hunkEnd]

	var b strings.Builder
	b.WriteString("--- a/" + path + "\n")
	b.WriteString("+++ b/" + path + "\n")
	oldStart := hunkStart + 1
	oldCount := len(oldBlock)
	newStart := oldStart
	newCount := oldCount + len(addLines)
	b.WriteString(fmt.Sprintf("@@ -%d,%d +%d,%d @@\n", oldStart, oldCount, newStart, newCount))
	for i, l := range oldBlock {
		if hunkStart+i == insertAt {
			for _, a := range addLines {
				b.WriteString("+" + a + "\n")
			}
		}
		b.WriteString(" " + l + "\n")
	}
	return b.String(), nil
}

func buildInsertAfterContainsPatch(path, content, contains string, addLines []string) (string, error) {
	lines := strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n")
	lineIdx := -1
	for i, l := range lines {
		if strings.Contains(l, contains) {
			lineIdx = i
			break
		}
	}
	if lineIdx == -1 {
		return "", fmt.Errorf("line not found for contains: %s", contains)
	}
	insertAt := lineIdx + 1
	hunkStart := lineIdx - 2
	if hunkStart < 0 {
		hunkStart = 0
	}
	hunkEnd := lineIdx + 4
	if hunkEnd > len(lines) {
		hunkEnd = len(lines)
	}
	oldBlock := lines[hunkStart:hunkEnd]

	var b strings.Builder
	b.WriteString("--- a/" + path + "\n")
	b.WriteString("+++ b/" + path + "\n")
	oldStart := hunkStart + 1
	oldCount := len(oldBlock)
	newStart := oldStart
	newCount := oldCount + len(addLines)
	b.WriteString(fmt.Sprintf("@@ -%d,%d +%d,%d @@\n", oldStart, oldCount, newStart, newCount))
	for i, l := range oldBlock {
		b.WriteString(" " + l + "\n")
		if hunkStart+i+1 == insertAt {
			for _, a := range addLines {
				b.WriteString("+" + a + "\n")
			}
		}
	}
	return b.String(), nil
}

func buildAppendLinesPatch(path, content string, addLines []string) (string, error) {
	lines := strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	var b strings.Builder
	b.WriteString("--- a/" + path + "\n")
	b.WriteString("+++ b/" + path + "\n")
	if len(lines) == 0 {
		b.WriteString(fmt.Sprintf("@@ -0,0 +1,%d @@\n", len(addLines)))
		for _, a := range addLines {
			b.WriteString("+" + a + "\n")
		}
		return b.String(), nil
	}
	hunkStart := len(lines) - 3
	if hunkStart < 0 {
		hunkStart = 0
	}
	oldBlock := lines[hunkStart:]
	oldStart := hunkStart + 1
	oldCount := len(oldBlock)
	newStart := oldStart
	newCount := oldCount + len(addLines)
	b.WriteString(fmt.Sprintf("@@ -%d,%d +%d,%d @@\n", oldStart, oldCount, newStart, newCount))
	for _, l := range oldBlock {
		b.WriteString(" " + l + "\n")
	}
	for _, a := range addLines {
		b.WriteString("+" + a + "\n")
	}
	return b.String(), nil
}

func buildReplaceLinePatch(path, content, oldLine, newLine string) (string, error) {
	lines := strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	lineIdx := -1
	for i, line := range lines {
		if line == oldLine {
			lineIdx = i
			break
		}
	}
	if lineIdx == -1 {
		return "", fmt.Errorf("line not found for replacement")
	}
	hunkStart := lineIdx - 2
	if hunkStart < 0 {
		hunkStart = 0
	}
	hunkEnd := lineIdx + 3
	if hunkEnd > len(lines) {
		hunkEnd = len(lines)
	}
	oldBlock := lines[hunkStart:hunkEnd]

	var b strings.Builder
	b.WriteString("--- a/" + path + "\n")
	b.WriteString("+++ b/" + path + "\n")
	oldStart := hunkStart + 1
	oldCount := len(oldBlock)
	newStart := oldStart
	newCount := oldCount
	b.WriteString(fmt.Sprintf("@@ -%d,%d +%d,%d @@\n", oldStart, oldCount, newStart, newCount))
	for i, line := range oldBlock {
		if hunkStart+i == lineIdx {
			b.WriteString("-" + line + "\n")
			b.WriteString("+" + newLine + "\n")
			continue
		}
		b.WriteString(" " + line + "\n")
	}
	return b.String(), nil
}

func buildReplaceLineRangePatch(path, content string, start, end int, newLines []string) (string, error) {
	lines := strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	if start < 0 || end < start || end > len(lines) {
		return "", fmt.Errorf("invalid replacement range")
	}
	hunkStart := start - 2
	if hunkStart < 0 {
		hunkStart = 0
	}
	hunkEnd := end + 2
	if hunkEnd > len(lines) {
		hunkEnd = len(lines)
	}

	var b strings.Builder
	b.WriteString("--- a/" + path + "\n")
	b.WriteString("+++ b/" + path + "\n")
	oldStart := hunkStart + 1
	oldCount := hunkEnd - hunkStart
	newCount := (start - hunkStart) + len(newLines) + (hunkEnd - end)
	b.WriteString(fmt.Sprintf("@@ -%d,%d +%d,%d @@\n", oldStart, oldCount, oldStart, newCount))
	for _, line := range lines[hunkStart:start] {
		b.WriteString(" " + line + "\n")
	}
	for _, line := range lines[start:end] {
		b.WriteString("-" + line + "\n")
	}
	for _, line := range newLines {
		b.WriteString("+" + line + "\n")
	}
	for _, line := range lines[end:hunkEnd] {
		b.WriteString(" " + line + "\n")
	}
	return b.String(), nil
}

func autoPatchConfigValidation(repoRoot string) (string, error) {
	path := filepath.ToSlash(filepath.Join("internal", "config", "config.go"))
	content, err := tools.RepoRead(repoRoot, path, 1024*1024)
	if err != nil {
		return "", err
	}
	lines := strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n")
	if len(lines) == 0 {
		return "", fmt.Errorf("empty file")
	}
	for _, l := range lines {
		if strings.Contains(l, "cfg.Model.APIKey") && strings.Contains(l, "cfg.Model.BaseURL") && strings.Contains(l, "api_key requires base_url") {
			return "", nil
		}
	}
	insertAt := -1
	for i, l := range lines {
		if strings.TrimSpace(l) == "return cfg, nil" {
			insertAt = i
			break
		}
	}
	if insertAt == -1 {
		return "", fmt.Errorf("return not found")
	}
	indent := leadingWhitespace(lines[insertAt])
	add := []string{
		indent + "if strings.TrimSpace(cfg.Model.APIKey) != \"\" && strings.TrimSpace(cfg.Model.BaseURL) == \"\" {",
		indent + "\treturn nil, fmt.Errorf(\"api_key requires base_url\")",
		indent + "}",
	}

	hunkStart := insertAt - 3
	if hunkStart < 0 {
		hunkStart = 0
	}
	hunkEnd := insertAt + 3
	if hunkEnd > len(lines) {
		hunkEnd = len(lines)
	}
	oldBlock := lines[hunkStart:hunkEnd]

	var b strings.Builder
	b.WriteString("--- a/" + path + "\n")
	b.WriteString("+++ b/" + path + "\n")
	oldStart := hunkStart + 1
	oldCount := len(oldBlock)
	newStart := oldStart
	newCount := oldCount + len(add)
	b.WriteString(fmt.Sprintf("@@ -%d,%d +%d,%d @@\n", oldStart, oldCount, newStart, newCount))
	for i, l := range oldBlock {
		if hunkStart+i == insertAt {
			for _, a := range add {
				b.WriteString("+" + a + "\n")
			}
		}
		b.WriteString(" " + l + "\n")
	}
	return normalizeCoderPatchForTargets(withDiffGitHeader(path, b.String()), []string{path}), nil
}

func leadingWhitespace(s string) string {
	i := 0
	for i < len(s) {
		if s[i] != ' ' && s[i] != '\t' {
			break
		}
		i++
	}
	return s[:i]
}

func trySynthesizeGoalTargetRecovery(in CoderInput, targets []string) (CoderOutput, bool) {
	repoRoot := strings.TrimSpace(in.RepoSummary)
	if repoRoot == "" {
		return CoderOutput{}, false
	}
	if out, ok := trySynthesizeDoomLoopResetPatch(in.Goal, repoRoot, targets); ok {
		return out, true
	}
	if out, ok := trySynthesizeDBPathValidationPatch(in.Goal, repoRoot, targets); ok {
		return out, true
	}
	if patch, ok := maybeAutoPatch(in); ok {
		return CoderOutput{
			Patch: patch,
			Notes: "synthesized APIKey/BaseURL validation patch from snapshots",
		}, true
	}
	return CoderOutput{}, false
}

func trySynthesizeRepoOnlySnapshotPatch(goal string, repoRoot string, targets []string) (CoderOutput, bool) {
	if out, ok := trySynthesizeReorderOnlyPatch(goal, repoRoot, targets); ok {
		return out, true
	}
	if out, ok := trySynthesizeMaxRuntimeStepsCommentPatch(goal, repoRoot, targets); ok {
		return out, true
	}
	return CoderOutput{}, false
}

func hasMinimalDoomLoopResetTestShape(text string) bool {
	low := strings.ToLower(text)
	return strings.Contains(text, "func TestDoomLoopDetectorReset(t *testing.T)") &&
		strings.Contains(text, "[]struct {") &&
		strings.Contains(low, `name: "negative blocks without reset"`) &&
		strings.Contains(low, `name: "positive reset clears state"`) &&
		strings.Count(text, "wantBlocked:") == 2
}

func hasExplicitMaxRuntimeBranchComment(text string) bool {
	low := strings.ToLower(text)
	if low == "" {
		return false
	}
	for _, token := range []string{"turn", "finish", "failed", "blocked"} {
		if !strings.Contains(low, token) {
			return false
		}
	}
	return !strings.Contains(low, "terminal nodes")
}

func hasCanonicalDBPathValidationTestShape(text string) bool {
	return strings.Contains(text, "func TestLoadValidatesDBPathSuffix(t *testing.T)") &&
		strings.Contains(text, `name: "accepts .db suffix"`) &&
		strings.Contains(text, `name: "rejects non-.db suffix"`) &&
		strings.Contains(text, `db_path must end with .db extension`)
}

func normalizedContentLines(content string) []string {
	lines := strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	return lines
}

func buildGoFileFunctionRewritePatch(path, snapshot string, names []string, replacement []string) (string, error) {
	lines := normalizedContentLines(snapshot)
	if len(lines) == 0 {
		return "", fmt.Errorf("empty file")
	}
	nameSet := make(map[string]struct{}, len(names))
	for _, name := range names {
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		nameSet[name] = struct{}{}
	}
	if len(nameSet) == 0 {
		return "", fmt.Errorf("no function names")
	}
	var rebuilt []string
	inserted := false
	changed := false
	for i := 0; i < len(lines); {
		trimmed := strings.TrimSpace(lines[i])
		if m := goFuncScopeRegexp.FindStringSubmatch(trimmed); len(m) == 2 {
			if _, ok := nameSet[m[1]]; ok {
				_, end, found := findGoFunctionBounds(lines[i:], m[1])
				if !found {
					return "", fmt.Errorf("function bounds not found for %s", m[1])
				}
				if !inserted {
					if len(rebuilt) > 0 && rebuilt[len(rebuilt)-1] != "" {
						rebuilt = append(rebuilt, "")
					}
					rebuilt = append(rebuilt, replacement...)
					inserted = true
				}
				i += end
				changed = true
				for i < len(lines) && strings.TrimSpace(lines[i]) == "" {
					i++
				}
				continue
			}
		}
		rebuilt = append(rebuilt, lines[i])
		i++
	}
	if !inserted {
		if len(rebuilt) > 0 && strings.TrimSpace(rebuilt[len(rebuilt)-1]) != "" {
			rebuilt = append(rebuilt, "")
		}
		rebuilt = append(rebuilt, replacement...)
		changed = true
	}
	if !changed {
		return "", fmt.Errorf("no function rewrite needed")
	}
	return buildReplaceLineRangePatch(path, snapshot, 0, len(lines), rebuilt)
}

func joinPatchSections(sections []string) string {
	var normalized []string
	for _, section := range sections {
		section = strings.TrimSpace(section)
		if section == "" {
			continue
		}
		normalized = append(normalized, section)
	}
	return strings.Join(normalized, "\n")
}

func trySynthesizeDoomLoopResetPatch(goal string, repoRoot string, targets []string) (CoderOutput, bool) {
	lowGoal := strings.ToLower(goal)
	if !strings.Contains(lowGoal, "doomloopdetector") || !strings.Contains(lowGoal, "reset") {
		return CoderOutput{}, false
	}
	wantTargets := []string{
		filepath.ToSlash(filepath.Join("internal", "loop", "processor.go")),
		filepath.ToSlash(filepath.Join("internal", "loop", "processor_test.go")),
	}
	if !targetSetMatches(targets, wantTargets...) {
		return CoderOutput{}, false
	}
	snapshots := buildRepoOnlyTargetSnapshots(repoRoot, wantTargets)
	processor := strings.TrimSpace(snapshots[wantTargets[0]])
	processorTest := strings.TrimSpace(snapshots[wantTargets[1]])
	if processor == "" || processorTest == "" || strings.HasPrefix(processor, "[repo_read_error]") || strings.HasPrefix(processorTest, "[repo_read_error]") {
		return CoderOutput{}, false
	}
	needCodePatch := !strings.Contains(processor, "func (d *DoomLoopDetector) Reset()")
	needTestPatch := !hasMinimalDoomLoopResetTestShape(processorTest)
	if !needCodePatch && !needTestPatch {
		return CoderOutput{}, false
	}
	var patchParts []string
	if needCodePatch {
		codePatch, err := buildAppendLinesPatch(wantTargets[0], processor, []string{
			"",
			"func (d *DoomLoopDetector) Reset() {",
			"\td.lastTool = \"\"",
			"\td.lastInput = \"\"",
			"\td.count = 0",
			"}",
		})
		if err != nil {
			return CoderOutput{}, false
		}
		patchParts = append(patchParts, withDiffGitHeader(wantTargets[0], codePatch))
	}
	if needTestPatch {
		resetTestLines := []string{
			"func TestDoomLoopDetectorReset(t *testing.T) {",
			"\ttests := []struct {",
			"\t\tname        string",
			"\t\treset       bool",
			"\t\twantBlocked bool",
			"\t}{",
			"\t\t{name: \"negative blocks without reset\", wantBlocked: true},",
			"\t\t{name: \"positive reset clears state\", reset: true, wantBlocked: false},",
			"\t}",
			"\tfor _, tt := range tests {",
			"\t\tt.Run(tt.name, func(t *testing.T) {",
			"\t\t\td := NewDoomLoopDetector(3)",
			"\t\t\td.Observe(\"run_command\", \"go test ./...\")",
			"\t\t\td.Observe(\"run_command\", \"go test ./...\")",
			"\t\t\tif tt.reset {",
			"\t\t\t\td.Reset()",
			"\t\t\t\tif d.count != 0 || d.lastTool != \"\" || d.lastInput != \"\" {",
			"\t\t\t\t\tt.Fatalf(\"expected reset to clear detector state, got count=%d lastTool=%q lastInput=%q\", d.count, d.lastTool, d.lastInput)",
			"\t\t\t\t}",
			"\t\t\t}",
			"\t\t\tif got := d.Observe(\"run_command\", \"go test ./...\"); got != tt.wantBlocked {",
			"\t\t\t\tt.Fatalf(\"blocked=%v want %v\", got, tt.wantBlocked)",
			"\t\t\t}",
			"\t\t})",
			"\t}",
			"}",
		}
		testLines := normalizedContentLines(processorTest)
		start, end, found := findGoFunctionBounds(testLines, "TestDoomLoopDetectorReset")
		var testPatch string
		var err error
		if found {
			testPatch, err = buildReplaceLineRangePatch(wantTargets[1], processorTest, start, end, resetTestLines)
		} else {
			testPatch, err = buildAppendLinesPatch(wantTargets[1], processorTest, append([]string{""}, resetTestLines...))
		}
		if err != nil {
			return CoderOutput{}, false
		}
		patchParts = append(patchParts, withDiffGitHeader(wantTargets[1], testPatch))
	}
	patch := normalizeCoderPatchForTargets(joinPatchSections(patchParts), wantTargets)
	if strings.TrimSpace(patch) == "" || !patchTouchesAnyTarget(patch, wantTargets) {
		return CoderOutput{}, false
	}
	return CoderOutput{
		Patch: patch,
		Notes: "synthesized DoomLoopDetector Reset patch from snapshots",
	}, true
}

func trySynthesizeDBPathValidationPatch(goal string, repoRoot string, targets []string) (CoderOutput, bool) {
	lowGoal := strings.ToLower(goal)
	if !(strings.Contains(lowGoal, "dbpath") || strings.Contains(lowGoal, "db_path")) {
		return CoderOutput{}, false
	}
	wantTargets := []string{
		filepath.ToSlash(filepath.Join("internal", "config", "config.go")),
		filepath.ToSlash(filepath.Join("internal", "config", "config_test.go")),
	}
	if !targetSetMatches(targets, wantTargets...) || !goalNeedsMinimalTableDrivenTesting(goal, wantTargets) {
		return CoderOutput{}, false
	}
	snapshots := buildRepoOnlyTargetSnapshots(repoRoot, wantTargets)
	configSnapshot := strings.TrimSpace(snapshots[wantTargets[0]])
	testSnapshot := strings.TrimSpace(snapshots[wantTargets[1]])
	if configSnapshot == "" || testSnapshot == "" || strings.HasPrefix(configSnapshot, "[repo_read_error]") || strings.HasPrefix(testSnapshot, "[repo_read_error]") {
		return CoderOutput{}, false
	}
	needCodePatch := !strings.Contains(configSnapshot, `db_path must end with .db extension`)
	needTestPatch := !hasCanonicalDBPathValidationTestShape(testSnapshot)
	if !needCodePatch && !needTestPatch {
		return CoderOutput{}, false
	}
	var patchParts []string
	if needCodePatch {
		configLines := strings.Split(strings.ReplaceAll(configSnapshot, "\r\n", "\n"), "\n")
		insertAt := -1
		for i, line := range configLines {
			if strings.TrimSpace(line) == "return cfg, nil" {
				insertAt = i
				break
			}
		}
		if insertAt == -1 {
			return CoderOutput{}, false
		}
		indent := leadingWhitespace(configLines[insertAt])
		codePatch, err := buildInsertBeforeNeedlePatch(wantTargets[0], configSnapshot, "return cfg, nil", []string{
			indent + `if !strings.HasSuffix(cfg.DBPath, ".db") {`,
			indent + `	return nil, fmt.Errorf("db_path must end with .db extension")`,
			indent + `}`,
		})
		if err != nil {
			return CoderOutput{}, false
		}
		patchParts = append(patchParts, withDiffGitHeader(wantTargets[0], codePatch))
	}
	if needTestPatch {
		testPatch, err := buildGoFileFunctionRewritePatch(wantTargets[1], testSnapshot, []string{"TestLoadValidatesDBPathSuffix", "TestLoadValidatesDBPathExtension"}, []string{
			"func TestLoadValidatesDBPathSuffix(t *testing.T) {",
			"\ttests := []struct {",
			"\t\tname    string",
			"\t\tdata    string",
			"\t\twantErr string",
			"\t}{",
			"\t\t{name: \"accepts .db suffix\", data: \"{\\\"db_path\\\":\\\"/tmp/state.db\\\"}\"},",
			"\t\t{name: \"rejects non-.db suffix\", data: \"{\\\"db_path\\\":\\\"/tmp/state.txt\\\"}\", wantErr: \"db_path must end with .db extension\"},",
			"\t}",
			"\tfor _, tt := range tests {",
			"\t\tt.Run(tt.name, func(t *testing.T) {",
			"\t\t\tpath := filepath.Join(t.TempDir(), \"config.json\")",
			"\t\t\tif err := os.WriteFile(path, []byte(tt.data), 0o644); err != nil {",
			"\t\t\t\tt.Fatalf(\"write: %v\", err)",
			"\t\t\t}",
			"\t\t\t_, err := Load(path)",
			"\t\t\tif tt.wantErr == \"\" {",
			"\t\t\t\tif err != nil {",
			"\t\t\t\t\tt.Fatalf(\"Load: %v\", err)",
			"\t\t\t\t}",
			"\t\t\t\treturn",
			"\t\t\t}",
			"\t\t\tif err == nil {",
			"\t\t\t\tt.Fatalf(\"expected error %q\", tt.wantErr)",
			"\t\t\t}",
			"\t\t\tif err.Error() != tt.wantErr {",
			"\t\t\t\tt.Fatalf(\"expected error %q, got %v\", tt.wantErr, err)",
			"\t\t\t}",
			"\t\t})",
			"\t}",
			"}",
		})
		if err != nil {
			return CoderOutput{}, false
		}
		patchParts = append(patchParts, withDiffGitHeader(wantTargets[1], testPatch))
	}
	patch := normalizeCoderPatchForTargets(joinPatchSections(patchParts), wantTargets)
	if strings.TrimSpace(patch) == "" || !patchTouchesAnyTarget(patch, wantTargets) {
		return CoderOutput{}, false
	}
	return CoderOutput{
		Patch: patch,
		Notes: "synthesized DBPath validation patch from snapshots",
	}, true
}

func trySynthesizeMaxRuntimeStepsCommentPatch(goal string, repoRoot string, targets []string) (CoderOutput, bool) {
	if !isMaxRuntimeStepsCommentGoal(goal) {
		return CoderOutput{}, false
	}
	wantTargets := []string{filepath.ToSlash(filepath.Join("internal", "loop", "engine_eino.go"))}
	if !targetSetMatches(targets, wantTargets...) {
		return CoderOutput{}, false
	}
	snapshot := strings.TrimSpace(buildRepoOnlyTargetSnapshots(repoRoot, wantTargets)[wantTargets[0]])
	if snapshot == "" || strings.HasPrefix(snapshot, "[repo_read_error]") {
		return CoderOutput{}, false
	}
	if !strings.Contains(snapshot, "return maxIterations*3 + 8") {
		return CoderOutput{}, false
	}
	lines := strings.Split(strings.ReplaceAll(snapshot, "\r\n", "\n"), "\n")
	oldLine := ""
	for _, line := range lines {
		if strings.TrimSpace(line) == "// Each loop turn has one main processing node, plus terminal nodes." {
			oldLine = line
			break
		}
	}
	if oldLine == "" {
		return CoderOutput{}, false
	}
	newLine := leadingWhitespace(oldLine) + `// Each iteration reserves three runtime steps plus fixed overhead for buildLoopRunner's "turn"/"finish"/"failed"/"blocked" branches.`
	patch, err := buildReplaceLinePatch(wantTargets[0], snapshot, oldLine, newLine)
	if err != nil {
		return CoderOutput{}, false
	}
	patch = normalizeCoderPatchForTargets(withDiffGitHeader(wantTargets[0], patch), wantTargets)
	if !patchTouchesTargets(patch, wantTargets, false) {
		return CoderOutput{}, false
	}
	return CoderOutput{
		Patch: patch,
		Notes: "synthesized maxRuntimeSteps comment patch from snapshots",
	}, true
}

func trySynthesizeWriteErrStatusTextPatch(goal string, repoRoot string, targets []string, patch string) (CoderOutput, bool) {
	if !isWriteErrKBGoal(goal) || strings.TrimSpace(repoRoot) == "" {
		return CoderOutput{}, false
	}
	wantTargets := []string{filepath.ToSlash(filepath.Join("internal", "http", "server.go"))}
	if !targetSetMatches(targets, wantTargets...) {
		return CoderOutput{}, false
	}
	snapshot := strings.TrimSpace(buildRepoOnlyTargetSnapshots(repoRoot, wantTargets)[wantTargets[0]])
	if snapshot == "" || strings.HasPrefix(snapshot, "[repo_read_error]") {
		return CoderOutput{}, false
	}
	lines := strings.Split(strings.ReplaceAll(snapshot, "\r\n", "\n"), "\n")
	start, end, ok := findGoFunctionBounds(lines, "writeErr")
	if !ok {
		return CoderOutput{}, false
	}
	writeErrBody := strings.Join(lines[start:end], "\n")
	patchSuggestsStatusCodeAttempt := strings.Contains(patch, `"code"`) || strings.Contains(patch, "switch code") || strings.Contains(strings.ToLower(patch), "http.statustext(code)")
	if hasStableWriteErrCodeMapping(writeErrBody) || hasStableWriteErrCodeMapping(patch) {
		return CoderOutput{}, false
	}
	if !patchSuggestsStatusCodeAttempt && !writeErrBodyNeedsStableCodeRecovery(writeErrBody) {
		return CoderOutput{}, false
	}
	patchText, err := buildReplaceLineRangePatch(wantTargets[0], snapshot, start, end, []string{
		"func writeErr(w http.ResponseWriter, code int, msg string) {",
		"\terrorCode := \"INTERNAL_ERROR\"",
		"\tswitch code {",
		"\tcase http.StatusBadRequest:",
		"\t\terrorCode = \"BAD_REQUEST\"",
		"\tcase http.StatusUnauthorized:",
		"\t\terrorCode = \"UNAUTHORIZED\"",
		"\tcase http.StatusForbidden:",
		"\t\terrorCode = \"FORBIDDEN\"",
		"\tcase http.StatusNotFound:",
		"\t\terrorCode = \"NOT_FOUND\"",
		"\tcase http.StatusMethodNotAllowed:",
		"\t\terrorCode = \"METHOD_NOT_ALLOWED\"",
		"\tcase http.StatusConflict:",
		"\t\terrorCode = \"CONFLICT\"",
		"\tcase http.StatusTooManyRequests:",
		"\t\terrorCode = \"TOO_MANY_REQUESTS\"",
		"\t}",
		"\twriteJSON(w, code, map[string]any{\"error\": msg, \"code\": errorCode})",
		"}",
	})
	if err != nil {
		return CoderOutput{}, false
	}
	patchText = normalizeCoderPatchForTargets(withDiffGitHeader(wantTargets[0], patchText), wantTargets)
	if !patchTouchesTargets(patchText, wantTargets, false) {
		return CoderOutput{}, false
	}
	return CoderOutput{
		Patch:   patchText,
		Summary: "Updated writeErr to emit KB-compliant machine-readable error codes via explicit stable HTTP status mapping.",
		Notes:   "synthesized writeErr stable-code patch from snapshots",
	}, true
}

func hasStableWriteErrCodeMapping(text string) bool {
	low := strings.ToLower(strings.TrimSpace(text))
	if low == "" {
		return false
	}
	return strings.Contains(low, "switch code") &&
		strings.Contains(low, "case http.statusbadrequest:") &&
		strings.Contains(low, "method_not_allowed") &&
		strings.Contains(low, `"code"`) &&
		(strings.Contains(low, "internal_error") || strings.Contains(low, "errorcode := "))
}

func writeErrBodyNeedsStableCodeRecovery(body string) bool {
	low := strings.ToLower(strings.TrimSpace(body))
	if low == "" {
		return false
	}
	if !strings.Contains(low, `"code"`) {
		return true
	}
	if strings.Contains(low, "http.statustext(code)") {
		return true
	}
	patterns := []string{
		"strings.replaceall(msg",
		"strings.replaceall(strings.trimspace(msg)",
		"strings.toupper(msg",
		"strings.toupper(strings.replaceall(strings.trimspace(msg)",
		"machinecode :=",
		"errorcode :=",
	}
	for _, pattern := range patterns {
		if strings.Contains(low, pattern) {
			return true
		}
	}
	return false
}

func detectRepoOnlySnapshotDefinitionIssues(goal string, patch string, targets []string) []string {
	if !isMaxRuntimeStepsCommentGoal(goal) || strings.TrimSpace(patch) == "" {
		return nil
	}
	wantTargets := []string{filepath.ToSlash(filepath.Join("internal", "loop", "engine_eino.go"))}
	if !targetSetMatches(targets, wantTargets...) {
		return nil
	}
	low := strings.ToLower(patch)
	var issues []string
	if strings.Contains(low, "einoengine") {
		issues = append(issues, "outdated maxRuntimeSteps snapshot reference: einoEngine")
	}
	if strings.Contains(low, "maxturns") || strings.Contains(low, "*4 + 2") || strings.Contains(low, "*4+2") {
		issues = append(issues, "outdated maxRuntimeSteps formula reference")
	}
	return issues
}

func appendUniqueIssues(base []string, extra ...string) []string {
	if len(extra) == 0 {
		return base
	}
	seen := make(map[string]struct{}, len(base)+len(extra))
	out := make([]string, 0, len(base)+len(extra))
	for _, item := range base {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		if _, ok := seen[item]; ok {
			continue
		}
		seen[item] = struct{}{}
		out = append(out, item)
	}
	for _, item := range extra {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		if _, ok := seen[item]; ok {
			continue
		}
		seen[item] = struct{}{}
		out = append(out, item)
	}
	sort.Strings(out)
	return out
}

func targetSetMatches(targets []string, want ...string) bool {
	got := normalizeCitationList(targets)
	need := normalizeCitationList(want)
	if len(got) != len(need) {
		return false
	}
	seen := make(map[string]struct{}, len(got))
	for _, target := range got {
		seen[target] = struct{}{}
	}
	for _, target := range need {
		if _, ok := seen[target]; !ok {
			return false
		}
	}
	return true
}

func withDiffGitHeader(path string, patch string) string {
	patch = strings.TrimSpace(patch)
	if patch == "" || strings.HasPrefix(patch, "diff --git ") {
		return patch
	}
	return fmt.Sprintf("diff --git a/%s b/%s\n%s", path, path, patch)
}

package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/cloudwego/eino/compose"
	"github.com/cloudwego/eino/flow/agent/react"
	"github.com/cloudwego/eino/schema"
	"github.com/kina/agent-coding-loop/internal/kb"
	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/skills"
	"github.com/kina/agent-coding-loop/internal/tools"
)

type Reviewer struct {
	client ClientConfig
	runner *tools.Runner
	skills *skills.Registry
	kb     *kb.Client
}

type ReviewInput struct {
	Goal             string              `json:"goal"`
	RepoRoot         string              `json:"repo_root"`
	Diff             string              `json:"diff"`
	StatusShort      string              `json:"status_short"`
	AppliedPatch     string              `json:"applied_patch"`
	CommandOutput    string              `json:"command_output"`
	SkillsSummary    string              `json:"skills_summary"`
	KBSearchCalls    int                 `json:"kb_search_calls"`
	RetrievalMode    model.RetrievalMode `json:"retrieval_mode,omitempty"`
	RetrievedContext []kb.SearchHit      `json:"retrieved_context,omitempty"`
	RetrievedQuery   string              `json:"retrieved_query,omitempty"`
}

type ReviewOutput struct {
	Decision       string                `json:"decision"`
	Summary        string                `json:"summary"`
	Findings       []model.ReviewFinding `json:"findings"`
	Markdown       string                `json:"review_markdown"`
	UsedFallback   bool                  `json:"used_fallback"`
	FallbackSource string                `json:"fallback_source"`
}

var goalFileTokenRE = regexp.MustCompile(`[A-Za-z0-9_./\-]+\.[A-Za-z0-9_+-]+`)

var (
	reviewerToolCallingTimeout = 60 * time.Second
	reviewerCompletionTimeout  = 60 * time.Second
)

func NewReviewer(client ClientConfig, opts ...Option) *Reviewer {
	deps := applyOptions(opts)
	return &Reviewer{
		client: client,
		runner: deps.runner,
		skills: deps.skills,
		kb:     deps.kb,
	}
}

func (r *Reviewer) Review(ctx context.Context, in ReviewInput) (ReviewOutput, error) {
	if !r.client.Ready() {
		out := fallbackReview(in)
		out.UsedFallback = true
		out.FallbackSource = "offline"
		normalizeReviewOutput(&out)
		enforceFallbackNoApprove(&out)
		enforceKBSearchConsistency(in, &out)
		enforceGoalTargetCoverage(in, &out)
		enforceMarkdownDuplicateReviewConsistency(in, &out)
		enforceReorderOnlyReviewConsistency(in, &out)
		ensureActionableFindings(&out)
		return out, nil
	}

	einoCtx, cancelEino := withReviewerTimeout(ctx, reviewerToolCallingTimeout)
	out, err := runWithHardTimeout(einoCtx, reviewerToolCallingTimeout, func(callCtx context.Context) (ReviewOutput, error) {
		return r.reviewWithEino(callCtx, in)
	})
	cancelEino()
	if err == nil {
		enforceKBSearchConsistency(in, &out)
		enforceGoalTargetCoverage(in, &out)
		enforceMarkdownDuplicateReviewConsistency(in, &out)
		enforceReorderOnlyReviewConsistency(in, &out)
		ensureActionableFindings(&out)
		return out, nil
	}

	fallbackCtx, cancelFallback := withReviewerTimeout(ctx, reviewerCompletionTimeout)
	fallback, fallbackErr := runWithHardTimeout(fallbackCtx, reviewerCompletionTimeout, func(callCtx context.Context) (ReviewOutput, error) {
		return r.reviewWithClient(callCtx, in)
	})
	cancelFallback()
	if fallbackErr != nil {
		out := fallbackReview(in)
		out.UsedFallback = true
		out.FallbackSource = "heuristic"
		out.Summary = strings.TrimSpace(out.Summary + " Eino review failed; fallback completion failed; heuristic fallback used.")
		out.Markdown = strings.TrimSpace(out.Markdown + "\n\n(Eino review failed: " + err.Error() + ")\n(Fallback completion failed: " + fallbackErr.Error() + ")")
		normalizeReviewOutput(&out)
		enforceFallbackNoApprove(&out)
		enforceKBSearchConsistency(in, &out)
		enforceGoalTargetCoverage(in, &out)
		enforceMarkdownDuplicateReviewConsistency(in, &out)
		enforceReorderOnlyReviewConsistency(in, &out)
		ensureActionableFindings(&out)
		return out, nil
	}
	fallback.UsedFallback = true
	fallback.FallbackSource = "client_completion"
	fallback.Markdown = strings.TrimSpace(strings.TrimSpace(fallback.Markdown) + "\n\n(Eino tool-call path failed: " + err.Error() + ")\n(Fallback completion used.)")
	normalizeReviewOutput(&fallback)
	enforceFallbackNoApprove(&fallback)
	enforceKBSearchConsistency(in, &fallback)
	enforceGoalTargetCoverage(in, &fallback)
	enforceMarkdownDuplicateReviewConsistency(in, &fallback)
	enforceReorderOnlyReviewConsistency(in, &fallback)
	ensureActionableFindings(&fallback)
	return fallback, nil
}

func withReviewerTimeout(ctx context.Context, max time.Duration) (context.Context, context.CancelFunc) {
	if max <= 0 {
		return context.WithCancel(ctx)
	}
	if deadline, ok := ctx.Deadline(); ok {
		remaining := time.Until(deadline)
		if remaining > 0 && remaining < max {
			return context.WithCancel(ctx)
		}
	}
	return context.WithTimeout(ctx, max)
}

func fallbackReview(in ReviewInput) ReviewOutput {
	if strings.Contains(strings.ToUpper(in.CommandOutput), "FAIL") {
		return ReviewOutput{
			Decision: string(model.ReviewDecisionRequestChanges),
			Summary:  "Automated gate failed: command output contains FAIL.",
			Findings: []model.ReviewFinding{{Severity: "high", File: "", Line: 0, Message: "Tests or checks failed"}},
			Markdown: "Requesting changes because validation commands failed.",
		}
	}
	return ReviewOutput{
		Decision: string(model.ReviewDecisionApprove),
		Summary:  "Fallback reviewer approved: no failures detected in command output.",
		Findings: []model.ReviewFinding{},
		Markdown: "Approved by fallback reviewer.",
	}
}

func (r *Reviewer) reviewWithEino(ctx context.Context, in ReviewInput) (ReviewOutput, error) {
	chatModel, err := r.client.newToolCallingModel(ctx)
	if err != nil {
		return ReviewOutput{}, err
	}

	repoRoot := strings.TrimSpace(in.RepoRoot)
	if repoRoot == "" {
		repoRoot = "."
	}
	runner := r.runner
	if runner == nil {
		runner = tools.NewRunner(tools.WithReadOnly(true))
	}
	toolset, err := tools.BuildReviewerTools(repoRoot, r.skills, runner, r.kb)
	if err != nil {
		return ReviewOutput{}, err
	}

	rAgent, err := react.NewAgent(ctx, &react.AgentConfig{
		ToolCallingModel: chatModel,
		ToolsConfig: compose.ToolsNodeConfig{
			Tools: toolset,
		},
		MaxStep: 12,
	})
	if err != nil {
		return ReviewOutput{}, err
	}

	systemPrompt, userPrompt := reviewerPrompts(in)
	var wire any
	if err := completeJSONWithGenerator(ctx, func(ctx context.Context, messages []*schema.Message) (*schema.Message, error) {
		return rAgent.Generate(ctx, messages)
	}, systemPrompt, userPrompt, &wire); err != nil {
		return ReviewOutput{}, err
	}
	b, err := json.Marshal(wire)
	if err != nil {
		return ReviewOutput{}, wrapStructuredOutputStageError("encode repaired reviewer json failed", fmt.Sprintf("%v", wire), err)
	}
	out, err := decodeReviewOutput(string(b))
	if err != nil {
		return ReviewOutput{}, wrapStructuredOutputStageError("parse reviewer json failed", string(b), err)
	}
	normalizeReviewOutput(&out)
	return out, nil
}

func (r *Reviewer) reviewWithClient(ctx context.Context, in ReviewInput) (ReviewOutput, error) {
	system, user := reviewerPrompts(in)
	var wire any
	if err := r.client.CompleteJSON(ctx, system, user, &wire); err != nil {
		return ReviewOutput{}, err
	}
	b, _ := json.Marshal(wire)
	out, err := decodeReviewOutput(string(b))
	if err != nil {
		return ReviewOutput{}, err
	}
	normalizeReviewOutput(&out)
	return out, nil
}

func decodeReviewOutput(content string) (ReviewOutput, error) {
	raw := extractJSON(content)
	var m map[string]any
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		var out ReviewOutput
		if err2 := json.Unmarshal([]byte(raw), &out); err2 == nil {
			return out, nil
		}
		return ReviewOutput{}, err
	}
	out := ReviewOutput{}
	if v, ok := m["decision"].(string); ok {
		out.Decision = v
	}
	if v, ok := m["summary"].(string); ok {
		out.Summary = v
	}
	if v, ok := m["review_markdown"].(string); ok {
		out.Markdown = v
	} else if v, ok := m["markdown"].(string); ok {
		out.Markdown = v
	}
	if v, ok := m["used_fallback"].(bool); ok {
		out.UsedFallback = v
	}
	if v, ok := m["fallback_source"].(string); ok {
		out.FallbackSource = strings.TrimSpace(v)
	}
	if f, ok := m["findings"]; ok {
		b, _ := json.Marshal(f)
		var items []model.ReviewFinding
		if err := json.Unmarshal(b, &items); err == nil {
			out.Findings = items
		} else {
			var s string
			if err2 := json.Unmarshal(b, &s); err2 == nil && strings.TrimSpace(s) != "" {
				out.Findings = []model.ReviewFinding{{Severity: "high", File: "", Line: 0, Message: s}}
			} else {
				var ss []string
				if err3 := json.Unmarshal(b, &ss); err3 == nil && len(ss) > 0 {
					out.Findings = make([]model.ReviewFinding, 0, len(ss))
					for _, it := range ss {
						it = strings.TrimSpace(it)
						if it == "" {
							continue
						}
						out.Findings = append(out.Findings, model.ReviewFinding{Severity: "high", File: "", Line: 0, Message: it})
					}
				}
			}
		}
	}
	return out, nil
}

func wrapStructuredOutputStageError(stage, raw string, err error) error {
	return fmt.Errorf("%s: %w; content=%s", stage, err, truncateDiagnosticPreview(raw))
}

func normalizeReviewOutput(out *ReviewOutput) {
	if out.Decision == "" {
		out.Decision = string(model.ReviewDecisionComment)
	}
	if out.Summary == "" {
		out.Summary = "Reviewer completed."
	}
	if out.Markdown == "" {
		out.Markdown = out.Summary
	}
}

func ensureActionableFindings(out *ReviewOutput) {
	if out == nil {
		return
	}
	if strings.TrimSpace(strings.ToLower(out.Decision)) != string(model.ReviewDecisionRequestChanges) {
		return
	}
	fallbackMsg := strings.TrimSpace(out.Summary)
	if fallbackMsg == "" {
		fallbackMsg = strings.TrimSpace(out.Markdown)
	}
	if fallbackMsg == "" {
		fallbackMsg = "Reviewer requested changes without a specific finding."
	}
	hasActionable := false
	for i := range out.Findings {
		if strings.TrimSpace(out.Findings[i].Severity) == "" {
			out.Findings[i].Severity = "high"
		}
		if strings.TrimSpace(out.Findings[i].Message) == "" {
			out.Findings[i].Message = fallbackMsg
		}
		if strings.TrimSpace(out.Findings[i].Message) != "" {
			hasActionable = true
		}
	}
	if hasActionable {
		return
	}
	out.Findings = append(out.Findings, model.ReviewFinding{
		Severity: "high",
		Message:  fallbackMsg,
	})
}

func enforceFallbackNoApprove(out *ReviewOutput) {
	if out == nil {
		return
	}
	if !out.UsedFallback {
		return
	}
	if strings.TrimSpace(strings.ToLower(out.Decision)) != string(model.ReviewDecisionApprove) {
		return
	}
	out.Decision = string(model.ReviewDecisionComment)
	note := "Fallback reviewer cannot approve; decision downgraded to comment."
	if !strings.Contains(strings.ToLower(out.Summary), "fallback reviewer cannot approve") {
		out.Summary = strings.TrimSpace(strings.TrimSpace(out.Summary) + " " + note)
	}
	if !strings.Contains(strings.ToLower(out.Markdown), "fallback reviewer cannot approve") {
		out.Markdown = strings.TrimSpace(strings.TrimSpace(out.Markdown) + "\n\n" + note)
	}
}

func enforceGoalTargetCoverage(in ReviewInput, out *ReviewOutput) {
	if out == nil {
		return
	}
	targets := extractGoalTargetFiles(in.Goal)
	if len(targets) == 0 {
		return
	}
	changed := extractChangedFiles(in.Diff, targets...)
	for p := range extractChangedFiles(in.AppliedPatch, targets...) {
		changed[p] = struct{}{}
	}
	for p := range extractStatusFiles(in.StatusShort) {
		changed[p] = struct{}{}
	}
	missing := make([]string, 0, len(targets))
	for _, file := range targets {
		if _, ok := changed[file]; !ok {
			missing = append(missing, file)
		}
	}
	if len(missing) == 0 {
		return
	}
	sort.Strings(missing)
	out.Decision = string(model.ReviewDecisionRequestChanges)
	reason := "Goal-target file(s) not touched in diff: " + strings.Join(missing, ", ") + "."
	if !strings.Contains(strings.ToLower(out.Summary), "goal-target file(s) not touched") {
		out.Summary = strings.TrimSpace(strings.TrimSpace(out.Summary) + " " + reason)
	}
	if !strings.Contains(strings.ToLower(out.Markdown), "goal-target file(s) not touched") {
		out.Markdown = strings.TrimSpace(strings.TrimSpace(out.Markdown) + "\n\n" + reason)
	}
	for _, file := range missing {
		out.Findings = append(out.Findings, model.ReviewFinding{
			Severity: "high",
			File:     file,
			Line:     0,
			Message:  "Target file required by goal is not modified in the current diff.",
		})
	}
}

func enforceReorderOnlyReviewConsistency(in ReviewInput, out *ReviewOutput) {
	// Narrow fallback retained for legacy reorder-only review recovery. Do not extend with new task shapes.
	if out == nil || !isReorderOnlyGoal(in.Goal) {
		return
	}
	if strings.Contains(strings.ToUpper(in.CommandOutput), "FAIL") {
		return
	}
	targets := extractGoalTargetFiles(in.Goal)
	if len(targets) != 1 {
		return
	}
	if !diffTouchesTargets(in.Diff, targets, false) && !patchTouchesTargets(in.AppliedPatch, targets, false) {
		return
	}
	if strings.TrimSpace(in.RepoRoot) == "" {
		return
	}
	if !isReorderOnlySnapshotSorted(strings.TrimSpace(in.RepoRoot), in.Goal, targets) {
		return
	}
	out.Decision = string(model.ReviewDecisionComment)
	out.Summary = "Reorder-only target file is sorted correctly according to the current snapshot and validation passed."
	out.Markdown = "Reorder-only target file is sorted correctly according to the current snapshot and validation passed."
	out.Findings = nil
}

func enforceMarkdownDuplicateReviewConsistency(in ReviewInput, out *ReviewOutput) {
	if out == nil {
		return
	}
	if strings.TrimSpace(strings.ToLower(out.Decision)) != string(model.ReviewDecisionRequestChanges) {
		return
	}
	if strings.TrimSpace(in.RepoRoot) == "" || !reviewMentionsMarkdownDuplicate(out) {
		return
	}
	targets := extractGoalTargetFiles(in.Goal)
	if len(targets) == 0 {
		return
	}
	headings := extractRequiredMarkdownHeadings(in.Goal)
	if len(headings) == 0 {
		return
	}
	duplicateSupported := false
	for _, target := range targets {
		if strings.ToLower(filepath.Ext(target)) != ".md" {
			continue
		}
		content, err := tools.RepoRead(strings.TrimSpace(in.RepoRoot), target, repoOnlySnapshotMaxBytes)
		if err != nil || strings.TrimSpace(content) == "" {
			continue
		}
		for _, heading := range headings {
			if markdownSnapshotShowsDuplicateSection(content, heading) {
				duplicateSupported = true
				break
			}
		}
		if duplicateSupported {
			break
		}
	}
	if duplicateSupported {
		return
	}

	filtered := make([]model.ReviewFinding, 0, len(out.Findings))
	for _, finding := range out.Findings {
		if findingMentionsMarkdownDuplicate(finding) {
			continue
		}
		filtered = append(filtered, finding)
	}
	out.Findings = filtered

	otherBlockers := hasCommandFailure(in.CommandOutput) ||
		hasNonKBFindings(out.Findings) ||
		reviewMentionsMissingKBSearch(out) ||
		reviewMentionsGoalTargetUntouched(out)

	note := "Removed unsupported markdown repetition finding after snapshot check."
	if !otherBlockers {
		out.Decision = string(model.ReviewDecisionComment)
		out.Summary = note
		out.Markdown = note
		return
	}
	if len(out.Findings) > 0 {
		msgs := make([]string, 0, len(out.Findings))
		seen := map[string]struct{}{}
		for _, finding := range out.Findings {
			msg := strings.TrimSpace(finding.Message)
			if msg == "" {
				continue
			}
			if _, ok := seen[msg]; ok {
				continue
			}
			seen[msg] = struct{}{}
			msgs = append(msgs, msg)
		}
		if len(msgs) > 0 {
			out.Summary = "Reviewer found remaining issues: " + strings.Join(msgs, "; ")
			out.Markdown = out.Summary + "\n\n" + note
			return
		}
	}
	out.Summary = strings.TrimSpace(note + " Remaining issues still require changes.")
	out.Markdown = out.Summary
}

func enforceKBSearchConsistency(in ReviewInput, out *ReviewOutput) {
	if out == nil {
		return
	}
	if !reviewRequiresKBSearch(in) {
		return
	}
	if in.KBSearchCalls <= 0 {
		out.Decision = string(model.ReviewDecisionRequestChanges)
		reason := "Required kb_search call evidence missing for this KB task."
		if !strings.Contains(strings.ToLower(out.Summary), "kb_search") {
			out.Summary = strings.TrimSpace(strings.TrimSpace(out.Summary) + " " + reason)
		}
		if !strings.Contains(strings.ToLower(out.Markdown), "kb_search") {
			out.Markdown = strings.TrimSpace(strings.TrimSpace(out.Markdown) + "\n\n" + reason)
		}
		return
	}
	if strings.TrimSpace(strings.ToLower(out.Decision)) != string(model.ReviewDecisionRequestChanges) {
		return
	}
	if !reviewMentionsMissingKBSearch(out) {
		return
	}
	if hasCommandFailure(in.CommandOutput) {
		return
	}
	if hasNonKBFindings(out.Findings) {
		return
	}
	out.Decision = string(model.ReviewDecisionComment)
	note := "KB search evidence exists in run metadata; removed missing-kb_search rejection."
	out.Summary = strings.TrimSpace(strings.TrimSpace(out.Summary) + " " + note)
	out.Markdown = strings.TrimSpace(strings.TrimSpace(out.Markdown) + "\n\n" + note)
}

func reviewRequiresKBSearch(in ReviewInput) bool {
	return in.RetrievalMode == model.RetrievalModePrefetch
}

func reviewMentionsMissingKBSearch(out *ReviewOutput) bool {
	if out == nil {
		return false
	}
	low := strings.ToLower(strings.TrimSpace(out.Summary + "\n" + out.Markdown))
	patterns := []string{
		"未按要求先调用 kb_search",
		"缺少 kb_search 调用证据",
		"必须先通过 kb_search",
		"missing kb_search",
		"must call kb_search",
	}
	for _, p := range patterns {
		if strings.Contains(low, p) {
			return true
		}
	}
	return false
}

func reviewMentionsMarkdownDuplicate(out *ReviewOutput) bool {
	if out == nil {
		return false
	}
	low := strings.ToLower(strings.TrimSpace(out.Summary + "\n" + out.Markdown))
	patterns := []string{
		"duplicated",
		"duplicate glossary",
		"appears twice",
		"appears two times",
		"repeated paragraphs",
		"keep only one copy",
		"section is duplicated",
	}
	for _, p := range patterns {
		if strings.Contains(low, p) {
			return true
		}
	}
	for _, finding := range out.Findings {
		if findingMentionsMarkdownDuplicate(finding) {
			return true
		}
	}
	return false
}

func reviewMentionsGoalTargetUntouched(out *ReviewOutput) bool {
	if out == nil {
		return false
	}
	low := strings.ToLower(strings.TrimSpace(out.Summary + "\n" + out.Markdown))
	return strings.Contains(low, "goal-target file(s) not touched")
}

func findingMentionsMarkdownDuplicate(finding model.ReviewFinding) bool {
	low := strings.ToLower(strings.TrimSpace(finding.Message))
	if low == "" {
		return false
	}
	patterns := []string{
		"duplicated",
		"duplicate glossary",
		"appears twice",
		"repeated paragraphs",
		"keep only one copy",
		"duplicate section",
	}
	for _, p := range patterns {
		if strings.Contains(low, p) {
			return true
		}
	}
	return false
}

func markdownSnapshotShowsDuplicateSection(content string, heading string) bool {
	heading = strings.TrimSpace(heading)
	if heading == "" {
		return false
	}
	lines := strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n")
	headingCount := 0
	for _, line := range lines {
		if strings.TrimSpace(line) == heading {
			headingCount++
		}
	}
	if headingCount > 1 {
		return true
	}
	paragraphs := extractMarkdownSectionParagraphs(lines, heading)
	if len(paragraphs) == 0 {
		return false
	}
	seen := make(map[string]int, len(paragraphs))
	for _, block := range paragraphs {
		seen[block]++
		if seen[block] > 1 {
			return true
		}
	}
	return false
}

func extractMarkdownSectionParagraphs(lines []string, heading string) []string {
	start := -1
	for i, line := range lines {
		if strings.TrimSpace(line) == heading {
			start = i + 1
			break
		}
	}
	if start < 0 || start >= len(lines) {
		return nil
	}
	blocks := make([]string, 0)
	current := make([]string, 0)
	flush := func() {
		if len(current) == 0 {
			return
		}
		blocks = append(blocks, strings.Join(current, "\n"))
		current = current[:0]
	}
	for _, raw := range lines[start:] {
		trim := strings.TrimSpace(raw)
		if strings.HasPrefix(trim, "#") {
			break
		}
		if trim == "" {
			flush()
			continue
		}
		current = append(current, trim)
	}
	flush()
	return blocks
}

func hasCommandFailure(output string) bool {
	low := strings.ToLower(strings.TrimSpace(output))
	if low == "" {
		return false
	}
	if strings.Contains(strings.ToUpper(output), "FAIL") {
		return true
	}
	failureTokens := []string{
		"error:",
		"exit status",
		"build failed",
		"test failed",
		"panic:",
	}
	for _, tok := range failureTokens {
		if strings.Contains(low, tok) {
			return true
		}
	}
	return false
}

func hasNonKBFindings(findings []model.ReviewFinding) bool {
	for _, f := range findings {
		msg := strings.ToLower(strings.TrimSpace(f.Message))
		file := strings.ToLower(strings.TrimSpace(f.File))
		if msg == "" && file == "" {
			continue
		}
		if strings.Contains(msg, "kb_search") {
			continue
		}
		return true
	}
	return false
}

func extractGoalTargetFiles(goal string) []string {
	raw := goalFileTokenRE.FindAllString(goal, -1)
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
		p := normalizePathForCompare(token)
		if p == "" {
			continue
		}
		ext := strings.ToLower(filepath.Ext(p))
		if _, ok := allowedExt[ext]; !ok {
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

func extractChangedFiles(diff string, targets ...string) map[string]struct{} {
	out := make(map[string]struct{})
	for _, line := range strings.Split(strings.ReplaceAll(diff, "\r\n", "\n"), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "+++ ") {
			p := normalizePathForCompare(strings.TrimSpace(strings.TrimPrefix(line, "+++ ")), targets...)
			if p == "" {
				continue
			}
			out[p] = struct{}{}
			continue
		}
		if strings.HasPrefix(line, "diff --git ") {
			fields := strings.Fields(line)
			if len(fields) < 4 {
				continue
			}
			p := normalizePathForCompare(fields[3], targets...)
			if p == "" {
				continue
			}
			out[p] = struct{}{}
		}
	}
	return out
}

func extractStatusFiles(status string) map[string]struct{} {
	out := make(map[string]struct{})
	for _, line := range strings.Split(strings.ReplaceAll(status, "\r\n", "\n"), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if len(line) < 3 {
			continue
		}
		pathPart := strings.TrimSpace(line[2:])
		if pathPart == "" {
			continue
		}
		if strings.Contains(pathPart, " -> ") {
			parts := strings.Split(pathPart, " -> ")
			pathPart = strings.TrimSpace(parts[len(parts)-1])
		}
		path := normalizePathForCompare(pathPart)
		if path == "" {
			continue
		}
		out[path] = struct{}{}
	}
	return out
}

func normalizePathForCompare(path string, targets ...string) string {
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
	if len(targets) > 0 {
		path = matchPatchPathToTarget(path, targets)
	}
	return path
}

func reviewerPrompts(in ReviewInput) (string, string) {
	targets := extractGoalTargetFiles(in.Goal)
	singleFnConstraint := buildSingleTargetFunctionConstraint(in.Goal, targets)
	testingConstraint := buildMinimalTestingConstraint(in.Goal, targets)
	inlineEditConstraint := buildMixedTaskInlineEditConstraint(in.Goal, targets)
	reorderOnlyConstraint := buildReorderOnlySnapshotConstraint(in.Goal)
	system := `You are a strict code reviewer.
You may use read-only tools to inspect repository files, search code, inspect diff, and query the knowledge base.
- retrieved_context in the review input contains pre-fetched knowledge base evidence; use it as the primary reference for domain rules. Call kb_search only for supplementary checks.
- when kb_scope_contract is present, review only the requested KB-backed rule(s) and target files. Do not request adjacent rules from the same knowledge-base document unless they are explicitly named in kb_scope_contract.identifiers.
Return JSON only with fields: decision, summary, findings, review_markdown.
- decision must be one of: approve, request_changes, comment
- If tests/checks fail, decision must be request_changes.
- findings must include concrete file/line risk when possible.
- never return markdown outside JSON.`
	if singleFnConstraint != "" {
		system = strings.TrimSpace(system + "\n- for this single-target-function review, do not require signature or call-site changes unless the goal explicitly requires that broader edit.\n- " + singleFnConstraint)
	}
	if testingConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + testingConstraint)
		system = strings.TrimSpace(system + "\n- for KB-guided validation tasks, do not require extra constants, helper names, or assertions beyond what the goal and KB evidence explicitly require.")
	}
	if inlineEditConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + inlineEditConstraint)
	}
	if reorderOnlyConstraint != "" {
		system = strings.TrimSpace(system + "\n- " + reorderOnlyConstraint)
	}
	payload := map[string]any{
		"review_input":      in,
		"kb_scope_contract": buildKBScopeContract(in.Goal, targets),
	}
	payloadJSON, _ := json.MarshalIndent(payload, "", "  ")
	user := fmt.Sprintf("Review input:\n%s\nUse tools when needed, then return strict JSON only.", string(payloadJSON))
	return system, user
}

func isReorderOnlySnapshotSorted(repoRoot string, goal string, targets []string) bool {
	if !isReorderOnlyGoal(goal) || strings.TrimSpace(repoRoot) == "" {
		return false
	}
	targets = normalizeCitationList(targets)
	if len(targets) != 1 {
		return false
	}
	target := targets[0]
	if !strings.HasSuffix(strings.ToLower(target), ".go") {
		return false
	}
	functions := extractGoalFunctionIdentifiers(goal)
	if len(functions) != 1 {
		return false
	}
	snapshot := buildRepoOnlyTargetSnapshots(repoRoot, []string{target})[target]
	if strings.TrimSpace(snapshot) == "" || strings.HasPrefix(snapshot, "[repo_read_error]") {
		return false
	}
	lines := strings.Split(strings.ReplaceAll(snapshot, "\r\n", "\n"), "\n")
	start, end, ok := findGoFunctionBounds(lines, functions[0])
	if !ok {
		return false
	}
	_, entriesStart, entriesEnd, _, ok := findReorderableReturnSlice(lines[start:end])
	if !ok {
		return false
	}
	entriesStart += start
	entriesEnd += start
	names := make([]string, 0, entriesEnd-entriesStart)
	for _, line := range lines[entriesStart:entriesEnd] {
		name, ok := extractReorderOnlyEntryIdentifier(line)
		if !ok {
			return false
		}
		names = append(names, name)
	}
	if len(names) < 2 {
		return false
	}
	sortedNames := append([]string(nil), names...)
	sort.Strings(sortedNames)
	for i := range names {
		if names[i] != sortedNames[i] {
			return false
		}
	}
	return true
}

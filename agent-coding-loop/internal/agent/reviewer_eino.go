package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/cloudwego/eino-ext/components/model/openai"
	"github.com/cloudwego/eino/compose"
	"github.com/cloudwego/eino/flow/agent/react"
	"github.com/cloudwego/eino/schema"
	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/skills"
	"github.com/kina/agent-coding-loop/internal/tools"
)

type Reviewer struct {
	client ClientConfig
	runner *tools.Runner
	skills *skills.Registry
}

type ReviewInput struct {
	Goal          string `json:"goal"`
	RepoRoot      string `json:"repo_root"`
	Diff          string `json:"diff"`
	CommandOutput string `json:"command_output"`
	SkillsSummary string `json:"skills_summary"`
}

type ReviewOutput struct {
	Decision string                `json:"decision"`
	Summary  string                `json:"summary"`
	Findings []model.ReviewFinding `json:"findings"`
	Markdown string                `json:"review_markdown"`
}

func NewReviewer(client ClientConfig, opts ...Option) *Reviewer {
	deps := applyOptions(opts)
	return &Reviewer{
		client: client,
		runner: deps.runner,
		skills: deps.skills,
	}
}

func (r *Reviewer) Review(ctx context.Context, in ReviewInput) (ReviewOutput, error) {
	if !r.client.Ready() {
		return fallbackReview(in), nil
	}

	out, err := r.reviewWithEino(ctx, in)
	if err == nil {
		return out, nil
	}

	fallback, fallbackErr := r.reviewWithClient(ctx, in)
	if fallbackErr != nil {
		return ReviewOutput{}, fmt.Errorf("eino review failed: %w; fallback failed: %v", err, fallbackErr)
	}
	fallback.Markdown = strings.TrimSpace(strings.TrimSpace(fallback.Markdown) + "\n\n(Eino tool-call path failed, fallback completion used.)")
	return fallback, nil
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
	chatModel, err := openai.NewChatModel(ctx, &openai.ChatModelConfig{
		BaseURL: r.client.BaseURL,
		Model:   r.client.Model,
		APIKey:  r.client.APIKey,
	})
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
	toolset, err := tools.BuildReviewerTools(repoRoot, r.skills, runner)
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
	msg, err := rAgent.Generate(ctx, []*schema.Message{
		schema.SystemMessage(systemPrompt),
		schema.UserMessage(userPrompt),
	})
	if err != nil {
		return ReviewOutput{}, err
	}
	var out ReviewOutput
	content := ""
	if msg != nil {
		content = msg.Content
	}
	if err := json.Unmarshal([]byte(extractJSON(content)), &out); err != nil {
		return ReviewOutput{}, fmt.Errorf("parse reviewer json failed: %w; content=%s", err, content)
	}
	normalizeReviewOutput(&out)
	return out, nil
}

func (r *Reviewer) reviewWithClient(ctx context.Context, in ReviewInput) (ReviewOutput, error) {
	system, user := reviewerPrompts(in)
	var out ReviewOutput
	if err := r.client.CompleteJSON(ctx, system, user, &out); err != nil {
		return ReviewOutput{}, err
	}
	normalizeReviewOutput(&out)
	return out, nil
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

func reviewerPrompts(in ReviewInput) (string, string) {
	system := `You are a strict code reviewer.
You may use read-only tools to inspect repository files, search code, inspect diff and read skills.
Return JSON only with fields: decision, summary, findings, review_markdown.
- decision must be one of: approve, request_changes, comment
- If tests/checks fail, decision must be request_changes.
- findings must include concrete file/line risk when possible.
- never return markdown outside JSON.`
	payload, _ := json.MarshalIndent(in, "", "  ")
	user := fmt.Sprintf("Review input:\n%s\nUse tools when needed, then return strict JSON only.", string(payload))
	return system, user
}

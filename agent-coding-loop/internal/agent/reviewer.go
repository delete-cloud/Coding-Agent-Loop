package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/kina/agent-coding-loop/internal/model"
)

type Reviewer struct {
	client ClientConfig
}

type ReviewInput struct {
	Goal          string `json:"goal"`
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

func NewReviewer(client ClientConfig) *Reviewer {
	return &Reviewer{client: client}
}

func (r *Reviewer) Review(ctx context.Context, in ReviewInput) (ReviewOutput, error) {
	if !r.client.Ready() {
		if strings.Contains(strings.ToUpper(in.CommandOutput), "FAIL") {
			return ReviewOutput{
				Decision: string(model.ReviewDecisionRequestChanges),
				Summary:  "Automated gate failed: command output contains FAIL.",
				Findings: []model.ReviewFinding{{Severity: "high", File: "", Line: 0, Message: "Tests or checks failed"}},
				Markdown: "Requesting changes because validation commands failed.",
			}, nil
		}
		return ReviewOutput{
			Decision: string(model.ReviewDecisionApprove),
			Summary:  "Fallback reviewer approved: no failures detected in command output.",
			Findings: []model.ReviewFinding{},
			Markdown: "Approved by fallback reviewer.",
		}, nil
	}

	system := `You are a strict code reviewer.
Return JSON only with fields: decision, summary, findings, review_markdown.
- decision must be one of: approve, request_changes, comment
- If tests/checks fail, decision must be request_changes.`
	payload, _ := json.MarshalIndent(in, "", "  ")
	user := fmt.Sprintf("Review input:\n%s\nReturn strict JSON only.", string(payload))
	var out ReviewOutput
	if err := r.client.CompleteJSON(ctx, system, user, &out); err != nil {
		return ReviewOutput{}, err
	}
	if out.Decision == "" {
		out.Decision = string(model.ReviewDecisionComment)
	}
	if out.Summary == "" {
		out.Summary = "Reviewer completed."
	}
	if out.Markdown == "" {
		out.Markdown = out.Summary
	}
	return out, nil
}

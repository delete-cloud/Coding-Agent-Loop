package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
)

type Coder struct {
	client ClientConfig
}

type CoderInput struct {
	Goal           string
	RepoSummary    string
	PreviousReview string
	Diff           string
	TestOutput     string
	Commands       []string
	SkillsSummary  string
}

type CoderOutput struct {
	Summary  string   `json:"summary"`
	Patch    string   `json:"patch"`
	Commands []string `json:"commands"`
	Notes    string   `json:"notes"`
}

func NewCoder(client ClientConfig) *Coder {
	return &Coder{client: client}
}

func (c *Coder) Generate(ctx context.Context, in CoderInput) (CoderOutput, error) {
	if !c.client.Ready() {
		cmds := in.Commands
		if len(cmds) == 0 {
			cmds = []string{"go test ./..."}
		}
		return CoderOutput{
			Summary:  "LLM unavailable; fallback coder requests local validation commands.",
			Patch:    "",
			Commands: cmds,
			Notes:    "Configure OPENAI_BASE_URL and OPENAI_MODEL to enable patch generation.",
		}, nil
	}

	system := `You are a coding agent. Return JSON only with fields: summary, patch, commands, notes.
- patch must be unified diff text or empty string.
- commands must be shell commands to validate changes.
- keep commands minimal and deterministic.`
	payload, _ := json.MarshalIndent(in, "", "  ")
	user := fmt.Sprintf("Task input:\n%s\nReturn strict JSON only.", string(payload))
	var out CoderOutput
	if err := c.client.CompleteJSON(ctx, system, user, &out); err != nil {
		return CoderOutput{}, err
	}
	if len(out.Commands) == 0 {
		out.Commands = in.Commands
	}
	if out.Summary == "" {
		out.Summary = "Coder generated output."
	}
	out.Patch = strings.TrimSpace(out.Patch)
	return out, nil
}

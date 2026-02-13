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
	"github.com/kina/agent-coding-loop/internal/skills"
	"github.com/kina/agent-coding-loop/internal/tools"
)

type Coder struct {
	client ClientConfig
	runner *tools.Runner
	skills *skills.Registry
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

func NewCoder(client ClientConfig, opts ...Option) *Coder {
	deps := applyOptions(opts)
	return &Coder{
		client: client,
		runner: deps.runner,
		skills: deps.skills,
	}
}

func (c *Coder) Generate(ctx context.Context, in CoderInput) (CoderOutput, error) {
	if !c.client.Ready() {
		return fallbackCoder(in), nil
	}

	out, err := c.generateWithEino(ctx, in)
	if err == nil {
		return out, nil
	}

	fallback, fallbackErr := c.generateWithClient(ctx, in)
	if fallbackErr != nil {
		return CoderOutput{}, fmt.Errorf("eino generate failed: %w; fallback failed: %v", err, fallbackErr)
	}
	fallback.Notes = strings.TrimSpace(strings.TrimSpace(fallback.Notes) + "\nEino tool-call path failed, fallback completion used.")
	return fallback, nil
}

func fallbackCoder(in CoderInput) CoderOutput {
	cmds := in.Commands
	if len(cmds) == 0 {
		cmds = []string{"go test ./..."}
	}
	return CoderOutput{
		Summary:  "LLM unavailable; fallback coder requests local validation commands.",
		Patch:    "",
		Commands: cmds,
		Notes:    "Configure OPENAI_BASE_URL and OPENAI_MODEL to enable patch generation.",
	}
}

func (c *Coder) generateWithEino(ctx context.Context, in CoderInput) (CoderOutput, error) {
	chatModel, err := openai.NewChatModel(ctx, &openai.ChatModelConfig{
		BaseURL: c.client.BaseURL,
		Model:   c.client.Model,
		APIKey:  c.client.APIKey,
	})
	if err != nil {
		return CoderOutput{}, err
	}

	runner := c.runner
	if runner == nil {
		runner = tools.NewRunner()
	}
	toolset, err := tools.BuildCoderTools(in.RepoSummary, c.skills, runner)
	if err != nil {
		return CoderOutput{}, err
	}

	rAgent, err := react.NewAgent(ctx, &react.AgentConfig{
		ToolCallingModel: chatModel,
		ToolsConfig: compose.ToolsNodeConfig{
			Tools: toolset,
		},
		MaxStep: 16,
	})
	if err != nil {
		return CoderOutput{}, err
	}

	systemPrompt, userPrompt := coderPrompts(in)
	msg, err := rAgent.Generate(ctx, []*schema.Message{
		schema.SystemMessage(systemPrompt),
		schema.UserMessage(userPrompt),
	})
	if err != nil {
		return CoderOutput{}, err
	}
	var out CoderOutput
	content := ""
	if msg != nil {
		content = msg.Content
	}
	if err := json.Unmarshal([]byte(extractJSON(content)), &out); err != nil {
		return CoderOutput{}, fmt.Errorf("parse coder json failed: %w; content=%s", err, content)
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

func (c *Coder) generateWithClient(ctx context.Context, in CoderInput) (CoderOutput, error) {
	system, user := coderPrompts(in)
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

func coderPrompts(in CoderInput) (string, string) {
	system := `You are a coding agent operating in a local git repository.
You may call tools to inspect repository files, search code, inspect diff, run safe commands, and read skills.
Return JSON only with fields: summary, patch, commands, notes.
- patch must be unified diff text or empty string.
- commands must be shell commands to validate the patch.
- keep commands minimal and deterministic.
- never return markdown outside JSON.`
	payload, _ := json.MarshalIndent(in, "", "  ")
	user := fmt.Sprintf("Task input:\n%s\nUse tools when needed, then return strict JSON only.", string(payload))
	return system, user
}

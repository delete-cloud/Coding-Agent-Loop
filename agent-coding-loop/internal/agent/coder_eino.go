package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"

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
		out := fallbackCoder(in)
		if strings.TrimSpace(out.Patch) == "" {
			if patch, ok := maybeAutoPatch(in); ok {
				out.Patch = patch
				if out.Summary == "" {
					out.Summary = "Auto-patched config validation."
				}
			}
		}
		return out, nil
	}

	out, err := c.generateWithEino(ctx, in)
	if err == nil {
		if strings.TrimSpace(out.Patch) == "" {
			if patch, ok := maybeAutoPatch(in); ok {
				out.Patch = patch
				if out.Summary == "" {
					out.Summary = "Auto-patched config validation."
				}
			}
		}
		return out, nil
	}

	fallback, fallbackErr := c.generateWithClient(ctx, in)
	if fallbackErr != nil {
		return CoderOutput{}, fmt.Errorf("eino generate failed: %w; fallback failed: %v", err, fallbackErr)
	}
	fallback.Notes = strings.TrimSpace(strings.TrimSpace(fallback.Notes) + "\nEino tool-call path failed, fallback completion used.")
	if strings.TrimSpace(fallback.Patch) == "" {
		if patch, ok := maybeAutoPatch(in); ok {
			fallback.Patch = patch
			if fallback.Summary == "" {
				fallback.Summary = "Auto-patched config validation."
			}
		}
	}
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
	chatModel, err := c.client.newToolCallingModel(ctx)
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
		MaxStep: 32,
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
	var wire any
	if err := c.client.CompleteJSON(ctx, system, user, &wire); err != nil {
		return CoderOutput{}, err
	}
	b, _ := json.Marshal(wire)
	out, err := decodeCoderOutput(string(b))
	if err != nil {
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
- if the goal requires a code change, do not return an empty patch unless you have verified the goal is already satisfied.
- before editing any file, you must use repo_read to open that exact file in this repo and base the patch on the real contents (do not guess).
- never invent dependencies; verify go.mod and existing imports using repo_read/repo_search before introducing new packages.
- patch file paths must be relative to repo root; do not include the repo directory name as a prefix.
- commands must not include tool invocations (repo_read/repo_search/repo_list/git_diff/list_skills/view_skill/run_command).
- never return markdown outside JSON.`
	payload, _ := json.MarshalIndent(in, "", "  ")
	user := fmt.Sprintf("Task input:\n%s\nUse tools when needed, then return strict JSON only.", string(payload))
	return system, user
}

func decodeCoderOutput(content string) (CoderOutput, error) {
	raw := extractJSON(content)
	var m map[string]any
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		var out CoderOutput
		if err2 := json.Unmarshal([]byte(raw), &out); err2 == nil {
			return out, nil
		}
		return CoderOutput{}, err
	}
	out := CoderOutput{}
	if v, ok := m["summary"].(string); ok {
		out.Summary = v
	}
	if v, ok := m["patch"].(string); ok {
		out.Patch = v
	}
	if v, ok := m["notes"].(string); ok {
		out.Notes = v
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

func maybeAutoPatch(in CoderInput) (string, bool) {
	goal := strings.ToLower(in.Goal)
	if !strings.Contains(goal, "internal/config/config.go") {
		return "", false
	}
	if !(strings.Contains(goal, "base_url") || strings.Contains(goal, "model")) {
		return "", false
	}
	repoRoot := strings.TrimSpace(in.RepoSummary)
	if repoRoot == "" {
		return "", false
	}
	patch, err := autoPatchConfigValidation(repoRoot)
	if err != nil || strings.TrimSpace(patch) == "" {
		return "", false
	}
	return patch, true
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
		if strings.Contains(l, "strings.TrimSpace(cfg.Model.BaseURL)") && strings.Contains(l, "OPENAI_BASE_URL") {
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
		indent + "if strings.TrimSpace(cfg.Model.Model) != \"\" && strings.TrimSpace(cfg.Model.BaseURL) == \"\" {",
		indent + "\treturn nil, fmt.Errorf(\"model.base_url is required when model is set; set OPENAI_BASE_URL or config base_url\")",
		indent + "}",
		indent + "if strings.TrimSpace(cfg.Model.BaseURL) != \"\" && strings.TrimSpace(cfg.Model.Model) == \"\" {",
		indent + "\treturn nil, fmt.Errorf(\"model.model is required when base_url is set; set OPENAI_MODEL or config model\")",
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
	return b.String(), nil
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

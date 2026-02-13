package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"sort"
	"strings"

	einotool "github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/components/tool/utils"
	"github.com/kina/agent-coding-loop/internal/skills"
)

type listArgs struct {
	Path string `json:"path,omitempty"`
}

type readArgs struct {
	Path     string `json:"path"`
	MaxBytes int    `json:"max_bytes,omitempty"`
}

type searchArgs struct {
	Query string `json:"query"`
}

type commandArgs struct {
	Command string `json:"command"`
}

type listSkillsArgs struct {
	Filter string `json:"filter,omitempty"`
}

type viewSkillArgs struct {
	Name    string `json:"name"`
	Section string `json:"section,omitempty"`
	TOC     bool   `json:"toc,omitempty"`
}

func BuildCoderTools(repoRoot string, reg *skills.Registry, runner *Runner) ([]einotool.BaseTool, error) {
	repoRoot = normalizeRepoRoot(repoRoot)
	if runner == nil {
		runner = NewRunner()
	}
	common, err := buildReadOnlyTools(repoRoot, reg, runner)
	if err != nil {
		return nil, err
	}
	runCommand, err := utils.InferTool(
		"run_command",
		"Run a safe shell command in the repository root and return combined stdout/stderr.",
		func(ctx context.Context, input commandArgs) (string, error) {
			cmd := strings.TrimSpace(input.Command)
			if cmd == "" {
				return "", fmt.Errorf("command is required")
			}
			stdout, stderr, err := runner.Run(ctx, cmd, repoRoot)
			combined := strings.TrimSpace(stdout + "\n" + stderr)
			if err != nil {
				if combined == "" {
					return err.Error(), nil
				}
				return strings.TrimSpace(combined + "\nERROR: " + err.Error()), nil
			}
			if combined == "" {
				return "command completed with no output", nil
			}
			return combined, nil
		},
	)
	if err != nil {
		return nil, err
	}
	return append(common, runCommand), nil
}

func BuildReviewerTools(repoRoot string, reg *skills.Registry, runner *Runner) ([]einotool.BaseTool, error) {
	repoRoot = normalizeRepoRoot(repoRoot)
	if runner == nil {
		runner = NewRunner(WithReadOnly(true))
	}
	return buildReadOnlyTools(repoRoot, reg, runner)
}

func buildReadOnlyTools(repoRoot string, reg *skills.Registry, runner *Runner) ([]einotool.BaseTool, error) {
	repoList, err := utils.InferTool(
		"repo_list",
		"List files under repository path. path is relative to repo root.",
		func(_ context.Context, input listArgs) (string, error) {
			path := strings.TrimSpace(input.Path)
			if path == "" {
				path = "."
			}
			entries, err := RepoList(repoRoot, path)
			if err != nil {
				return "", err
			}
			return strings.Join(entries, "\n"), nil
		},
	)
	if err != nil {
		return nil, err
	}

	repoRead, err := utils.InferTool(
		"repo_read",
		"Read a file in repository by relative path.",
		func(_ context.Context, input readArgs) (string, error) {
			maxBytes := input.MaxBytes
			if maxBytes <= 0 {
				maxBytes = 64 * 1024
			}
			return RepoRead(repoRoot, input.Path, maxBytes)
		},
	)
	if err != nil {
		return nil, err
	}

	repoSearch, err := utils.InferTool(
		"repo_search",
		"Search files in repository containing the query string.",
		func(_ context.Context, input searchArgs) (string, error) {
			matches, err := RepoSearch(repoRoot, input.Query)
			if err != nil {
				return "", err
			}
			return strings.Join(matches, "\n"), nil
		},
	)
	if err != nil {
		return nil, err
	}

	gitDiff, err := utils.InferTool(
		"git_diff",
		"Get current git diff in repository.",
		func(ctx context.Context, _ struct{}) (string, error) {
			stdout, stderr, err := runner.Run(ctx, "git diff -- .", repoRoot)
			out := strings.TrimSpace(stdout + "\n" + stderr)
			if err != nil {
				if out == "" {
					return err.Error(), nil
				}
				return strings.TrimSpace(out + "\nERROR: " + err.Error()), nil
			}
			return out, nil
		},
	)
	if err != nil {
		return nil, err
	}

	listSkillTool, err := utils.InferTool(
		"list_skills",
		"List available skills with short metadata.",
		func(_ context.Context, input listSkillsArgs) (string, error) {
			items := ListSkills(reg)
			filter := strings.ToLower(strings.TrimSpace(input.Filter))
			names := make([]string, 0, len(items))
			for _, item := range items {
				line := fmt.Sprintf("%s: %s", item.Name, item.Description)
				if filter != "" && !strings.Contains(strings.ToLower(line), filter) {
					continue
				}
				names = append(names, line)
			}
			sort.Strings(names)
			if len(names) == 0 {
				return "No skills available.", nil
			}
			return strings.Join(names, "\n"), nil
		},
	)
	if err != nil {
		return nil, err
	}

	viewSkillTool, err := utils.InferTool(
		"view_skill",
		"View a skill body, TOC, or one section from SKILL.md.",
		func(_ context.Context, input viewSkillArgs) (string, error) {
			return ViewSkill(reg, input.Name, input.Section, input.TOC)
		},
	)
	if err != nil {
		return nil, err
	}

	return []einotool.BaseTool{
		repoList,
		repoRead,
		repoSearch,
		gitDiff,
		listSkillTool,
		viewSkillTool,
	}, nil
}

func normalizeRepoRoot(repoRoot string) string {
	root := strings.TrimSpace(repoRoot)
	if root == "" {
		return "."
	}
	clean := filepath.Clean(root)
	if clean == "" {
		return "."
	}
	return clean
}

func toolNamesForDebug(items []einotool.BaseTool) string {
	names := make([]string, 0, len(items))
	for _, item := range items {
		info, err := item.Info(context.Background())
		if err != nil || info == nil {
			continue
		}
		names = append(names, info.Name)
	}
	data, _ := json.Marshal(names)
	return string(data)
}

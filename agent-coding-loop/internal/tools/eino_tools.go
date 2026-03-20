package tools

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	einotool "github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/components/tool/utils"
	"github.com/kina/agent-coding-loop/internal/kb"
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

type kbSearchArgs struct {
	Query     string `json:"query"`
	TopK      int    `json:"top_k,omitempty"`
	QueryType string `json:"query_type,omitempty"`
	Where     string `json:"where,omitempty"`
}

type ToolMode string

const (
	ToolModePlan   ToolMode = "plan"
	ToolModeCode   ToolMode = "code"
	ToolModeRepair ToolMode = "repair"
	ToolModeReview ToolMode = "review"
)

func BuildToolsForMode(repoRoot string, mode ToolMode, reg *skills.Registry, runner *Runner, kbClient *kb.Client) ([]einotool.BaseTool, error) {
	repoRoot = normalizeRepoRoot(repoRoot)
	if runner == nil {
		runner = NewRunner(WithReadOnly(true))
	}
	common, err := buildReadOnlyTools(repoRoot, reg, runner, kbClient)
	if err != nil {
		return nil, err
	}
	switch mode {
	case ToolModePlan, ToolModeRepair, ToolModeReview:
		return common, nil
	case ToolModeCode:
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
	default:
		return nil, fmt.Errorf("unknown tool mode: %s", mode)
	}
}

func BuildCoderTools(repoRoot string, reg *skills.Registry, runner *Runner, kbClient *kb.Client) ([]einotool.BaseTool, error) {
	return BuildToolsForMode(repoRoot, ToolModeCode, reg, runner, kbClient)
}

func BuildPlannerTools(repoRoot string, reg *skills.Registry, runner *Runner, kbClient *kb.Client) ([]einotool.BaseTool, error) {
	return BuildToolsForMode(repoRoot, ToolModePlan, reg, runner, kbClient)
}

func BuildReviewerTools(repoRoot string, reg *skills.Registry, runner *Runner, kbClient *kb.Client) ([]einotool.BaseTool, error) {
	return BuildToolsForMode(repoRoot, ToolModeReview, reg, runner, kbClient)
}

func buildReadOnlyTools(repoRoot string, reg *skills.Registry, runner *Runner, kbClient *kb.Client) ([]einotool.BaseTool, error) {
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
				if errors.Is(err, os.ErrNotExist) {
					return fmt.Sprintf("path not found: %s", path), nil
				}
				return formatToolError("repo_list", path, err), nil
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
			out, err := RepoRead(repoRoot, input.Path, maxBytes)
			if err != nil {
				if errors.Is(err, os.ErrNotExist) {
					return fmt.Sprintf("path not found: %s", strings.TrimSpace(input.Path)), nil
				}
				return formatToolError("repo_read", strings.TrimSpace(input.Path), err), nil
			}
			return out, nil
		},
	)
	if err != nil {
		return nil, err
	}

	repoSearch, err := utils.InferTool(
		"repo_search",
		"Search files in repository containing the query string.",
		func(_ context.Context, input searchArgs) (string, error) {
			q := strings.TrimSpace(input.Query)
			if q == "" {
				return "query is required; provide a non-empty string for repo_search (e.g. \"WithCheckPointID\").", nil
			}
			matches, err := RepoSearch(repoRoot, q)
			if err != nil {
				return formatToolError("repo_search", q, err), nil
			}
			if len(matches) == 0 {
				return "no matches", nil
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

	kbSearch, err := utils.InferTool(
		"kb_search",
		"Search external knowledge base (LanceDB sidecar) for relevant context. Returns cited chunks with path and offsets.",
		func(ctx context.Context, input kbSearchArgs) (string, error) {
			q := strings.TrimSpace(input.Query)
			if q == "" {
				return "query is required; provide a short topic or question for kb_search (e.g. \"rag pipeline glossary\").", nil
			}
			if kbClient == nil || strings.TrimSpace(kbClient.BaseURL) == "" {
				return "kb is not configured. Start kb/server.py and set AGENT_LOOP_KB_URL or use default http://127.0.0.1:8788.", nil
			}
			topK := input.TopK
			if topK <= 0 {
				topK = 8
			}
			resp, err := kbClient.Search(ctx, kb.SearchRequest{
				Query:     q,
				TopK:      topK,
				QueryType: strings.TrimSpace(input.QueryType),
				Where:     strings.TrimSpace(input.Where),
			})
			if err != nil {
				return formatToolError("kb_search", q, err), nil
			}
			if len(resp.Hits) == 0 {
				return "no hits", nil
			}
			var b strings.Builder
			for i, h := range resp.Hits {
				if i >= topK {
					break
				}
				score := ""
				if h.Score != nil {
					score = fmt.Sprintf(" score=%.6f", *h.Score)
				}
				ref := strings.TrimSpace(h.Path)
				if strings.TrimSpace(h.Heading) != "" {
					ref = ref + "#" + strings.TrimSpace(h.Heading)
				}
				b.WriteString(fmt.Sprintf("[%d] %s (%d-%d)%s\n", i+1, ref, h.Start, h.End, score))
				txt := strings.TrimSpace(h.Text)
				if len(txt) > 1200 {
					txt = txt[:1200]
				}
				b.WriteString(txt + "\n\n")
			}
			return strings.TrimSpace(b.String()), nil
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
		kbSearch,
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

func formatToolError(toolName string, input string, err error) string {
	msg := strings.TrimSpace(fmt.Sprint(err))
	if msg == "" {
		msg = "unknown error"
	}
	input = strings.TrimSpace(input)
	if input == "" {
		return fmt.Sprintf("%s error: %s (type=%T)", toolName, msg, err)
	}
	return fmt.Sprintf("%s error: %s (type=%T, input=%q)", toolName, msg, err, input)
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

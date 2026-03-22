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
			"Run a safe shell command in the repository root and return combined stdout/stderr. Use when you need to inspect repository state with a safe command or gather command-line context that repo inspection tools do not provide directly. Do not use it to read a known file or search for a known symbol; use repo_read or repo_search first when those fit. Example JSON: {\"command\":\"git status --short\"}. If a command fails, read the output and then narrow or correct the command before retrying.",
			func(ctx context.Context, input commandArgs) (string, error) {
				cmd := strings.TrimSpace(input.Command)
				if cmd == "" {
					return "command is required; provide a safe shell command for run_command (e.g. \"git status --short\").", nil
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
		"List files under a repository path. Use when you need directory structure or need to discover candidate files first. Do not use when you already know the exact file path; use repo_read instead. Example JSON: {\"path\":\"internal\"}. If a path is missing, fix the path or switch to repo_read for a known file.",
		func(_ context.Context, input listArgs) (string, error) {
			path := strings.TrimSpace(input.Path)
			if path == "" {
				path = "."
			}
			entries, err := RepoList(repoRoot, path)
			if err != nil {
				if errors.Is(err, os.ErrNotExist) {
					return formatToolMessage("repo_list", path, "path not found"), nil
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
		"Read a repository file by relative path. Use when you already know the file path and need contents. Do not use to search for an unknown symbol or string across the repo; use repo_search first. Example JSON: {\"path\":\"internal/tools/eino_tools.go\",\"max_bytes\":4096}. If the file is missing, confirm with repo_list or use repo_search to find the right file.",
		func(_ context.Context, input readArgs) (string, error) {
			maxBytes := input.MaxBytes
			if maxBytes <= 0 {
				maxBytes = 64 * 1024
			}
			out, err := RepoRead(repoRoot, input.Path, maxBytes)
			if err != nil {
				if errors.Is(err, os.ErrNotExist) {
					return formatToolMessage("repo_read", strings.TrimSpace(input.Path), "path not found"), nil
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
		"Search repository files containing a query string. Use when you know the symbol or string but not its location. Do not use when you already know which file to read; use repo_read instead of searching the whole repo first. Example JSON: {\"query\":\"buildReadOnlyTools\"}. If there are too many or no matches, refine the query or switch to repo_read once you know the file.",
		func(_ context.Context, input searchArgs) (string, error) {
			q := strings.TrimSpace(input.Query)
			if q == "" {
				return formatToolMessage("repo_search", "", "query is required"), nil
			}
			matches, err := RepoSearch(repoRoot, q)
			if err != nil {
				return formatToolError("repo_search", q, err), nil
			}
			if len(matches) == 0 {
				return formatToolMessage("repo_search", q, "no matches"), nil
			}
			return strings.Join(matches, "\n"), nil
		},
	)
	if err != nil {
		return nil, err
	}

	gitDiff, err := utils.InferTool(
		"git_diff",
		"Get the current git diff in the repository. Use when you need the current modified diff or want to review edits already made. Do not use it to understand untouched repository state; use repo_list, repo_read, or repo_search for that. Example JSON: {}. If the diff is empty, use repo_list, repo_read, or repo_search to inspect the repo directly.",
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
		"Search the external knowledge base (LanceDB sidecar) for relevant context and return cited chunks with path and offsets. Use when you need external or KB context that is not already in the repository. Do not use it instead of inspecting repository code; repo inspection tools remain primary for local code understanding. Example JSON: {\"query\":\"rag pipeline glossary\",\"top_k\":5}. If kb_search has no hits or is unavailable, inspect the repo directly with repo_list, repo_read, or repo_search.",
		func(ctx context.Context, input kbSearchArgs) (string, error) {
			q := strings.TrimSpace(input.Query)
			if q == "" {
				return formatToolMessage("kb_search", "", "query is required"), nil
			}
			if kbClient == nil || strings.TrimSpace(kbClient.BaseURL) == "" {
				return formatToolMessage("kb_search", q, "kb is not configured"), nil
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
				return formatToolMessage("kb_search", q, "no hits"), nil
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
	return formatToolMessage(toolName, input, msg)
}

func formatToolMessage(toolName string, input string, msg string) string {
	msg = strings.TrimSpace(msg)
	if msg == "" {
		msg = "unknown error"
	}
	input = strings.TrimSpace(input)
	lines := []string{"ERROR: " + msg}
	if input != "" {
		lines = append(lines, formatToolInputLine(toolName, input))
	}
	lines = append(lines, "SUGGESTION: "+toolSuggestion(toolName, msg))
	return strings.Join(lines, "\n")
}

func formatToolInputLine(toolName string, input string) string {
	switch toolName {
	case "repo_list", "repo_read":
		return "PATH: " + input
	case "repo_search", "kb_search":
		return "QUERY: " + input
	default:
		return "INPUT: " + input
	}
}

func toolSuggestion(toolName string, msg string) string {
	lower := strings.ToLower(strings.TrimSpace(msg))
	switch toolName {
	case "repo_list":
		if strings.Contains(lower, "escapes repo root") {
			return "Use a repository-relative path inside the repo root, such as \".\" or \"internal\". If you already know the file, switch to repo_read."
		}
		if strings.Contains(lower, "not found") {
			return "List a parent directory first or correct the path. If you already know a symbol or string, switch to repo_search."
		}
		return "Use a repository-relative directory path. If you already know the file, switch to repo_read."
	case "repo_read":
		if strings.Contains(lower, "escapes repo root") || strings.Contains(lower, "not found") {
			return "Use a repository-relative file path. If you do not know the exact file, use repo_list or repo_search first."
		}
		return "Use a repository-relative file path. If you do not know the exact file, use repo_list or repo_search first."
	case "repo_search":
		if strings.Contains(lower, "query is required") {
			return "Provide a short, distinctive query string, then switch to repo_read once you know the file."
		}
		if strings.Contains(lower, "no matches") || strings.Contains(lower, "too many") || strings.Contains(lower, "broad") {
			return "Narrow the query to a distinctive symbol or phrase. If you already know the file, use repo_read instead."
		}
		return "Narrow the query to a distinctive symbol or phrase. If you already know the file, use repo_read instead."
	case "kb_search":
		if strings.Contains(lower, "query is required") {
			return "Provide a short KB topic or question. If the answer should be in the repo, inspect the repo directly."
		}
		if strings.Contains(lower, "not configured") {
			return "Start kb/server.py and verify AGENT_LOOP_KB_URL. If KB is unavailable, inspect the repo directly with repo_list, repo_read, or repo_search."
		}
		if isKBServiceFailure(lower) {
			return "The KB service appears unavailable or unhealthy. Check kb/server.py and AGENT_LOOP_KB_URL, then fall back to repo inspection with repo_list, repo_read, or repo_search."
		}
		if strings.Contains(lower, "no hits") {
			return "Try a narrower KB query or different keywords. If the answer should be in the repo, inspect the repo directly."
		}
		return "Refine the KB query or inspect the repo directly if the KB does not help."
	default:
		return "Retry with a narrower input or switch to a more specific tool."
	}
}

func isKBServiceFailure(msg string) bool {
	return strings.Contains(msg, "connection refused") ||
		strings.Contains(msg, "connection reset") ||
		strings.Contains(msg, "unexpected eof") ||
		strings.Contains(msg, "timeout") ||
		strings.Contains(msg, "deadline exceeded") ||
		strings.Contains(msg, "context canceled") ||
		strings.Contains(msg, "status=") ||
		strings.Contains(msg, "decode kb search response failed") ||
		strings.Contains(msg, "kb search failed:") ||
		strings.Contains(msg, "unavailable")
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

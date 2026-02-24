package git

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/kina/agent-coding-loop/internal/tools"
)

type Client struct {
	runner *tools.Runner
}

func NewClient(r *tools.Runner) *Client {
	return &Client{runner: r}
}

func (c *Client) EnsureRepo(ctx context.Context, repo string) error {
	_, _, err := c.runner.Run(ctx, "git rev-parse --git-dir", repo)
	return err
}

func (c *Client) CurrentBranch(ctx context.Context, repo string) (string, error) {
	stdout, _, err := c.runner.Run(ctx, "git rev-parse --abbrev-ref HEAD", repo)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(stdout), nil
}

func (c *Client) RemoteURL(ctx context.Context, repo string) (string, error) {
	stdout, _, err := c.runner.Run(ctx, "git config --get remote.origin.url", repo)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(stdout), nil
}

func (c *Client) CreateFeatureBranch(ctx context.Context, repo string) (string, error) {
	branch := fmt.Sprintf("agent-loop/%d", time.Now().Unix())
	_, _, err := c.runner.Run(ctx, "git checkout -b "+branch, repo)
	if err != nil {
		return "", err
	}
	return branch, nil
}

func (c *Client) CheckoutBranch(ctx context.Context, repo, branch string) error {
	_, _, err := c.runner.Run(ctx, "git checkout "+shellQuote(branch), repo)
	return err
}

func (c *Client) Diff(ctx context.Context, repo string) (string, error) {
	stdout, _, err := c.runner.Run(ctx, "git diff -- .", repo)
	if err != nil {
		return "", err
	}
	return stdout, nil
}

func (c *Client) StatusShort(ctx context.Context, repo string) (string, error) {
	stdout, _, err := c.runner.Run(ctx, "git status --short", repo)
	if err != nil {
		return "", err
	}
	return stdout, nil
}

func (c *Client) ApplyPatch(ctx context.Context, repo, patch string) error {
	patchPath := filepath.Join(repo, ".agent-loop-last.patch")
	patch = normalizeUnifiedDiffForRepo(repo, patch)
	if err := os.WriteFile(patchPath, []byte(patch), 0o644); err != nil {
		return err
	}
	_, stderr, err := c.runner.Run(ctx, "git apply "+shellQuote(patchPath), repo)
	if err != nil {
		if strings.TrimSpace(stderr) != "" {
			return fmt.Errorf("%w: %s", err, strings.TrimSpace(stderr))
		}
		return err
	}
	return nil
}

func (c *Client) CommitAll(ctx context.Context, repo, message string) (string, error) {
	if _, _, err := c.runner.Run(ctx, "git add -A", repo); err != nil {
		return "", err
	}
	if _, _, err := c.runner.Run(ctx, "git commit -m "+shellQuote(message), repo); err != nil {
		return "", err
	}
	stdout, _, err := c.runner.Run(ctx, "git rev-parse HEAD", repo)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(stdout), nil
}

func (c *Client) CommitPaths(ctx context.Context, repo string, paths []string, message string) (string, error) {
	if len(paths) == 0 {
		return "", nil
	}
	quoted := make([]string, 0, len(paths))
	for _, p := range paths {
		if strings.TrimSpace(p) == "" {
			continue
		}
		quoted = append(quoted, shellQuote(p))
	}
	if len(quoted) == 0 {
		return "", nil
	}
	if _, _, err := c.runner.Run(ctx, "git add -- "+strings.Join(quoted, " "), repo); err != nil {
		return "", err
	}
	if _, _, err := c.runner.Run(ctx, "git commit -m "+shellQuote(message), repo); err != nil {
		return "", err
	}
	stdout, _, err := c.runner.Run(ctx, "git rev-parse HEAD", repo)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(stdout), nil
}

func (c *Client) Push(ctx context.Context, repo, branch string) error {
	_, _, err := c.runner.Run(ctx, "git push -u origin "+shellQuote(branch), repo)
	return err
}

func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "'\\''") + "'"
}

func normalizeUnifiedDiff(patch string) string {
	patch = strings.TrimSpace(patch) + "\n"
	if strings.HasPrefix(strings.TrimSpace(patch), "```") {
		patch = strings.TrimSpace(patch)
		patch = strings.TrimPrefix(patch, "```diff")
		patch = strings.TrimPrefix(patch, "```patch")
		patch = strings.TrimPrefix(patch, "```")
		patch = strings.TrimSuffix(patch, "```")
		patch = strings.TrimSpace(patch) + "\n"
	}
	return fixHunkCounts(patch)
}

func normalizeUnifiedDiffForRepo(repo string, patch string) string {
	patch = normalizeUnifiedDiff(patch)
	return rewriteUnifiedDiffPaths(repo, patch)
}

func rewriteUnifiedDiffPaths(repo string, patch string) string {
	base := filepath.Base(repo)
	if strings.TrimSpace(base) == "" || base == "." || base == string(os.PathSeparator) {
		return patch
	}
	prefix := base + string(os.PathSeparator)
	lines := strings.Split(patch, "\n")
	for i := 0; i < len(lines); i++ {
		line := lines[i]
		if strings.HasPrefix(line, "diff --git ") {
			rest := strings.TrimPrefix(line, "diff --git ")
			parts := strings.SplitN(rest, " ", 2)
			if len(parts) == 2 {
				a := rewriteDiffPathToken(parts[0], prefix)
				b := rewriteDiffPathToken(parts[1], prefix)
				lines[i] = "diff --git " + a + " " + b
			}
			continue
		}
		if strings.HasPrefix(line, "--- ") || strings.HasPrefix(line, "+++ ") {
			head := line[:4]
			p := strings.TrimSpace(line[4:])
			if p == "/dev/null" {
				continue
			}
			lines[i] = head + " " + rewriteDiffPathToken(p, prefix)
			continue
		}
		if strings.HasPrefix(line, "rename from ") {
			p := strings.TrimPrefix(line, "rename from ")
			lines[i] = "rename from " + rewriteRelPath(p, prefix)
			continue
		}
		if strings.HasPrefix(line, "rename to ") {
			p := strings.TrimPrefix(line, "rename to ")
			lines[i] = "rename to " + rewriteRelPath(p, prefix)
			continue
		}
	}
	return strings.Join(lines, "\n")
}

func rewriteDiffPathToken(tok string, prefix string) string {
	tok = strings.TrimSpace(tok)
	if tok == "" {
		return tok
	}
	if strings.HasPrefix(tok, "a/") {
		return "a/" + rewriteRelPath(strings.TrimPrefix(tok, "a/"), prefix)
	}
	if strings.HasPrefix(tok, "b/") {
		return "b/" + rewriteRelPath(strings.TrimPrefix(tok, "b/"), prefix)
	}
	return rewriteRelPath(tok, prefix)
}

func rewriteRelPath(rel string, prefix string) string {
	rel = strings.TrimLeft(rel, string(os.PathSeparator))
	if strings.HasPrefix(rel, prefix) {
		return strings.TrimPrefix(rel, prefix)
	}
	return rel
}

func fixHunkCounts(patch string) string {
	lines := strings.Split(patch, "\n")
	out := make([]string, 0, len(lines))
	for i := 0; i < len(lines); i++ {
		line := lines[i]
		oldStart, newStart, suffix, ok := parseHunkHeader(line)
		if !ok {
			out = append(out, line)
			continue
		}
		oldCount, newCount := 0, 0
		j := i + 1
		for ; j < len(lines); j++ {
			l := lines[j]
			if strings.HasPrefix(l, "@@ -") || strings.HasPrefix(l, "diff --git ") {
				break
			}
			if strings.HasPrefix(l, "--- ") && j > 0 && strings.HasPrefix(lines[j-1], "diff --git ") {
				break
			}
			if strings.HasPrefix(l, "\\ No newline at end of file") {
				continue
			}
			if l == "" {
				continue
			}
			switch l[0] {
			case ' ':
				oldCount++
				newCount++
			case '-':
				oldCount++
			case '+':
				newCount++
			}
		}
		out = append(out, fmt.Sprintf("@@ -%d,%d +%d,%d @@%s", oldStart, oldCount, newStart, newCount, suffix))
		for k := i + 1; k < j; k++ {
			out = append(out, lines[k])
		}
		i = j - 1
	}
	return strings.Join(out, "\n")
}

func parseHunkHeader(line string) (int, int, string, bool) {
	if !strings.HasPrefix(line, "@@ -") {
		return 0, 0, "", false
	}
	rest := strings.TrimPrefix(line, "@@ -")
	parts := strings.SplitN(rest, " +", 2)
	if len(parts) != 2 {
		return 0, 0, "", false
	}
	oldPart := parts[0]
	parts2 := strings.SplitN(parts[1], " @@", 2)
	if len(parts2) != 2 {
		return 0, 0, "", false
	}
	newPart := parts2[0]
	suffix := parts2[1]
	oldStart, _, ok := parseRange(oldPart)
	if !ok {
		return 0, 0, "", false
	}
	newStart, _, ok := parseRange(newPart)
	if !ok {
		return 0, 0, "", false
	}
	return oldStart, newStart, suffix, true
}

func parseRange(s string) (int, int, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, 0, false
	}
	parts := strings.SplitN(s, ",", 2)
	start, err := strconv.Atoi(parts[0])
	if err != nil {
		return 0, 0, false
	}
	count := 1
	if len(parts) == 2 && strings.TrimSpace(parts[1]) != "" {
		n, err := strconv.Atoi(parts[1])
		if err != nil {
			return 0, 0, false
		}
		count = n
	}
	return start, count, true
}

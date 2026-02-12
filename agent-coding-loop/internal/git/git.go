package git

import (
	"context"
	"fmt"
	"path/filepath"
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
	if _, _, err := c.runner.Run(ctx, "cat > "+shellQuote(patchPath)+" <<'PATCH'\n"+patch+"\nPATCH", repo); err != nil {
		return err
	}
	_, _, err := c.runner.Run(ctx, "git apply "+shellQuote(patchPath), repo)
	return err
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

package github

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/kina/agent-coding-loop/internal/model"
)

type Runner interface {
	Run(ctx context.Context, cmd string, dir string) (stdout string, stderr string, err error)
}

type Client struct {
	runner Runner
}

func NewClient(r Runner) *Client {
	return &Client{runner: r}
}

func (c *Client) ResolvePRMode(ctx context.Context, requested model.PRMode, remoteURL string) model.PRMode {
	switch requested {
	case model.PRModeLive:
		return model.PRModeLive
	case model.PRModeDryRun:
		return model.PRModeDryRun
	}
	if !strings.Contains(strings.ToLower(remoteURL), "github.com") {
		return model.PRModeDryRun
	}
	_, _, err := c.runner.Run(ctx, "gh auth status", "")
	if err != nil {
		return model.PRModeDryRun
	}
	return model.PRModeLive
}

func (c *Client) CreatePR(ctx context.Context, repo, title, body, head, base string) (string, error) {
	cmd := fmt.Sprintf("gh pr create --title %q --body %q --head %q --base %q", title, body, head, base)
	stdout, stderr, err := c.runner.Run(ctx, cmd, repo)
	if err != nil {
		return "", fmt.Errorf("gh pr create failed: %v %s", err, strings.TrimSpace(stderr))
	}
	return strings.TrimSpace(stdout), nil
}

func (c *Client) SubmitReview(ctx context.Context, repo string, decision model.ReviewDecision, bodyFile string) error {
	flag := "--comment"
	switch decision {
	case model.ReviewDecisionApprove:
		flag = "--approve"
	case model.ReviewDecisionRequestChanges:
		flag = "--request-changes"
	}
	cmd := fmt.Sprintf("gh pr review %s -F %q", flag, bodyFile)
	_, stderr, err := c.runner.Run(ctx, cmd, repo)
	if err != nil {
		return fmt.Errorf("gh pr review failed: %v %s", err, strings.TrimSpace(stderr))
	}
	return nil
}

func WriteDryRunArtifacts(root, runID, title, body, review string) (string, error) {
	base := filepath.Join(root, ".agent-loop-artifacts", runID, "pr")
	if err := os.MkdirAll(base, 0o755); err != nil {
		return "", err
	}
	cmd := fmt.Sprintf("gh pr create --title %q --body %q\n# then\ngh pr review --comment -F review.md\n", title, body)
	if err := os.WriteFile(filepath.Join(base, "commands.sh"), []byte(cmd), 0o755); err != nil {
		return "", err
	}
	if err := os.WriteFile(filepath.Join(base, "review.md"), []byte(review), 0o644); err != nil {
		return "", err
	}
	return base, nil
}

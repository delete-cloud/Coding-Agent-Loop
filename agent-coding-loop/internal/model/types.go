package model

import (
	"errors"
	"fmt"
	"path/filepath"
	"strings"
)

type RunStatus string

const (
	RunStatusQueued      RunStatus = "queued"
	RunStatusRunning     RunStatus = "running"
	RunStatusNeedsChange RunStatus = "needs_changes"
	RunStatusBlocked     RunStatus = "blocked"
	RunStatusCompleted   RunStatus = "completed"
	RunStatusFailed      RunStatus = "failed"
)

type LoopDecision string

const (
	LoopDecisionContinue       LoopDecision = "continue"
	LoopDecisionRequestChanges LoopDecision = "request_changes"
	LoopDecisionComplete       LoopDecision = "complete"
	LoopDecisionAbort          LoopDecision = "abort"
)

type PRMode string

const (
	PRModeAuto   PRMode = "auto"
	PRModeLive   PRMode = "live"
	PRModeDryRun PRMode = "dry_run"
)

func ParsePRMode(v string) (PRMode, error) {
	switch strings.TrimSpace(strings.ToLower(v)) {
	case "", "auto":
		return PRModeAuto, nil
	case "live":
		return PRModeLive, nil
	case "dry-run", "dry_run", "dryrun":
		return PRModeDryRun, nil
	default:
		return "", fmt.Errorf("unsupported pr mode: %s", v)
	}
}

type ReviewDecision string

const (
	ReviewDecisionApprove        ReviewDecision = "approve"
	ReviewDecisionRequestChanges ReviewDecision = "request_changes"
	ReviewDecisionComment        ReviewDecision = "comment"
)

type CommandSet struct {
	Test  []string `json:"test"`
	Lint  []string `json:"lint"`
	Build []string `json:"build"`
}

type ModelSpec struct {
	Provider string `json:"provider" yaml:"provider"`
	Model    string `json:"model" yaml:"model"`
	BaseURL  string `json:"base_url" yaml:"base_url"`
	APIKey   string `json:"api_key" yaml:"api_key"`
}

type RunSpec struct {
	Goal               string     `json:"goal" yaml:"goal"`
	Repo               string     `json:"repo" yaml:"repo"`
	Commands           CommandSet `json:"commands" yaml:"commands"`
	PRMode             PRMode     `json:"pr_mode" yaml:"pr_mode"`
	MaxIterations      int        `json:"max_iterations" yaml:"max_iterations"`
	Provider           string     `json:"provider" yaml:"provider"`
	Model              string     `json:"model" yaml:"model"`
	ContinueLoopOnDeny bool       `json:"continue_loop_on_deny" yaml:"continue_loop_on_deny"`
}

func (s *RunSpec) Normalize() {
	if s.PRMode == "" {
		s.PRMode = PRModeAuto
	}
	if s.MaxIterations <= 0 {
		s.MaxIterations = 5
	}
	if s.Repo != "" {
		s.Repo = filepath.Clean(s.Repo)
	}
}

func (s *RunSpec) Validate() error {
	s.Normalize()
	if strings.TrimSpace(s.Goal) == "" {
		return errors.New("goal is required")
	}
	if s.MaxIterations < 1 {
		return errors.New("max_iterations must be >= 1")
	}
	switch s.PRMode {
	case PRModeAuto, PRModeLive, PRModeDryRun:
		return nil
	default:
		return fmt.Errorf("invalid pr_mode: %s", s.PRMode)
	}
}

type RunResult struct {
	RunID         string         `json:"run_id"`
	Status        RunStatus      `json:"status"`
	Branch        string         `json:"branch"`
	Commit        string         `json:"commit"`
	PRURL         string         `json:"pr_url"`
	ReviewOutcome ReviewDecision `json:"review_outcome"`
	ArtifactsDir  string         `json:"artifacts_dir"`
	Summary       string         `json:"summary"`
}

type ReviewFinding struct {
	Severity string `json:"severity"`
	File     string `json:"file"`
	Line     int    `json:"line"`
	Message  string `json:"message"`
}

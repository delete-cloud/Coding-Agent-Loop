package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/kina/agent-coding-loop/internal/model"
)

const (
	progressPollInterval = 1 * time.Second
	progressFetchLimit   = 100
	defaultTailTimeout   = 30 * time.Minute
)

type progressService interface {
	RunWithProgress(ctx context.Context, spec model.RunSpec) (string, <-chan model.RunResult, error)
	Resume(ctx context.Context, runID string) (model.RunResult, error)
	GetProgressEventsAfter(ctx context.Context, runID string, afterID int64, limit int) ([]model.ProgressEvent, error)
}

type fetchProgressFunc func(ctx context.Context, runID string, afterID int64, limit int) ([]model.ProgressEvent, error)

func runWithProgressCmd(ctx context.Context, svc progressService, spec model.RunSpec, stdout, stderr io.Writer, tailTimeout time.Duration) error {
	runID, resultCh, err := svc.RunWithProgress(ctx, spec)
	if err != nil {
		return err
	}
	if err := tailProgress(ctx, svc.GetProgressEventsAfter, runID, stderr, tailTimeout); err != nil {
		return err
	}
	result, ok := <-resultCh
	if !ok {
		return fmt.Errorf("run result channel closed")
	}
	return printJSONTo(stdout, result)
}

func resumeWithProgressCmd(ctx context.Context, svc progressService, runID string, stdout, stderr io.Writer, tailTimeout time.Duration) error {
	resultCh := make(chan model.RunResult, 1)
	go func() {
		defer close(resultCh)
		result, err := svc.Resume(ctx, runID)
		if err != nil {
			result = model.RunResult{RunID: runID, Status: model.RunStatusFailed, Summary: err.Error()}
		}
		resultCh <- result
	}()

	if err := tailProgress(ctx, svc.GetProgressEventsAfter, runID, stderr, tailTimeout); err != nil {
		return err
	}
	result, ok := <-resultCh
	if !ok {
		return fmt.Errorf("resume result channel closed")
	}
	return printJSONTo(stdout, result)
}

func tailProgress(ctx context.Context, fetch fetchProgressFunc, runID string, stderr io.Writer, tailTimeout time.Duration) error {
	if fetch == nil {
		return fmt.Errorf("fetch progress function is required")
	}
	if strings.TrimSpace(runID) == "" {
		return fmt.Errorf("run id is required")
	}
	timeout := tailTimeout
	if timeout <= 0 {
		timeout = defaultTailTimeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	ticker := time.NewTicker(progressPollInterval)
	defer ticker.Stop()

	var afterID int64
	for {
		events, err := fetch(ctx, runID, afterID, progressFetchLimit)
		if err != nil {
			return err
		}
		for _, event := range events {
			if event.ID > afterID {
				afterID = event.ID
			}
			if err := renderProgressEvent(stderr, event); err != nil {
				return err
			}
			if isTerminalCLIProgressEvent(event.EventType) {
				return nil
			}
		}

		select {
		case <-ctx.Done():
			return fmt.Errorf("tail progress timed out for run %s: %w", runID, ctx.Err())
		case <-ticker.C:
		}
	}
}

func renderProgressEvent(w io.Writer, event model.ProgressEvent) error {
	if w == nil {
		return nil
	}
	line := formatProgressEvent(event)
	if line == "" {
		return nil
	}
	_, err := fmt.Fprintln(w, line)
	return err
}

func formatProgressEvent(event model.ProgressEvent) string {
	prefix := "[run]"
	if event.Iteration > 0 {
		prefix = fmt.Sprintf("[iter %d]", event.Iteration)
	}

	switch event.EventType {
	case model.ProgressEventRunStarted:
		return prefix + " started"
	case model.ProgressEventRunCompleted:
		line := prefix + " completed"
		if branch, _ := event.Detail["branch"].(string); strings.TrimSpace(branch) != "" {
			line += " branch=" + branch
		}
		return line
	case model.ProgressEventRunFailed:
		if strings.TrimSpace(event.Summary) == "" {
			return prefix + " failed"
		}
		return prefix + " failed: " + strings.TrimSpace(event.Summary)
	case model.ProgressEventRunBlocked:
		if strings.TrimSpace(event.Summary) == "" {
			return prefix + " blocked"
		}
		return prefix + " blocked: " + strings.TrimSpace(event.Summary)
	default:
		if strings.TrimSpace(event.Summary) == "" {
			return ""
		}
		return prefix + " " + strings.TrimSpace(event.Summary)
	}
}

func isTerminalCLIProgressEvent(eventType model.ProgressEventType) bool {
	switch eventType {
	case model.ProgressEventRunCompleted, model.ProgressEventRunFailed, model.ProgressEventRunBlocked:
		return true
	default:
		return false
	}
}

func printJSONTo(w io.Writer, v any) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

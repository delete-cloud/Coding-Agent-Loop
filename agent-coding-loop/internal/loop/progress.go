package loop

import (
	"context"
	"strings"
	"time"

	"github.com/kina/agent-coding-loop/internal/model"
)

func (e *Engine) emitProgress(
	ctx context.Context,
	runID string,
	iteration int,
	eventType model.ProgressEventType,
	status model.ProgressStatus,
	summary string,
	detail map[string]any,
) {
	if e == nil || e.store == nil || strings.TrimSpace(runID) == "" {
		return
	}
	summary = sanitizeProgressSummary(summary)
	if summary == "" {
		return
	}
	if detail == nil {
		detail = map[string]any{}
	}
	_ = e.store.InsertProgressEvent(ctx, model.ProgressEvent{
		RunID:     runID,
		Iteration: iteration,
		EventType: eventType,
		Status:    status,
		Summary:   summary,
		Detail:    detail,
		CreatedAt: time.Now().UnixMilli(),
	})
}

func sanitizeProgressSummary(summary string) string {
	summary = strings.ReplaceAll(summary, "\r", " ")
	summary = strings.ReplaceAll(summary, "\n", " ")
	return strings.TrimSpace(summary)
}

func progressCommandKind(commands model.CommandSet, command string) string {
	switch {
	case containsString(commands.Test, command):
		return "test"
	case containsString(commands.Lint, command):
		return "lint"
	case containsString(commands.Build, command):
		return "build"
	default:
		return "custom"
	}
}

func containsString(items []string, want string) bool {
	for _, item := range items {
		if item == want {
			return true
		}
	}
	return false
}

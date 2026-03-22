package agent

import (
	"context"
	"strings"
)

type PromptCallRecord struct {
	Tool         string
	Path         string
	Status       string
	SystemPrompt string
	UserPrompt   string
	RawResponse  string
	ErrorText    string
}

type promptCallRecorderKey struct{}

type PromptCallRecorder func(context.Context, PromptCallRecord)

func WithPromptCallRecorder(ctx context.Context, recorder PromptCallRecorder) context.Context {
	if recorder == nil {
		return ctx
	}
	return context.WithValue(ctx, promptCallRecorderKey{}, recorder)
}

func emitPromptCall(ctx context.Context, rec PromptCallRecord) {
	rec.Tool = strings.TrimSpace(rec.Tool)
	rec.Path = strings.TrimSpace(rec.Path)
	rec.Status = strings.TrimSpace(rec.Status)
	if rec.Tool == "" || rec.Status == "" {
		return
	}
	recorder, _ := ctx.Value(promptCallRecorderKey{}).(PromptCallRecorder)
	if recorder != nil {
		recorder(ctx, rec)
	}
}

func emitPromptStarted(ctx context.Context, toolName, path, systemPrompt, userPrompt string) {
	emitPromptCall(ctx, PromptCallRecord{
		Tool:         toolName,
		Path:         path,
		Status:       "started",
		SystemPrompt: systemPrompt,
		UserPrompt:   userPrompt,
	})
}

func emitPromptCompleted(ctx context.Context, toolName, path, systemPrompt, userPrompt, rawResponse string) {
	emitPromptCall(ctx, PromptCallRecord{
		Tool:         toolName,
		Path:         path,
		Status:       "completed",
		SystemPrompt: systemPrompt,
		UserPrompt:   userPrompt,
		RawResponse:  rawResponse,
	})
}

func emitPromptError(ctx context.Context, toolName, path, systemPrompt, userPrompt, rawResponse string, err error) {
	rec := PromptCallRecord{
		Tool:         toolName,
		Path:         path,
		Status:       "error",
		SystemPrompt: systemPrompt,
		UserPrompt:   userPrompt,
		RawResponse:  rawResponse,
	}
	if err != nil {
		rec.ErrorText = err.Error()
	}
	emitPromptCall(ctx, rec)
}

package service

import (
	"context"

	"github.com/kina/agent-coding-loop/internal/model"
)

func (s *Service) RunWithProgress(ctx context.Context, spec model.RunSpec) (string, <-chan model.RunResult, error) {
	if err := spec.Validate(); err != nil {
		return "", nil, err
	}
	runID, err := s.store.CreateRun(ctx, spec, model.RunStatusQueued)
	if err != nil {
		return "", nil, err
	}
	resultCh := make(chan model.RunResult, 1)
	go func() {
		defer close(resultCh)
		result, runErr := s.engine.RunWithID(context.Background(), runID, spec)
		if runErr != nil {
			result = model.RunResult{RunID: runID, Status: model.RunStatusFailed, Summary: runErr.Error()}
		}
		resultCh <- result
	}()
	return runID, resultCh, nil
}

func (s *Service) GetProgressEventsAfter(ctx context.Context, runID string, afterID int64, limit int) ([]model.ProgressEvent, error) {
	return s.store.ListProgressEventsAfter(ctx, runID, afterID, limit)
}

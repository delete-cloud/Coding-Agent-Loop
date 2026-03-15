package sqlite

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/kina/agent-coding-loop/internal/model"
)

const (
	defaultProgressListLimit = 100
	maxProgressListLimit     = 500
)

func (s *Store) InsertProgressEvent(ctx context.Context, ev model.ProgressEvent) error {
	if err := ev.Validate(); err != nil {
		return err
	}

	detailJSON := "{}"
	if ev.Detail != nil {
		payload, err := json.Marshal(ev.Detail)
		if err != nil {
			return fmt.Errorf("marshal progress detail: %w", err)
		}
		detailJSON = string(payload)
	}

	sql := fmt.Sprintf(
		"INSERT INTO progress_events (run_id, iteration, event_type, status, summary, detail_json, created_at) VALUES (%s,%d,%s,%s,%s,%s,%d);",
		q(ev.RunID), ev.Iteration, q(string(ev.EventType)), q(string(ev.Status)), q(ev.Summary), q(detailJSON), ev.CreatedAt,
	)
	_, _, err := s.run(ctx, sql)
	return err
}

func (s *Store) ListProgressEventsAfter(ctx context.Context, runID string, afterID int64, limit int) ([]model.ProgressEvent, error) {
	if stringsTrimmedEmpty(runID) {
		return nil, fmt.Errorf("run_id is required")
	}
	if afterID < 0 {
		afterID = 0
	}
	switch {
	case limit <= 0:
		limit = defaultProgressListLimit
	case limit > maxProgressListLimit:
		limit = maxProgressListLimit
	}

	rows, err := s.query(ctx, fmt.Sprintf(
		"SELECT id, run_id, iteration, event_type, status, summary, detail_json, created_at FROM progress_events WHERE run_id=%s AND id>%d ORDER BY id ASC LIMIT %d;",
		q(runID), afterID, limit,
	))
	if err != nil {
		return nil, err
	}

	events := make([]model.ProgressEvent, 0, len(rows))
	for _, row := range rows {
		if len(row) < 8 {
			return nil, fmt.Errorf("progress event row parse failed: expected 8 columns, got %d", len(row))
		}
		var detail map[string]any
		if row[6] != "" {
			if err := json.Unmarshal([]byte(row[6]), &detail); err != nil {
				return nil, fmt.Errorf("unmarshal progress detail: %w", err)
			}
		}
		if detail == nil {
			detail = map[string]any{}
		}
		events = append(events, model.ProgressEvent{
			ID:        parseInt64(row[0]),
			RunID:     row[1],
			Iteration: int(parseInt64(row[2])),
			EventType: model.ProgressEventType(row[3]),
			Status:    model.ProgressStatus(row[4]),
			Summary:   row[5],
			Detail:    detail,
			CreatedAt: parseInt64(row[7]),
		})
	}
	return events, nil
}

func stringsTrimmedEmpty(v string) bool {
	return len(v) == 0
}

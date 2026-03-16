package httpapi

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/kina/agent-coding-loop/internal/model"
)

const (
	defaultProgressLimit    = 100
	streamPollInterval      = 250 * time.Millisecond
	streamKeepAliveInterval = 15 * time.Second
)

func (s *Server) handleRunProgress(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	afterID, err := parseAfterID(r)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	limit, err := parseLimit(r)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	events, err := s.svc.GetProgressEventsAfter(r.Context(), runID, afterID, limit)
	if err != nil {
		writeErr(w, http.StatusNotFound, err.Error())
		return
	}
	nextAfterID := afterID
	if len(events) > 0 {
		nextAfterID = events[len(events)-1].ID
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"run_id":        runID,
		"events":        events,
		"next_after_id": nextAfterID,
	})
}

func (s *Server) handleRunStream(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	// Verify run exists before opening the stream.
	if _, err := s.svc.GetRun(r.Context(), runID); err != nil {
		writeErr(w, http.StatusNotFound, err.Error())
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeErr(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}

	afterID, err := parseStreamCursor(r)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	pollTicker := time.NewTicker(streamPollInterval)
	defer pollTicker.Stop()
	keepAliveTicker := time.NewTicker(streamKeepAliveInterval)
	defer keepAliveTicker.Stop()

	// Check run status as fallback every ~5 seconds (20 poll cycles) in case
	// the terminal progress event failed to persist.
	pollCount := 0
	const runStatusCheckInterval = 20

	for {
		events, err := s.svc.GetProgressEventsAfter(r.Context(), runID, afterID, defaultProgressLimit)
		if err != nil {
			return
		}
		if len(events) > 0 {
			for _, event := range events {
				if err := writeSSEProgressEvent(w, event); err != nil {
					return
				}
				flusher.Flush()
				afterID = event.ID
				if isTerminalProgressEvent(event.EventType) {
					return
				}
			}
		}

		// Fallback: if no terminal progress event, periodically check whether
		// the run itself has reached a terminal status.
		pollCount++
		if pollCount%runStatusCheckInterval == 0 {
			if run, err := s.svc.GetRun(r.Context(), runID); err == nil {
				if isTerminalRunStatus(run.Status) {
					// Flush any last events that arrived between polls.
					remaining, _ := s.svc.GetProgressEventsAfter(r.Context(), runID, afterID, defaultProgressLimit)
					for _, event := range remaining {
						_ = writeSSEProgressEvent(w, event)
						flusher.Flush()
					}
					return
				}
			}
		}

		select {
		case <-r.Context().Done():
			return
		case <-keepAliveTicker.C:
			if _, err := fmt.Fprint(w, ": keepalive\n\n"); err != nil {
				return
			}
			flusher.Flush()
		case <-pollTicker.C:
		}
	}
}

func isTerminalRunStatus(status string) bool {
	switch model.RunStatus(status) {
	case model.RunStatusCompleted, model.RunStatusFailed, model.RunStatusBlocked:
		return true
	default:
		return false
	}
}

func parseAfterID(r *http.Request) (int64, error) {
	raw := strings.TrimSpace(r.URL.Query().Get("after_id"))
	if raw == "" {
		return 0, nil
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid after_id: %w", err)
	}
	if value < 0 {
		return 0, fmt.Errorf("after_id must be >= 0")
	}
	return value, nil
}

func parseLimit(r *http.Request) (int, error) {
	raw := strings.TrimSpace(r.URL.Query().Get("limit"))
	if raw == "" {
		return defaultProgressLimit, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return 0, fmt.Errorf("invalid limit: %w", err)
	}
	if value <= 0 {
		return 0, fmt.Errorf("limit must be > 0")
	}
	return value, nil
}

func parseStreamCursor(r *http.Request) (int64, error) {
	if raw := strings.TrimSpace(r.Header.Get("Last-Event-ID")); raw != "" {
		value, err := strconv.ParseInt(raw, 10, 64)
		if err != nil {
			return 0, fmt.Errorf("invalid Last-Event-ID: %w", err)
		}
		if value < 0 {
			return 0, fmt.Errorf("Last-Event-ID must be >= 0")
		}
		return value, nil
	}
	return parseAfterID(r)
}

func writeSSEProgressEvent(w http.ResponseWriter, event model.ProgressEvent) error {
	payload, err := json.Marshal(event)
	if err != nil {
		return err
	}
	if _, err := fmt.Fprintf(w, "id: %d\n", event.ID); err != nil {
		return err
	}
	if _, err := fmt.Fprint(w, "event: progress\n"); err != nil {
		return err
	}
	if _, err := fmt.Fprintf(w, "data: %s\n\n", payload); err != nil {
		return err
	}
	return nil
}

func isTerminalProgressEvent(eventType model.ProgressEventType) bool {
	switch eventType {
	case model.ProgressEventRunCompleted, model.ProgressEventRunFailed, model.ProgressEventRunBlocked:
		return true
	default:
		return false
	}
}

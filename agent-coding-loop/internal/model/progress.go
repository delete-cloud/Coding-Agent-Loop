package model

import "fmt"

type ProgressEventType string

const (
	ProgressEventRunStarted        ProgressEventType = "run_started"
	ProgressEventIterationStarted  ProgressEventType = "iteration_started"
	ProgressEventCoderGenerating   ProgressEventType = "coder_generating"
	ProgressEventPatchApplying     ProgressEventType = "patch_applying"
	ProgressEventPatchFailed       ProgressEventType = "patch_failed"
	ProgressEventCommandRunning    ProgressEventType = "command_running"
	ProgressEventReviewerReviewing ProgressEventType = "reviewer_reviewing"
	ProgressEventIterationComplete ProgressEventType = "iteration_completed"
	ProgressEventRunCompleted      ProgressEventType = "run_completed"
	ProgressEventRunFailed         ProgressEventType = "run_failed"
	ProgressEventRunBlocked        ProgressEventType = "run_blocked"
)

func (t ProgressEventType) Validate() error {
	switch t {
	case ProgressEventRunStarted,
		ProgressEventIterationStarted,
		ProgressEventCoderGenerating,
		ProgressEventPatchApplying,
		ProgressEventPatchFailed,
		ProgressEventCommandRunning,
		ProgressEventReviewerReviewing,
		ProgressEventIterationComplete,
		ProgressEventRunCompleted,
		ProgressEventRunFailed,
		ProgressEventRunBlocked:
		return nil
	default:
		return fmt.Errorf("invalid progress event type: %s", t)
	}
}

type ProgressStatus string

const (
	ProgressStatusStarted   ProgressStatus = "started"
	ProgressStatusProgress  ProgressStatus = "progress"
	ProgressStatusCompleted ProgressStatus = "completed"
	ProgressStatusError     ProgressStatus = "error"
)

func (s ProgressStatus) Validate() error {
	switch s {
	case ProgressStatusStarted,
		ProgressStatusProgress,
		ProgressStatusCompleted,
		ProgressStatusError:
		return nil
	default:
		return fmt.Errorf("invalid progress status: %s", s)
	}
}

type ProgressEvent struct {
	ID        int64             `json:"id"`
	RunID     string            `json:"run_id"`
	Iteration int               `json:"iteration"`
	EventType ProgressEventType `json:"event_type"`
	Status    ProgressStatus    `json:"status"`
	Summary   string            `json:"summary"`
	Detail    map[string]any    `json:"detail"`
	CreatedAt int64             `json:"created_at"`
}

func (e ProgressEvent) Validate() error {
	if e.RunID == "" {
		return fmt.Errorf("progress event run_id is required")
	}
	if err := e.EventType.Validate(); err != nil {
		return err
	}
	if err := e.Status.Validate(); err != nil {
		return err
	}
	if e.Iteration < 0 {
		return fmt.Errorf("progress event iteration must be >= 0")
	}
	if e.Summary == "" {
		return fmt.Errorf("progress event summary is required")
	}
	if e.CreatedAt <= 0 {
		return fmt.Errorf("progress event created_at must be > 0")
	}
	return nil
}

package model

import "testing"

func TestProgressEventTypeValidate(t *testing.T) {
	valid := []ProgressEventType{
		ProgressEventRunStarted,
		ProgressEventRunBlocked,
	}
	for _, eventType := range valid {
		if err := eventType.Validate(); err != nil {
			t.Fatalf("Validate(%q): %v", eventType, err)
		}
	}

	if err := ProgressEventType("unknown").Validate(); err == nil {
		t.Fatal("expected unknown progress event type to fail validation")
	}
}

func TestProgressStatusValidate(t *testing.T) {
	valid := []ProgressStatus{
		ProgressStatusStarted,
		ProgressStatusProgress,
		ProgressStatusCompleted,
		ProgressStatusError,
	}
	for _, status := range valid {
		if err := status.Validate(); err != nil {
			t.Fatalf("Validate(%q): %v", status, err)
		}
	}

	if err := ProgressStatus("unknown").Validate(); err == nil {
		t.Fatal("expected unknown progress status to fail validation")
	}
}

func TestParseRetrievalMode(t *testing.T) {
	mode, err := ParseRetrievalMode("prefetch")
	if err != nil {
		t.Fatalf("ParseRetrievalMode(prefetch): %v", err)
	}
	if mode != RetrievalModePrefetch {
		t.Fatalf("expected prefetch, got %q", mode)
	}

	mode, err = ParseRetrievalMode("off")
	if err != nil {
		t.Fatalf("ParseRetrievalMode(off): %v", err)
	}
	if mode != RetrievalModeOff {
		t.Fatalf("expected off, got %q", mode)
	}
}

func TestRunSpecValidateAcceptsRetrievalMode(t *testing.T) {
	spec := RunSpec{
		Goal:          "demo",
		PRMode:        PRModeDryRun,
		RetrievalMode: RetrievalModePrefetch,
		MaxIterations: 1,
	}
	if err := spec.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
}

func TestRunSpecValidateRejectsInvalidRetrievalMode(t *testing.T) {
	spec := RunSpec{
		Goal:          "demo",
		PRMode:        PRModeDryRun,
		RetrievalMode: RetrievalMode("bogus"),
		MaxIterations: 1,
	}
	if err := spec.Validate(); err == nil {
		t.Fatal("expected invalid retrieval_mode error")
	}
}

func TestParsePlanMode(t *testing.T) {
	cases := []struct {
		input string
		want  PlanMode
	}{
		{"on", PlanModeOn},
		{"off", PlanModeOff},
		{"", PlanModeOn},
		{"true", PlanModeOn},
		{"false", PlanModeOff},
		{"1", PlanModeOn},
		{"0", PlanModeOff},
		{"enabled", PlanModeOn},
		{"disabled", PlanModeOff},
	}
	for _, tc := range cases {
		got, err := ParsePlanMode(tc.input)
		if err != nil {
			t.Fatalf("ParsePlanMode(%q): %v", tc.input, err)
		}
		if got != tc.want {
			t.Fatalf("ParsePlanMode(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
	if _, err := ParsePlanMode("bogus"); err == nil {
		t.Fatal("expected error for invalid plan mode")
	}
}

func TestRunSpecValidateAcceptsPlanMode(t *testing.T) {
	spec := RunSpec{
		Goal:          "demo",
		PRMode:        PRModeDryRun,
		PlanMode:      PlanModeOff,
		MaxIterations: 1,
	}
	if err := spec.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
}

func TestRunSpecNormalizeDefaultsPlanModeOn(t *testing.T) {
	spec := RunSpec{Goal: "demo", MaxIterations: 1}
	spec.Normalize()
	if spec.PlanMode != PlanModeOn {
		t.Fatalf("expected default plan_mode=on, got %q", spec.PlanMode)
	}
}

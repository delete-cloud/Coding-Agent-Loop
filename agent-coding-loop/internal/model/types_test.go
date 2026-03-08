package model

import "testing"

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

package agent

import (
	"context"
	"testing"
)

func TestReviewerFallbackRequestsChangesOnFail(t *testing.T) {
	r := NewReviewer(ClientConfig{})
	out, err := r.Review(context.Background(), ReviewInput{Diff: "", CommandOutput: "FAIL\n---"})
	if err != nil {
		t.Fatalf("Review: %v", err)
	}
	if out.Decision != "request_changes" {
		t.Fatalf("expected request_changes, got %s", out.Decision)
	}
}

func TestReviewerFallbackApprovesOnPass(t *testing.T) {
	r := NewReviewer(ClientConfig{})
	out, err := r.Review(context.Background(), ReviewInput{Diff: "x", CommandOutput: "PASS"})
	if err != nil {
		t.Fatalf("Review: %v", err)
	}
	if out.Decision != "approve" {
		t.Fatalf("expected approve, got %s", out.Decision)
	}
}

func TestCoderFallback(t *testing.T) {
	c := NewCoder(ClientConfig{})
	out, err := c.Generate(context.Background(), CoderInput{Goal: "demo", PreviousReview: "fix"})
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if out.Summary == "" {
		t.Fatal("expected summary")
	}
}

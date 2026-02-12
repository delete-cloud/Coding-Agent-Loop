package loop

import "testing"

func TestDoomLoopDetector(t *testing.T) {
	d := NewDoomLoopDetector(3)
	if d.Observe("run_command", "go test ./...") {
		t.Fatal("unexpected blocked on first call")
	}
	if d.Observe("run_command", "go test ./...") {
		t.Fatal("unexpected blocked on second call")
	}
	if !d.Observe("run_command", "go test ./...") {
		t.Fatal("expected blocked on third identical call")
	}
}

func TestDoomLoopDetectorResetsOnDifferentInput(t *testing.T) {
	d := NewDoomLoopDetector(3)
	d.Observe("run_command", "go test ./...")
	d.Observe("run_command", "go test ./...")
	if d.Observe("run_command", "go test ./cmd/...") {
		t.Fatal("different input should reset sequence")
	}
}

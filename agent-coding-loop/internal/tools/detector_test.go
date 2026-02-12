package tools

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/kina/agent-coding-loop/internal/model"
)

func TestResolveCommandsPrefersExplicit(t *testing.T) {
	dir := t.TempDir()
	spec := model.RunSpec{Commands: model.CommandSet{Test: []string{"pytest -q"}}}
	set, err := ResolveCommands(spec, dir)
	if err != nil {
		t.Fatalf("ResolveCommands: %v", err)
	}
	if len(set.Test) != 1 || set.Test[0] != "pytest -q" {
		t.Fatalf("unexpected explicit commands: %+v", set.Test)
	}
}

func TestResolveCommandsDetectGo(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "go.mod"), []byte("module x"), 0o644); err != nil {
		t.Fatalf("write go.mod: %v", err)
	}
	set, err := ResolveCommands(model.RunSpec{}, dir)
	if err != nil {
		t.Fatalf("ResolveCommands: %v", err)
	}
	if len(set.Test) == 0 || set.Test[0] != "go test ./..." {
		t.Fatalf("expected go test command, got %+v", set.Test)
	}
}

func TestResolveCommandsDetectNode(t *testing.T) {
	dir := t.TempDir()
	pkg := `{"name":"x","scripts":{"test":"vitest run","lint":"eslint ."}}`
	if err := os.WriteFile(filepath.Join(dir, "package.json"), []byte(pkg), 0o644); err != nil {
		t.Fatalf("write package.json: %v", err)
	}
	set, err := ResolveCommands(model.RunSpec{}, dir)
	if err != nil {
		t.Fatalf("ResolveCommands: %v", err)
	}
	if len(set.Test) == 0 || set.Test[0] != "npm test" {
		t.Fatalf("expected npm test, got %+v", set.Test)
	}
}

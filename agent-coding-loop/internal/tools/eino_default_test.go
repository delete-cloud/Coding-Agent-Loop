package tools

import "testing"

func TestBuildCoderToolsAvailableInDefaultBuild(t *testing.T) {
	t.Parallel()

	got, err := BuildCoderTools(t.TempDir(), nil, NewRunner())
	if err != nil {
		t.Fatalf("BuildCoderTools: %v", err)
	}
	if len(got) == 0 {
		t.Fatal("expected coder tools in default build")
	}
}

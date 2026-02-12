package skills

import (
	"os"
	"path/filepath"
	"testing"
)

func TestParseSkillContent(t *testing.T) {
	content := `---
name: test-skill
description: test description
---
# Title

## Instructions
Do something
`

	meta, body, err := ParseSkillContent([]byte(content))
	if err != nil {
		t.Fatalf("ParseSkillContent returned err: %v", err)
	}
	if meta.Name != "test-skill" {
		t.Fatalf("unexpected name: %s", meta.Name)
	}
	if meta.Description != "test description" {
		t.Fatalf("unexpected description: %s", meta.Description)
	}
	if body == "" {
		t.Fatal("expected non-empty body")
	}
}

func TestParseSkillContentRejectsMissingDescription(t *testing.T) {
	content := `---
name: test-skill
---
# Title
`
	_, _, err := ParseSkillContent([]byte(content))
	if err == nil {
		t.Fatal("expected error for missing description")
	}
}

func TestExtractTOCAndSection(t *testing.T) {
	body := "# A\n\n## B\ntext\n\n## C\nmore"
	toc := ExtractTOC(body)
	if toc == "" {
		t.Fatal("expected toc")
	}
	section := ExtractSection(body, "B")
	if section == "" {
		t.Fatal("expected section")
	}
}

func TestRegistryLoadMetadata(t *testing.T) {
	dir := t.TempDir()
	skillDir := filepath.Join(dir, "demo")
	if err := os.MkdirAll(skillDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(skillDir, "SKILL.md"), []byte(`---
name: demo
description: demo desc
---
# Demo
`), 0o644); err != nil {
		t.Fatalf("write skill: %v", err)
	}

	r := NewRegistry([]string{dir})
	if err := r.Load(); err != nil {
		t.Fatalf("Load: %v", err)
	}
	all := r.List()
	if len(all) != 1 {
		t.Fatalf("expected 1 skill, got %d", len(all))
	}
}

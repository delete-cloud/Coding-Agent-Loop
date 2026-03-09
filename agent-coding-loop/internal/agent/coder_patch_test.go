package agent

import (
	"strings"
	"testing"
)

func TestDecodeCoderOutputExtractsPatchFromFencedDiffBlock(t *testing.T) {
	content := "{\n" +
		"  \"summary\": \"update readme\",\n" +
		"  \"patch\": \"I updated the README.\\n```diff\\n--- a/README.md\\n+++ b/README.md\\n@@\\n-foo\\n+bar\\n```\\nRun tests after applying.\",\n" +
		"  \"commands\": [\"go test ./...\"]\n" +
		"}"

	out, err := decodeCoderOutput(content)
	if err != nil {
		t.Fatalf("decodeCoderOutput: %v", err)
	}
	if strings.Contains(out.Patch, "I updated the README") || strings.Contains(out.Patch, "Run tests after applying") {
		t.Fatalf("expected prose stripped from patch, got %q", out.Patch)
	}
	if !strings.HasPrefix(out.Patch, "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@") {
		t.Fatalf("expected normalized diff header, got %q", out.Patch)
	}
}

func TestDecodeCoderOutputExtractsPatchFromInlineDiffWithoutFence(t *testing.T) {
	content := "{\n" +
		"  \"summary\": \"update config\",\n" +
		"  \"patch\": \"Patch only below:\\n--- a/internal/config/config.go\\n+++ b/internal/config/config.go\\n@@\\n if cfg.Artifacts == \\\"\\\" {\\n \\tcfg.Artifacts = \\\".agent-loop-artifacts\\\"\\n }\\n+if cfg.DBPath == \\\"\\\" {\\n+\\tcfg.DBPath = \\\"state.db\\\"\\n+}\\nThanks.\",\n" +
		"  \"commands\": []\n" +
		"}"

	out, err := decodeCoderOutput(content)
	if err != nil {
		t.Fatalf("decodeCoderOutput: %v", err)
	}
	if strings.Contains(out.Patch, "Patch only below") || strings.Contains(out.Patch, "Thanks.") {
		t.Fatalf("expected surrounding prose stripped from patch, got %q", out.Patch)
	}
	if !strings.HasPrefix(out.Patch, "diff --git a/internal/config/config.go b/internal/config/config.go\n--- a/internal/config/config.go\n+++ b/internal/config/config.go\n@@") {
		t.Fatalf("expected normalized diff header, got %q", out.Patch)
	}
}

func TestNormalizeCoderPatchForTargetsAddsHeaderToSingleTargetHunkFragment(t *testing.T) {
	patch := "@@ -1,5 +1,8 @@\n func Load() (*Config, error) {\n \tcfg := &Config{}\n+\tif strings.TrimSpace(cfg.Model.APIKey) != \"\" && strings.TrimSpace(cfg.Model.BaseURL) == \"\" {\n+\t\treturn nil, fmt.Errorf(\"api_key requires base_url\")\n+\t}\n \treturn cfg, nil\n }\n"

	got := normalizeCoderPatchForTargets(patch, []string{"internal/config/config.go"})
	wantPrefix := "diff --git a/internal/config/config.go b/internal/config/config.go\n--- a/internal/config/config.go\n+++ b/internal/config/config.go\n@@ -1,5 +1,8 @@"
	if !strings.HasPrefix(got, wantPrefix) {
		t.Fatalf("expected synthesized single-target headers, got %q", got)
	}
}

func TestNormalizeCoderPatchAddsMissingFileHeadersAfterDiffGit(t *testing.T) {
	patch := "diff --git a/internal/config/config.go b/internal/config/config.go\n@@ -60,9 +60,20 @@ func Load(path string) (*Config, error) {\n+\treturn nil, nil\n }\n diff --git a/internal/config/config_test.go b/internal/config/config_test.go\n @@ -52,3 +52,8 @@ func TestLoadEnvOverrides(t *testing.T) {\n+\tt.Fatalf(\"boom\")\n }\n"

	got := normalizeCoderPatch(patch)
	if !strings.Contains(got, "diff --git a/internal/config/config.go b/internal/config/config.go\n--- a/internal/config/config.go\n+++ b/internal/config/config.go\n@@ -60,9 +60,20 @@") {
		t.Fatalf("expected synthesized headers for first file, got %q", got)
	}
	if !strings.Contains(got, "\ndiff --git a/internal/config/config_test.go b/internal/config/config_test.go\n--- a/internal/config/config_test.go\n+++ b/internal/config/config_test.go\n@@ -52,3 +52,8 @@") {
		t.Fatalf("expected synthesized headers for second file, got %q", got)
	}
}

func TestNormalizeCoderPatchStripsLeadingSpacesFromStructuralLines(t *testing.T) {
	patch := " diff --git a/README.md b/README.md\n index 123..456 100644\n --- a/README.md\n +++ b/README.md\n @@ -1 +1 @@\n -old\n +new\n"

	got := normalizeCoderPatch(patch)
	if strings.Contains(got, "\n diff --git ") || strings.Contains(got, "\n index ") || strings.Contains(got, "\n --- ") || strings.Contains(got, "\n +++ ") || strings.Contains(got, "\n @@ ") {
		t.Fatalf("expected structural lines to be left-aligned, got %q", got)
	}
	if !strings.HasPrefix(got, "diff --git a/README.md b/README.md\nindex 123..456 100644\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@") {
		t.Fatalf("unexpected normalized patch %q", got)
	}
}

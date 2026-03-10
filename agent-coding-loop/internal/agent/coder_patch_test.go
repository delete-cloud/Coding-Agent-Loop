package agent

import (
	"fmt"
	"os"
	"path/filepath"
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
	patch := "@@ -1,4 +1,7 @@\n func Load() (*Config, error) {\n \tcfg := &Config{}\n+\tif strings.TrimSpace(cfg.Model.APIKey) != \"\" && strings.TrimSpace(cfg.Model.BaseURL) == \"\" {\n+\t\treturn nil, fmt.Errorf(\"api_key requires base_url\")\n+\t}\n \treturn cfg, nil\n }\n"

	got := normalizeCoderPatchForTargets(patch, []string{"internal/config/config.go"})
	wantPrefix := "diff --git a/internal/config/config.go b/internal/config/config.go\n--- a/internal/config/config.go\n+++ b/internal/config/config.go\n@@ -1,4 +1,7 @@"
	if !strings.HasPrefix(got, wantPrefix) {
		t.Fatalf("expected synthesized single-target headers, got %q", got)
	}
}

func TestNormalizeCoderPatchAddsMissingFileHeadersAfterDiffGit(t *testing.T) {
	patch := "diff --git a/internal/config/config.go b/internal/config/config.go\n@@ -60,1 +60,2 @@ func Load(path string) (*Config, error) {\n+\treturn nil, nil\n }\n diff --git a/internal/config/config_test.go b/internal/config/config_test.go\n @@ -52,1 +52,2 @@ func TestLoadEnvOverrides(t *testing.T) {\n+\tt.Fatalf(\"boom\")\n }\n"

	got := normalizeCoderPatch(patch)
	if !strings.Contains(got, "diff --git a/internal/config/config.go b/internal/config/config.go\n--- a/internal/config/config.go\n+++ b/internal/config/config.go\n@@ -60,1 +60,2 @@") {
		t.Fatalf("expected synthesized headers for first file, got %q", got)
	}
	if !strings.Contains(got, "\ndiff --git a/internal/config/config_test.go b/internal/config/config_test.go\n--- a/internal/config/config_test.go\n+++ b/internal/config/config_test.go\n@@ -52,1 +52,2 @@") {
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

func TestNormalizeCoderPatchSynthesizesBareHunkHeaderWhenChangesExist(t *testing.T) {
	patch := "diff --git a/README.md b/README.md\nindex d4ff5d2..b0b875c 100644\n--- a/README.md\n+++ b/README.md\n@@\n-Old line\n+New line\n"

	got := normalizeCoderPatch(patch)
	if !strings.Contains(got, "\n@@ -1,1 +1,1 @@\n-Old line\n+New line") {
		t.Fatalf("expected synthesized bare hunk header, got %q", got)
	}
}

func TestNormalizeCoderPatchDropsContextOnlyBareHunk(t *testing.T) {
	patch := "diff --git a/README.md b/README.md\nindex d4ff5d2..b0b875c 100644\n--- a/README.md\n+++ b/README.md\n@@\n - resume        : Resume a stopped workflow run with --run-id <RUN_ID>.\n - inspect       : Print runtime information for a workflow run with --run-id <RUN_ID>.\n"

	got := normalizeCoderPatch(patch)
	if got != "" {
		t.Fatalf("expected context-only malformed hunk to be rejected, got %q", got)
	}
}

func TestNormalizeCoderPatchForTargetsStripsRepoPrefixFromSingleTargetPaths(t *testing.T) {
	patch := "diff --git a/agent-coding-loop/internal/config/config.go b/agent-coding-loop/internal/config/config.go\n--- a/agent-coding-loop/internal/config/config.go\n+++ b/agent-coding-loop/internal/config/config.go\n@@ -1,1 +1,2 @@\n package config\n+// note\n"

	got := normalizeCoderPatchForTargets(patch, []string{"internal/config/config.go"})
	if strings.Contains(got, "agent-coding-loop/internal/config/config.go") {
		t.Fatalf("expected repo prefix stripped, got %q", got)
	}
	want := "diff --git a/internal/config/config.go b/internal/config/config.go\n--- a/internal/config/config.go\n+++ b/internal/config/config.go\n@@ -1,1 +1,2 @@"
	if !strings.HasPrefix(got, want) {
		t.Fatalf("unexpected normalized patch %q", got)
	}
}

func TestNormalizeCoderPatchForTargetsStripsRepoPrefixFromMultiTargetPaths(t *testing.T) {
	patch := "diff --git a/agent-coding-loop/internal/config/config.go b/agent-coding-loop/internal/config/config.go\n--- a/agent-coding-loop/internal/config/config.go\n+++ b/agent-coding-loop/internal/config/config.go\n@@ -1,1 +1,2 @@\n package config\n+// note\n\ndiff --git a/agent-coding-loop/internal/config/config_test.go b/agent-coding-loop/internal/config/config_test.go\n--- a/agent-coding-loop/internal/config/config_test.go\n+++ b/agent-coding-loop/internal/config/config_test.go\n@@ -1,1 +1,2 @@\n package config\n+// test note\n"

	got := normalizeCoderPatchForTargets(patch, []string{"internal/config/config.go", "internal/config/config_test.go"})
	if strings.Contains(got, "agent-coding-loop/internal/config/") {
		t.Fatalf("expected repo prefix stripped for all targets, got %q", got)
	}
	if !strings.Contains(got, "diff --git a/internal/config/config.go b/internal/config/config.go") {
		t.Fatalf("missing normalized config.go path in %q", got)
	}
	if !strings.Contains(got, "diff --git a/internal/config/config_test.go b/internal/config/config_test.go") {
		t.Fatalf("missing normalized config_test.go path in %q", got)
	}
}

func TestNormalizeCoderPatchDropsTruncatedHunkWhenCountsDoNotMatch(t *testing.T) {
	patch := "diff --git a/internal/config/config.go b/internal/config/config.go\nindex 1f5a7d6..a8c4b59 100644\n--- a/internal/config/config.go\n+++ b/internal/config/config.go\n@@ -186,6 +186,12 @@ func Load(cfg Config) (Config, error) {\n \t}\n \n \tif cfg.Model.APIKey != \"\" && cfg.Model.BaseURL == \"\" {\n+\t\treturn cfg, fmt.Errorf(\"api_key requires base_url\")\n+\t}\n+\n+\tif cfg.Model.BaseURL != \"\" {\n+\t\tif !strings.HasPrefix(cfg.Model.BaseURL, \"https://\") && !strings.HasPrefix(cfg.Model.BaseURL, \"http://\") {\n+\t\t\treturn cfg, fmt.Errorf(\"base_url must start with http:// or https://\")\n+\t\t}\n \n \tif err := cfg.Validate(); err != nil {\n \t\treturn cfg, err\n \t}\n"

	got := normalizeCoderPatch(patch)
	if got != "" {
		t.Fatalf("expected truncated hunk to be rejected, got %q", got)
	}
}

func TestNormalizeCoderPatchPrefixesBareHunkContextLines(t *testing.T) {
	patch := "diff --git a/internal/http/server.go b/internal/http/server.go\n--- a/internal/http/server.go\n+++ b/internal/http/server.go\n@@ -7,6 +7,7 @@ import (\n\t\"io\"\n\t\"net/http\"\n\t\"strings\"\n+\t\"unicode\"\n\n\t\"github.com/kina/agent-coding-loop/internal/model\"\n\t\"github.com/kina/agent-coding-loop/internal/service\"\n"

	got := normalizeCoderPatch(patch)
	if got == "" {
		t.Fatal("expected patch to survive normalization")
	}
	if !strings.Contains(got, "\n \t\"io\"\n \t\"net/http\"\n \t\"strings\"\n+\t\"unicode\"\n \n \t\"github.com/kina/agent-coding-loop/internal/model\"") {
		t.Fatalf("expected bare hunk context lines to be prefixed as unified diff context, got %q", got)
	}
}

func TestNormalizeCoderPatchForRepoTargetsRecountsDriftedSingleFileHunk(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "internal", "http", "server.go")
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	content := `package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/service"
)

type Server struct {
	svc *service.Service
}

func NewServer(svc *service.Service) *Server {
	return &Server{svc: svc}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/runs", s.handleRuns)
	mux.HandleFunc("/v1/runs/", s.handleRunByID)
	mux.HandleFunc("/v1/skills", s.handleSkills)
	mux.HandleFunc("/v1/skills/", s.handleSkillByName)
	return mux
}

func (s *Server) ListenAndServe(ctx context.Context, addr string) error {
	httpServer := &http.Server{Addr: addr, Handler: s.Handler()}
	go func() {
		<-ctx.Done()
		_ = httpServer.Shutdown(context.Background())
	}()
	return httpServer.ListenAndServe()
}

func (s *Server) handleRuns(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
}

func writeJSON(w http.ResponseWriter, code int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]any{"error": msg})
}
`
	if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
		t.Fatalf("write server.go: %v", err)
	}
	patch := `diff --git a/internal/http/server.go b/internal/http/server.go
index 0b660e6..e1ec453 100644
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -7,6 +7,7 @@ import (
	"io"
	"net/http"
	"strings"
+	"unicode"

	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/service"
@@ -1,6 +1,27 @@
 func writeJSON(w http.ResponseWriter, code int, payload any) {
 }
 
 func writeErr(w http.ResponseWriter, code int, msg string) {
-	writeJSON(w, code, map[string]any{"error": msg})
+	var codeBuf strings.Builder
+	lastWasUnderscore := false
+	for _, r := range msg {
+		if unicode.IsLetter(r) || unicode.IsDigit(r) {
+			codeBuf.WriteRune(unicode.ToUpper(r))
+			lastWasUnderscore = false
+			continue
+		}
+		if codeBuf.Len() > 0 && !lastWasUnderscore {
+			codeBuf.WriteByte('_')
+			lastWasUnderscore = true
+		}
+	}
+	machineCode := strings.Trim(codeBuf.String(), "_")
+	if machineCode == "" {
+		if fallback := http.StatusText(code); fallback != "" {
+			machineCode = strings.ToUpper(strings.ReplaceAll(fallback, " ", "_"))
+		} else {
+			machineCode = "UNKNOWN_ERROR"
+		}
+	}
+	writeJSON(w, code, map[string]any{"error": msg, "code": machineCode})
 }
`

	normalized := normalizeCoderPatchForTargets(patch, []string{"internal/http/server.go"})
	if normalized == "" {
		t.Fatal("expected target normalization to preserve patch")
	}
	recounted := recountSingleTargetPatchAgainstSnapshot(root, normalized, "internal/http/server.go")
	got := normalizeCoderPatchForRepoTargets(root, patch, []string{"internal/http/server.go"})
	if got != recounted {
		t.Fatalf("expected repo-target normalization to match explicit recount path\nnormalized=%q\nrecounted=%q\ngot=%q", normalized, recounted, got)
	}
	if strings.Contains(got, "@@ -1,6 +1,27 @@") {
		t.Fatalf("expected drifted hunk header to be recounted, got %q", got)
	}
	lines := strings.Split(content, "\n")
	writeErrStart := 0
	for i, line := range lines {
		if line == "func writeErr(w http.ResponseWriter, code int, msg string) {" {
			writeErrStart = i + 1
			break
		}
	}
	if writeErrStart == 0 {
		t.Fatal("failed to locate writeErr in fixture")
	}
	wantHeader := fmt.Sprintf("@@ -%d,5 +%d,26 @@", writeErrStart-2, writeErrStart-1)
	if !strings.Contains(got, wantHeader) {
		t.Fatalf("expected recounted hunk header %q in %q", wantHeader, got)
	}
	if strings.Contains(got, "\n func writeJSON(w http.ResponseWriter, code int, payload any) {\n") {
		t.Fatalf("expected mismatched writeJSON context to be trimmed from recounted hunk, got %q", got)
	}
}

func TestNormalizeCoderPatchForContractRejectsPatchMissingTargetTouch(t *testing.T) {
	patch := `diff --git a/internal/config/other.go b/internal/config/other.go
--- a/internal/config/other.go
+++ b/internal/config/other.go
@@ -1,1 +1,2 @@
 package config
+// note
`

	got := normalizeCoderPatchForContract("", patch, []string{"internal/config/config.go"}, false, false)
	if got != "" {
		t.Fatalf("expected patch without target touch to be rejected, got %q", got)
	}
}

func TestNormalizeCoderPatchForContractRejectsRepoOnlyPatchTouchingNonTargets(t *testing.T) {
	patch := `diff --git a/internal/config/config.go b/internal/config/config.go
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -1,1 +1,2 @@
 package config
+// note
diff --git a/internal/config/other.go b/internal/config/other.go
--- a/internal/config/other.go
+++ b/internal/config/other.go
@@ -1,1 +1,2 @@
 package config
+// other
`

	got := normalizeCoderPatchForContract("", patch, []string{"internal/config/config.go"}, false, true)
	if got != "" {
		t.Fatalf("expected repo-only patch touching non-targets to be rejected, got %q", got)
	}
}

func TestNormalizeCoderPatchForContractRejectsDuplicateThreeLineAddedBlockInSameFile(t *testing.T) {
	patch := `diff --git a/kb/server.py b/kb/server.py
--- a/kb/server.py
+++ b/kb/server.py
@@ -10,2 +10,5 @@
             chunk_size = int(body.get("chunk_size") or 0)
             overlap = int(body.get("overlap") or 0)
+            if chunk_size < 100 or chunk_size > 8192:
+                self._send(400, {"error": "chunk_size must be between 100 and 8192"})
+                return
@@ -22,2 +25,5 @@
             chunk_size = int(body.get("chunk_size") or 0)
             overlap = int(body.get("overlap") or 0)
+            if chunk_size < 100 or chunk_size > 8192:
+                self._send(400, {"error": "chunk_size must be between 100 and 8192"})
+                return
`

	got := normalizeCoderPatchForContract("", patch, []string{"kb/server.py"}, false, false)
	if got != "" {
		t.Fatalf("expected duplicate three-line added block to be rejected, got %q", got)
	}
}

func TestNormalizeCoderPatchForContractRejectsDuplicateTwoLineAddedBlockInSameFile(t *testing.T) {
	patch := `diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,3 @@
 # CLI
+- inspect supports --run-id
+- inspect supports --json
@@ -8,1 +10,3 @@
 ## Notes
+- inspect supports --run-id
+- inspect supports --json
`

	got := normalizeCoderPatchForContract("", patch, []string{"README.md"}, false, false)
	if got != "" {
		t.Fatalf("expected duplicate two-line added block to be rejected, got %q", got)
	}
}

func TestNormalizeCoderPatchForContractAllowsRepeatedSingleLineAddedBlock(t *testing.T) {
	patch := `diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,2 @@
 # CLI
+- inspect
@@ -8,1 +9,2 @@
 ## Notes
+- inspect
`

	got := normalizeCoderPatchForContract("", patch, []string{"README.md"}, false, false)
	if got == "" {
		t.Fatalf("expected repeated single-line additions to remain allowed")
	}
}

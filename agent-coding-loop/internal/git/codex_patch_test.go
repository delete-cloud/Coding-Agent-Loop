package git

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kina/agent-coding-loop/internal/tools"
)

func TestApplyPatchAcceptsBareHunkHeaderWithoutRanges(t *testing.T) {
	repo := t.TempDir()
	r := tools.NewRunner()
	if _, _, err := r.Run(context.Background(), "git init", repo); err != nil {
		t.Fatalf("git init: %v", err)
	}
	_, _, _ = r.Run(context.Background(), "git config user.email test@example.com", repo)
	_, _, _ = r.Run(context.Background(), "git config user.name tester", repo)

	path := filepath.Join(repo, "internal", "http", "server.go")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	orig := `package http

func writeJSON(w any, code int, payload any) {}

func writeErr(w any, code int, msg string) {
	writeJSON(w, code, map[string]any{"error": msg})
}
`
	if err := os.WriteFile(path, []byte(orig), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, _, err := r.Run(context.Background(), "git add -A && git commit -m init", repo); err != nil {
		t.Fatalf("commit: %v", err)
	}

	patch := `--- a/internal/http/server.go
+++ b/internal/http/server.go
@@
 func writeErr(w any, code int, msg string) {
-	writeJSON(w, code, map[string]any{"error": msg})
+	writeJSON(w, code, map[string]any{"error": msg, "code": "NOT_FOUND"})
 }`

	c := NewClient(r)
	if err := c.ApplyPatch(context.Background(), repo, patch); err != nil {
		t.Fatalf("ApplyPatch: %v", err)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if !strings.Contains(string(got), `"code": "NOT_FOUND"`) {
		t.Fatalf("expected patch applied, got %q", string(got))
	}
}

func TestApplyPatchAcceptsMixedPatchWithBareSecondHunkHeader(t *testing.T) {
	repo := t.TempDir()
	r := tools.NewRunner()
	if _, _, err := r.Run(context.Background(), "git init", repo); err != nil {
		t.Fatalf("git init: %v", err)
	}
	_, _, _ = r.Run(context.Background(), "git config user.email test@example.com", repo)
	_, _, _ = r.Run(context.Background(), "git config user.name tester", repo)

	cfgPath := filepath.Join(repo, "internal", "config", "config.go")
	testPath := filepath.Join(repo, "internal", "config", "config_test.go")
	if err := os.MkdirAll(filepath.Dir(cfgPath), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	cfgOrig := `package config

import "fmt"

type Config struct{ DBPath string }

func Load() (*Config, error) {
	cfg := &Config{}
	return cfg, nil
}
`
	testOrig := `package config

import "testing"

func TestLoadEnvOverrides(t *testing.T) {}
`
	if err := os.WriteFile(cfgPath, []byte(cfgOrig), 0o644); err != nil {
		t.Fatalf("write config: %v", err)
	}
	if err := os.WriteFile(testPath, []byte(testOrig), 0o644); err != nil {
		t.Fatalf("write test: %v", err)
	}
	if _, _, err := r.Run(context.Background(), "git add -A && git commit -m init", repo); err != nil {
		t.Fatalf("commit: %v", err)
	}

	patch := `diff --git a/internal/config/config.go b/internal/config/config.go
index 1111111..2222222 100644
--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -5,4 +5,7 @@ type Config struct{ DBPath string }
 func Load() (*Config, error) {
 	cfg := &Config{}
+	if cfg.DBPath != "" && !strings.HasSuffix(cfg.DBPath, ".db") {
+		return nil, fmt.Errorf("db_path must end with .db extension")
+	}
 	return cfg, nil
 }
diff --git a/internal/config/config_test.go b/internal/config/config_test.go
index 3333333..4444444 100644
--- a/internal/config/config_test.go
+++ b/internal/config/config_test.go
@@
-import "testing"
+import (
+	"strings"
+	"testing"
+)
 
 func TestLoadEnvOverrides(t *testing.T) {}
+
+func TestLoadDBPathValidation(t *testing.T) {
+	if !strings.Contains("db_path must end with .db extension", ".db") {
+		t.Fatal("expected suffix")
+	}
+}`

	c := NewClient(r)
	if err := c.ApplyPatch(context.Background(), repo, patch); err != nil {
		t.Fatalf("ApplyPatch: %v", err)
	}
	gotCfg, err := os.ReadFile(cfgPath)
	if err != nil {
		t.Fatalf("read config: %v", err)
	}
	if !strings.Contains(string(gotCfg), "db_path must end with .db extension") {
		t.Fatalf("expected config patch applied, got %q", string(gotCfg))
	}
	gotTest, err := os.ReadFile(testPath)
	if err != nil {
		t.Fatalf("read test: %v", err)
	}
	if !strings.Contains(string(gotTest), "TestLoadDBPathValidation") {
		t.Fatalf("expected test patch applied, got %q", string(gotTest))
	}
}

package git

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kina/agent-coding-loop/internal/tools"
)

func TestApplyPatchRepairsHunkCounts(t *testing.T) {
	repo := t.TempDir()
	r := tools.NewRunner()
	_, _, err := r.Run(context.Background(), "git init", repo)
	if err != nil {
		t.Fatalf("git init: %v", err)
	}
	_, _, _ = r.Run(context.Background(), "git config user.email test@example.com", repo)
	_, _, _ = r.Run(context.Background(), "git config user.name tester", repo)

	if err := os.MkdirAll(filepath.Join(repo, "internal", "config"), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	orig := `package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type Config struct {
	ListenAddr string      ` + "`json:\"listen_addr\"`" + `
	DBPath     string      ` + "`json:\"db_path\"`" + `
	Artifacts  string      ` + "`json:\"artifacts_dir\"`" + `
	Model      ModelConfig ` + "`json:\"model\"`" + `
}

type ModelConfig struct {
	Provider string ` + "`json:\"provider\"`" + `
	BaseURL  string ` + "`json:\"base_url\"`" + `
	Model    string ` + "`json:\"model\"`" + `
	APIKey   string ` + "`json:\"api_key\"`" + `
}

func Load(path string) (*Config, error) {
	cfg := &Config{}
	_ = json.NewDecoder(strings.NewReader("")).Decode(cfg)
	if cfg.ListenAddr == "" {
		cfg.ListenAddr = "127.0.0.1:8787"
	}
	if cfg.DBPath == "" {
		cfg.DBPath = filepath.Join(".agent-loop-artifacts", "state.db")
	}
	if cfg.Artifacts == "" {
		cfg.Artifacts = ".agent-loop-artifacts"
	}
	return cfg, nil
}
`
	path := filepath.Join(repo, "internal", "config", "config.go")
	if err := os.WriteFile(path, []byte(orig), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	_, _, err = r.Run(context.Background(), "git add -A && git commit -m init", repo)
	if err != nil {
		t.Fatalf("commit: %v", err)
	}

	badPatch := `--- a/internal/config/config.go
+++ b/internal/config/config.go
@@ -2,6 +2,7 @@ package config
 
 import (
 	"encoding/json"
+	"errors"
 	"fmt"
 	"os"
 	"path/filepath"
@@ -56,6 +57,12 @@ func Load(path string) (*Config, error) {
 	if cfg.Artifacts == "" {
 		cfg.Artifacts = ".agent-loop-artifacts"
 	}
+	if cfg.Model.BaseURL == "" {
+		return nil, errors.New("model.base_url is required but empty")
+	}
+	if cfg.Model.Model == "" {
+		return nil, errors.New("model.model is required but empty")
+	}
 	return cfg, nil
 }
`

	c := NewClient(r)
	if err := c.ApplyPatch(context.Background(), repo, badPatch); err != nil {
		t.Fatalf("ApplyPatch: %v", err)
	}
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if string(b) == orig {
		t.Fatalf("expected file changed")
	}
}

func TestApplyPatchAddOnlyFallbackOverwritesExistingFile(t *testing.T) {
	repo := t.TempDir()
	r := tools.NewRunner()
	_, _, err := r.Run(context.Background(), "git init", repo)
	if err != nil {
		t.Fatalf("git init: %v", err)
	}
	_, _, _ = r.Run(context.Background(), "git config user.email test@example.com", repo)
	_, _, _ = r.Run(context.Background(), "git config user.name tester", repo)

	target := filepath.Join(repo, "docs", "note.md")
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(target, []byte("old\ncontent\n"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	_, _, err = r.Run(context.Background(), "git add -A && git commit -m init", repo)
	if err != nil {
		t.Fatalf("commit: %v", err)
	}

	patch := `--- a/docs/note.md
+++ b/docs/note.md
@@ -0,0 +1,3 @@
+# New Note
+
+updated content
`

	c := NewClient(r)
	if err := c.ApplyPatch(context.Background(), repo, patch); err != nil {
		t.Fatalf("ApplyPatch fallback: %v", err)
	}
	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	want := "# New Note\n\nupdated content\n"
	if string(got) != want {
		t.Fatalf("unexpected content\nwant:\n%s\ngot:\n%s", want, string(got))
	}
}

func TestParseAddOnlyPatchFiles(t *testing.T) {
	patch := `--- a/docs/implementation-summary-2026-02.md
+++ b/docs/implementation-summary-2026-02.md
@@ -0,0 +1,4 @@
+# Title
+
+line1
+line2`
	files, ok := parseAddOnlyPatchFiles(patch)
	if !ok {
		t.Fatalf("expected add-only parse success")
	}
	if len(files) != 1 {
		t.Fatalf("expected 1 file, got %d", len(files))
	}
	if files[0].Path != "docs/implementation-summary-2026-02.md" {
		t.Fatalf("unexpected path: %s", files[0].Path)
	}
	want := "# Title\n\nline1\nline2\n"
	if files[0].Content != want {
		t.Fatalf("unexpected content\nwant=%q\ngot=%q", want, files[0].Content)
	}
}

func TestApplyPatchAddOnlyFallbackSubdirRepo(t *testing.T) {
	root := t.TempDir()
	r := tools.NewRunner()
	_, _, err := r.Run(context.Background(), "git init", root)
	if err != nil {
		t.Fatalf("git init: %v", err)
	}
	_, _, _ = r.Run(context.Background(), "git config user.email test@example.com", root)
	_, _, _ = r.Run(context.Background(), "git config user.name tester", root)

	repo := filepath.Join(root, "agent-coding-loop")
	target := filepath.Join(repo, "docs", "implementation-summary-2026-02.md")
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(target, []byte("existing\n"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	_, _, err = r.Run(context.Background(), "git add -A && git commit -m init", root)
	if err != nil {
		t.Fatalf("commit: %v", err)
	}

	patch := `--- a/docs/implementation-summary-2026-02.md
+++ b/docs/implementation-summary-2026-02.md
@@ -0,0 +1,3 @@
+# Implementation Summary - February 2026
+
+Interview Talking Points`

	c := NewClient(r)
	if err := c.ApplyPatch(context.Background(), repo, patch); err != nil {
		t.Fatalf("ApplyPatch subdir fallback: %v", err)
	}
	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	want := "# Implementation Summary - February 2026\n\nInterview Talking Points\n"
	if string(got) != want {
		t.Fatalf("unexpected content\nwant:\n%s\ngot:\n%s", want, string(got))
	}
}

func TestApplyControlledRewritePatch(t *testing.T) {
	repo := t.TempDir()
	target := filepath.Join(repo, "docs", "sample.md")
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	orig := "alpha\nbeta\ngamma\n"
	if err := os.WriteFile(target, []byte(orig), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	patch := `--- a/docs/sample.md
+++ b/docs/sample.md
@@ -99,3 +99,3 @@
 alpha
-beta
+BETA
 gamma
`
	if err := applyControlledRewritePatch(repo, patch); err != nil {
		t.Fatalf("applyControlledRewritePatch: %v", err)
	}
	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	want := "alpha\nBETA\ngamma\n"
	if string(got) != want {
		t.Fatalf("unexpected content\nwant:\n%s\ngot:\n%s", want, string(got))
	}
}

func TestApplyControlledRewritePatchRejectsPathEscape(t *testing.T) {
	repo := t.TempDir()
	patch := `--- a/../../etc/passwd
+++ b/../../etc/passwd
@@ -1,1 +1,1 @@
-x
+y
`
	if err := applyControlledRewritePatch(repo, patch); err == nil {
		t.Fatalf("expected path escape to fail")
	}
}

func TestApplyPatchSubdirRepoDiffGitPatchPathRewrite(t *testing.T) {
	root := t.TempDir()
	r := tools.NewRunner()
	_, _, err := r.Run(context.Background(), "git init", root)
	if err != nil {
		t.Fatalf("git init: %v", err)
	}
	_, _, _ = r.Run(context.Background(), "git config user.email test@example.com", root)
	_, _, _ = r.Run(context.Background(), "git config user.name tester", root)

	repo := filepath.Join(root, "agent-coding-loop")
	target := filepath.Join(repo, "internal", "http", "server.go")
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	orig := `package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
)

func writeErr(w http.ResponseWriter, code int, msg string) {
	_ = context.TODO()
	_ = json.NewEncoder(nil)
	_ = fmt.Sprintf("")
	_ = io.EOF
	_ = strings.TrimSpace(msg)
	_ = code
}
`
	if err := os.WriteFile(target, []byte(orig), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	_, _, err = r.Run(context.Background(), "git add -A && git commit -m init", root)
	if err != nil {
		t.Fatalf("commit: %v", err)
	}

	patch1 := `--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -4,6 +4,7 @@ import (
 	"context"
 	"encoding/json"
 	"fmt"
+	"fmt"
 	"io"
 	"net/http"
 	"strings"
`
	patch2 := `diff --git a/internal/http/server.go b/internal/http/server.go
index b488a8f..c8e5c4e 100644
--- a/internal/http/server.go
+++ b/internal/http/server.go
@@ -4,7 +4,6 @@ import (
 	"context"
 	"encoding/json"
 	"fmt"
-	"fmt"
 	"io"
 	"net/http"
 	"strings"
`

	c := NewClient(r)
	if err := c.ApplyPatch(context.Background(), repo, patch1); err != nil {
		t.Fatalf("apply patch1: %v", err)
	}
	got1, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read after patch1: %v", err)
	}
	if strings.Count(string(got1), "\"fmt\"") != 2 {
		t.Fatalf("expected duplicated fmt import after patch1, got:\n%s", string(got1))
	}
	if err := c.ApplyPatch(context.Background(), repo, patch2); err != nil {
		t.Fatalf("apply patch2: %v", err)
	}
	got2, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read after patch2: %v", err)
	}
	if strings.Count(string(got2), "\"fmt\"") != 1 {
		t.Fatalf("expected single fmt import after patch2, got:\n%s", string(got2))
	}
}

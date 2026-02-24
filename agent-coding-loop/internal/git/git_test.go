package git

import (
	"context"
	"os"
	"path/filepath"
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


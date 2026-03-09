package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadDefaults(t *testing.T) {
	cfg, err := Load("")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.ListenAddr != "127.0.0.1:8787" {
		t.Fatalf("unexpected listen addr: %s", cfg.ListenAddr)
	}
	if cfg.DBPath == "" {
		t.Fatal("expected db path")
	}
}

func TestLoadJSONFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	data := `{"listen_addr":"127.0.0.1:9999","db_path":"/tmp/x.db"}`
	if err := os.WriteFile(path, []byte(data), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.ListenAddr != "127.0.0.1:9999" {
		t.Fatalf("unexpected listen addr: %s", cfg.ListenAddr)
	}
	if cfg.DBPath != "/tmp/x.db" {
		t.Fatalf("unexpected db path: %s", cfg.DBPath)
	}
}

func TestLoadEnvOverrides(t *testing.T) {
	t.Setenv("AGENT_LOOP_LISTEN", "127.0.0.1:3333")
	t.Setenv("AGENT_LOOP_DB_PATH", "/tmp/y.db")
	cfg, err := Load("")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.ListenAddr != "127.0.0.1:3333" {
		t.Fatalf("unexpected listen addr: %s", cfg.ListenAddr)
	}
	if cfg.DBPath != "/tmp/y.db" {
		t.Fatalf("unexpected db path: %s", cfg.DBPath)
	}
}

func TestLoadFallsBackToAnthropicAuthTokenForAPIKey(t *testing.T) {
	t.Setenv("OPENAI_BASE_URL", "https://right.codes/claude/v1")
	t.Setenv("OPENAI_MODEL", "claude-haiku-4-5")
	t.Setenv("OPENAI_API_KEY", "")
	t.Setenv("ANTHROPIC_AUTH_TOKEN", "anthropic-secret")

	cfg, err := Load("")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Model.APIKey != "anthropic-secret" {
		t.Fatalf("expected anthropic auth token fallback, got %q", cfg.Model.APIKey)
	}
}

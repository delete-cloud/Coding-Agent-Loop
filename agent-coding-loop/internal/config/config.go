package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type Config struct {
	ListenAddr string      `json:"listen_addr"`
	DBPath     string      `json:"db_path"`
	Artifacts  string      `json:"artifacts_dir"`
	Model      ModelConfig `json:"model"`
	KB         KBConfig    `json:"kb"`
}

type ModelConfig struct {
	Provider     string `json:"provider"`
	BaseURL      string `json:"base_url"`
	Model        string `json:"model"`
	APIKey       string `json:"api_key"`
	ResponsesAPI bool   `json:"responses_api"`
}

type KBConfig struct {
	BaseURL string `json:"base_url"`
}

func Load(path string) (*Config, error) {
	cfg := &Config{
		ListenAddr: "127.0.0.1:8787",
		DBPath:     filepath.Join(".agent-loop-artifacts", "state.db"),
		Artifacts:  ".agent-loop-artifacts",
		Model: ModelConfig{
			Provider: "openai-compatible",
			BaseURL:  strings.TrimRight(os.Getenv("OPENAI_BASE_URL"), "/"),
			Model:    os.Getenv("OPENAI_MODEL"),
			APIKey:   firstNonEmptyEnv("OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
		},
		KB: KBConfig{
			BaseURL: strings.TrimRight(os.Getenv("AGENT_LOOP_KB_URL"), "/"),
		},
	}
	if strings.TrimSpace(cfg.KB.BaseURL) == "" {
		cfg.KB.BaseURL = "http://127.0.0.1:8788"
	}
	if path != "" {
		if err := loadFile(path, cfg); err != nil {
			return nil, err
		}
	}
	overrideFromEnv(cfg)
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

func loadFile(path string, cfg *Config) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read config: %w", err)
	}
	ext := strings.ToLower(filepath.Ext(path))
	switch ext {
	case ".json":
		if err := json.Unmarshal(b, cfg); err != nil {
			return fmt.Errorf("parse config json: %w", err)
		}
	default:
		if err := parseSimpleYAML(string(b), cfg); err != nil {
			return fmt.Errorf("parse config yaml: %w", err)
		}
	}
	return nil
}

func parseSimpleYAML(raw string, cfg *Config) error {
	for _, line := range strings.Split(raw, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		parts := strings.SplitN(trimmed, ":", 2)
		if len(parts) != 2 {
			continue
		}
		k := strings.TrimSpace(parts[0])
		v := strings.Trim(strings.TrimSpace(parts[1]), `"'`)
		switch k {
		case "listen_addr":
			cfg.ListenAddr = v
		case "db_path":
			cfg.DBPath = v
		case "artifacts_dir":
			cfg.Artifacts = v
		case "provider":
			cfg.Model.Provider = v
		case "base_url":
			cfg.Model.BaseURL = strings.TrimRight(v, "/")
		case "model":
			cfg.Model.Model = v
		case "api_key":
			cfg.Model.APIKey = v
		case "responses_api":
			cfg.Model.ResponsesAPI = v == "true" || v == "1" || v == "yes"
		case "kb_base_url", "kb_url":
			cfg.KB.BaseURL = strings.TrimRight(v, "/")
		}
	}
	return nil
}

func overrideFromEnv(cfg *Config) {
	if v := strings.TrimSpace(os.Getenv("AGENT_LOOP_LISTEN")); v != "" {
		cfg.ListenAddr = v
	}
	if v := strings.TrimSpace(os.Getenv("AGENT_LOOP_DB_PATH")); v != "" {
		cfg.DBPath = v
	}
	if v := strings.TrimSpace(os.Getenv("AGENT_LOOP_ARTIFACTS_DIR")); v != "" {
		cfg.Artifacts = v
	}
	if v := strings.TrimSpace(os.Getenv("AGENT_LOOP_MODEL_PROVIDER")); v != "" {
		cfg.Model.Provider = v
	}
	if v := strings.TrimSpace(os.Getenv("OPENAI_BASE_URL")); v != "" {
		cfg.Model.BaseURL = strings.TrimRight(v, "/")
	}
	if v := strings.TrimSpace(os.Getenv("OPENAI_MODEL")); v != "" {
		cfg.Model.Model = v
	}
	if v := strings.TrimSpace(firstNonEmptyEnv("OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN")); v != "" {
		cfg.Model.APIKey = v
	}
	if v := strings.TrimSpace(os.Getenv("OPENAI_RESPONSES_API")); v != "" {
		cfg.Model.ResponsesAPI = v == "true" || v == "1" || v == "yes"
	}
	if v := strings.TrimSpace(os.Getenv("AGENT_LOOP_KB_URL")); v != "" {
		cfg.KB.BaseURL = strings.TrimRight(v, "/")
	}
}

func firstNonEmptyEnv(keys ...string) string {
	for _, key := range keys {
		if v := strings.TrimSpace(os.Getenv(key)); v != "" {
			return v
		}
	}
	return ""
}

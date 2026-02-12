package tools

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/kina/agent-coding-loop/internal/model"
)

func ResolveCommands(spec model.RunSpec, repo string) (model.CommandSet, error) {
	if strings.TrimSpace(repo) == "" {
		return model.CommandSet{}, fmt.Errorf("repo is required")
	}
	if len(spec.Commands.Test) > 0 || len(spec.Commands.Lint) > 0 || len(spec.Commands.Build) > 0 {
		return spec.Commands, nil
	}
	out := model.CommandSet{}
	if exists(filepath.Join(repo, "go.mod")) {
		out.Test = []string{"go test ./..."}
		out.Build = []string{"go build ./..."}
		return out, nil
	}
	if exists(filepath.Join(repo, "package.json")) {
		out.Test = []string{"npm test"}
		pkg, err := parsePackageJSON(filepath.Join(repo, "package.json"))
		if err == nil {
			if pkg.Scripts["lint"] != "" {
				out.Lint = []string{"npm run lint"}
			}
			if pkg.Scripts["build"] != "" {
				out.Build = []string{"npm run build"}
			}
		}
		return out, nil
	}
	if exists(filepath.Join(repo, "pyproject.toml")) || exists(filepath.Join(repo, "requirements.txt")) {
		out.Test = []string{"pytest -q"}
		return out, nil
	}
	return out, nil
}

type packageJSON struct {
	Scripts map[string]string `json:"scripts"`
}

func parsePackageJSON(path string) (*packageJSON, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var p packageJSON
	if err := json.Unmarshal(b, &p); err != nil {
		return nil, err
	}
	if p.Scripts == nil {
		p.Scripts = map[string]string{}
	}
	return &p, nil
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

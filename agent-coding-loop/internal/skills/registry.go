package skills

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

type Registry struct {
	mu    sync.RWMutex
	dirs  []string
	meta  []SkillMeta
	cache map[string]string
}

func NewRegistry(dirs []string) *Registry {
	return &Registry{dirs: dirs, cache: map[string]string{}}
}

func DefaultSearchPaths(repoRoot string) []string {
	paths := make([]string, 0, 3)
	if home, err := os.UserHomeDir(); err == nil {
		paths = append(paths, filepath.Join(home, ".codex", "skills", "superpowers", "skills"))
	}
	if v := strings.TrimSpace(os.Getenv("CODEX_HOME")); v != "" {
		paths = append(paths, filepath.Join(v, "skills", "superpowers", "skills"))
	}
	if strings.TrimSpace(repoRoot) != "" {
		paths = append(paths, filepath.Join(repoRoot, ".codex", "skills"))
	}
	return paths
}

func (r *Registry) Load() error {
	meta := make([]SkillMeta, 0)
	for _, root := range r.dirs {
		entries, err := os.ReadDir(root)
		if err != nil {
			continue
		}
		for _, e := range entries {
			if !e.IsDir() {
				continue
			}
			skillPath := filepath.Join(root, e.Name(), "SKILL.md")
			b, err := os.ReadFile(skillPath)
			if err != nil {
				continue
			}
			m, _, err := ParseSkillContent(b)
			if err != nil {
				continue
			}
			m.Path = filepath.Dir(skillPath)
			meta = append(meta, m)
		}
	}
	sort.Slice(meta, func(i, j int) bool { return meta[i].Name < meta[j].Name })
	r.mu.Lock()
	r.meta = meta
	r.cache = map[string]string{}
	r.mu.Unlock()
	return nil
}

func (r *Registry) List() []SkillMeta {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]SkillMeta, len(r.meta))
	copy(out, r.meta)
	return out
}

func (r *Registry) Get(name string) (SkillMeta, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	for _, m := range r.meta {
		if m.Name == name {
			return m, true
		}
	}
	return SkillMeta{}, false
}

func (r *Registry) LoadContent(name string) (string, bool, error) {
	r.mu.RLock()
	if c, ok := r.cache[name]; ok {
		r.mu.RUnlock()
		return c, true, nil
	}
	var path string
	for _, m := range r.meta {
		if m.Name == name {
			path = filepath.Join(m.Path, "SKILL.md")
			break
		}
	}
	r.mu.RUnlock()
	if path == "" {
		return "", false, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return "", true, err
	}
	_, body, err := ParseSkillContent(b)
	if err != nil {
		return "", true, err
	}
	r.mu.Lock()
	r.cache[name] = body
	r.mu.Unlock()
	return body, true, nil
}

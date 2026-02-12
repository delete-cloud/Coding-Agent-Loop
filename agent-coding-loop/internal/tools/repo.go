package tools

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func RepoList(repoRoot, rel string) ([]string, error) {
	base, err := securePath(repoRoot, rel)
	if err != nil {
		return nil, err
	}
	entries := make([]string, 0, 64)
	err = filepath.WalkDir(base, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if d.IsDir() {
			name := d.Name()
			if name == ".git" || name == ".worktrees" || name == "worktrees" {
				return filepath.SkipDir
			}
			return nil
		}
		relPath, err := filepath.Rel(repoRoot, path)
		if err != nil {
			return err
		}
		entries = append(entries, relPath)
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Strings(entries)
	return entries, nil
}

func RepoRead(repoRoot, rel string, maxBytes int) (string, error) {
	path, err := securePath(repoRoot, rel)
	if err != nil {
		return "", err
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	if maxBytes > 0 && len(b) > maxBytes {
		b = b[:maxBytes]
	}
	return string(b), nil
}

func RepoSearch(repoRoot, needle string) ([]string, error) {
	if strings.TrimSpace(needle) == "" {
		return nil, fmt.Errorf("needle is required")
	}
	files, err := RepoList(repoRoot, ".")
	if err != nil {
		return nil, err
	}
	matches := make([]string, 0, 32)
	for _, rel := range files {
		content, err := RepoRead(repoRoot, rel, 1024*1024)
		if err != nil {
			continue
		}
		if strings.Contains(content, needle) {
			matches = append(matches, rel)
		}
	}
	return matches, nil
}

func securePath(repoRoot, rel string) (string, error) {
	root, err := filepath.Abs(repoRoot)
	if err != nil {
		return "", err
	}
	target := filepath.Join(root, rel)
	clean, err := filepath.Abs(filepath.Clean(target))
	if err != nil {
		return "", err
	}
	if !strings.HasPrefix(clean, root) {
		return "", fmt.Errorf("path escapes repo root")
	}
	return clean, nil
}

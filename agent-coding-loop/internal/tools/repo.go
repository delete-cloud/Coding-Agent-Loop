package tools

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func RepoList(repoRoot, rel string) ([]string, error) {
	rel = normalizeRelPath(repoRoot, rel)
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
	rel = normalizeRelPath(repoRoot, rel)
	path, err := securePath(repoRoot, rel)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(path)
	if err != nil {
		return "", err
	}
	if info.IsDir() {
		items, err := os.ReadDir(path)
		if err != nil {
			return "", err
		}
		lines := make([]string, 0, len(items)+1)
		lines = append(lines, fmt.Sprintf("%s is a directory. Use one of these relative paths:", rel))
		for _, it := range items {
			name := it.Name()
			if it.IsDir() {
				name += "/"
			}
			if rel == "." {
				lines = append(lines, name)
				continue
			}
			lines = append(lines, filepath.ToSlash(filepath.Join(rel, name)))
		}
		sort.Strings(lines[1:])
		out := strings.Join(lines, "\n")
		if maxBytes > 0 && len(out) > maxBytes {
			out = out[:maxBytes]
		}
		return out, nil
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
	if clean != root && !strings.HasPrefix(clean, root+string(os.PathSeparator)) {
		return "", fmt.Errorf("path escapes repo root")
	}
	return clean, nil
}

func normalizeRelPath(repoRoot, rel string) string {
	rel = strings.TrimSpace(strings.ReplaceAll(rel, "\\", "/"))
	if rel == "" {
		return "."
	}
	rel = strings.TrimLeft(rel, "`'\"([{<")
	rel = strings.TrimRight(rel, "`'\"`)]}>,;:!?，。；：！、")
	rel = strings.TrimSpace(rel)
	if rel == "" {
		return "."
	}
	rootBase := filepath.Base(filepath.Clean(strings.TrimSpace(repoRoot)))
	if rootBase != "" && rootBase != "." {
		rel = stripEmbeddedRepoPrefix(rel, rootBase)
	}
	for strings.HasPrefix(rel, string(os.PathSeparator)) {
		rel = strings.TrimPrefix(rel, string(os.PathSeparator))
	}
	clean := filepath.Clean(filepath.FromSlash(rel))
	if rootBase != "" && rootBase != "." {
		prefix := rootBase + string(os.PathSeparator)
		for clean == rootBase || strings.HasPrefix(clean, prefix) {
			if clean == rootBase {
				return "."
			}
			clean = strings.TrimPrefix(clean, prefix)
		}
	}
	if clean == "" {
		return "."
	}
	return clean
}

func stripEmbeddedRepoPrefix(rel string, repoBase string) string {
	trimmed := strings.TrimSpace(strings.ReplaceAll(rel, "\\", "/"))
	if trimmed == "" || repoBase == "" || repoBase == "." {
		return trimmed
	}
	segments := strings.Split(strings.TrimLeft(trimmed, "/"), "/")
	for i, seg := range segments {
		if seg != repoBase {
			continue
		}
		if i == len(segments)-1 {
			return "."
		}
		return strings.Join(segments[i+1:], "/")
	}
	return trimmed
}

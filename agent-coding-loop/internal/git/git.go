package git

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/kina/agent-coding-loop/internal/tools"
)

type Client struct {
	runner *tools.Runner
}

func NewClient(r *tools.Runner) *Client {
	return &Client{runner: r}
}

func (c *Client) EnsureRepo(ctx context.Context, repo string) error {
	_, _, err := c.runner.Run(ctx, "git rev-parse --git-dir", repo)
	return err
}

func (c *Client) CurrentBranch(ctx context.Context, repo string) (string, error) {
	stdout, _, err := c.runner.Run(ctx, "git rev-parse --abbrev-ref HEAD", repo)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(stdout), nil
}

func (c *Client) RemoteURL(ctx context.Context, repo string) (string, error) {
	stdout, _, err := c.runner.Run(ctx, "git config --get remote.origin.url", repo)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(stdout), nil
}

func (c *Client) CreateFeatureBranch(ctx context.Context, repo string) (string, error) {
	branch := fmt.Sprintf("agent-loop/%d", time.Now().Unix())
	_, _, err := c.runner.Run(ctx, "git checkout -b "+branch, repo)
	if err != nil {
		return "", err
	}
	return branch, nil
}

func (c *Client) CheckoutBranch(ctx context.Context, repo, branch string) error {
	_, _, err := c.runner.Run(ctx, "git checkout "+shellQuote(branch), repo)
	return err
}

func (c *Client) Diff(ctx context.Context, repo string) (string, error) {
	stdout, _, err := c.runner.Run(ctx, "git diff -- .", repo)
	if err != nil {
		return "", err
	}
	return stdout, nil
}

func (c *Client) StatusShort(ctx context.Context, repo string) (string, error) {
	stdout, _, err := c.runner.Run(ctx, "git status --short --untracked-files=all", repo)
	if err != nil {
		return "", err
	}
	return stdout, nil
}

func (c *Client) ApplyPatch(ctx context.Context, repo, patch string) error {
	patchPath := filepath.Join(repo, ".agent-loop-last.patch")
	patchRepoRelative := normalizeUnifiedDiffForRepo(repo, patch)
	gitRepo := repo
	repoPrefix := ""
	if top, prefix, err := c.gitTopAndPrefix(ctx, repo); err == nil && strings.TrimSpace(top) != "" {
		gitRepo = strings.TrimSpace(top)
		repoPrefix = strings.TrimSpace(prefix)
	}
	patchForGit := rewriteUnifiedDiffPathsForGitRoot(repo, repoPrefix, patchRepoRelative)
	if err := os.WriteFile(patchPath, []byte(patchForGit), 0o644); err != nil {
		return err
	}
	_, stderr, err := c.runner.Run(ctx, "git apply "+shellQuote(patchPath), gitRepo)
	if err == nil {
		return nil
	}
	firstErr := err
	firstStderr := strings.TrimSpace(stderr)

	_, stderrRecount, errRecount := c.runner.Run(ctx, "git apply --recount "+shellQuote(patchPath), gitRepo)
	if errRecount == nil {
		return nil
	}

	_, stderr3way, err3way := c.runner.Run(ctx, "git apply --3way "+shellQuote(patchPath), gitRepo)
	if err3way == nil {
		return nil
	}

	// Narrow fallback: many LLM patches are add-only @@ -0,0 hunks against files that already exist.
	// If parsing succeeds, materialize the add-only content directly.
	if addOnlyErr := applyAddOnlyPatchFallback(repo, patchRepoRelative); addOnlyErr == nil {
		return nil
	}

	if rewriteErr := applyControlledRewritePatch(repo, patchRepoRelative); rewriteErr == nil {
		return nil
	}

	if firstStderr != "" {
		return fmt.Errorf("%w: %s", firstErr, firstStderr)
	}
	stderrRecount = strings.TrimSpace(stderrRecount)
	if stderrRecount != "" {
		return fmt.Errorf("%w: %s", firstErr, stderrRecount)
	}
	stderr3 := strings.TrimSpace(stderr3way)
	if stderr3 != "" {
		return fmt.Errorf("%w: %s", firstErr, stderr3)
	}
	return firstErr
}

func (c *Client) gitTopAndPrefix(ctx context.Context, repo string) (string, string, error) {
	stdoutTop, _, err := c.runner.Run(ctx, "git rev-parse --show-toplevel", repo)
	if err != nil {
		return "", "", err
	}
	stdoutPrefix, _, err := c.runner.Run(ctx, "git rev-parse --show-prefix", repo)
	if err != nil {
		return strings.TrimSpace(stdoutTop), "", nil
	}
	top := strings.TrimSpace(stdoutTop)
	prefix := filepath.ToSlash(strings.TrimSpace(stdoutPrefix))
	if prefix != "" && !strings.HasSuffix(prefix, "/") {
		prefix += "/"
	}
	return top, prefix, nil
}

func (c *Client) CommitAll(ctx context.Context, repo, message string) (string, error) {
	if _, _, err := c.runner.Run(ctx, "git add -A", repo); err != nil {
		return "", err
	}
	if _, _, err := c.runner.Run(ctx, "git commit -m "+shellQuote(message), repo); err != nil {
		return "", err
	}
	stdout, _, err := c.runner.Run(ctx, "git rev-parse HEAD", repo)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(stdout), nil
}

func (c *Client) CommitPaths(ctx context.Context, repo string, paths []string, message string) (string, error) {
	if len(paths) == 0 {
		return "", nil
	}
	quoted := make([]string, 0, len(paths))
	for _, p := range paths {
		if strings.TrimSpace(p) == "" {
			continue
		}
		quoted = append(quoted, shellQuote(p))
	}
	if len(quoted) == 0 {
		return "", nil
	}
	if _, _, err := c.runner.Run(ctx, "git add -- "+strings.Join(quoted, " "), repo); err != nil {
		return "", err
	}
	if _, _, err := c.runner.Run(ctx, "git commit -m "+shellQuote(message), repo); err != nil {
		return "", err
	}
	stdout, _, err := c.runner.Run(ctx, "git rev-parse HEAD", repo)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(stdout), nil
}

func (c *Client) Push(ctx context.Context, repo, branch string) error {
	_, _, err := c.runner.Run(ctx, "git push -u origin "+shellQuote(branch), repo)
	return err
}

func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "'\\''") + "'"
}

func normalizeUnifiedDiff(patch string) string {
	patch = strings.TrimSpace(patch) + "\n"
	if strings.HasPrefix(strings.TrimSpace(patch), "```") {
		patch = strings.TrimSpace(patch)
		patch = strings.TrimPrefix(patch, "```diff")
		patch = strings.TrimPrefix(patch, "```patch")
		patch = strings.TrimPrefix(patch, "```")
		patch = strings.TrimSuffix(patch, "```")
		patch = strings.TrimSpace(patch) + "\n"
	}
	return fixHunkCounts(patch)
}

func normalizeUnifiedDiffForRepo(repo string, patch string) string {
	patch = normalizeUnifiedDiff(patch)
	return rewriteUnifiedDiffPaths(repo, patch)
}

func rewriteUnifiedDiffPathsForGitRoot(repo, prefix, patch string) string {
	prefix = filepath.ToSlash(strings.TrimSpace(prefix))
	prefix = strings.TrimPrefix(prefix, "./")
	prefix = strings.Trim(prefix, "/")
	if prefix == "" {
		return patch
	}
	repoBase := filepath.Base(repo)
	lines := strings.Split(patch, "\n")
	for i := 0; i < len(lines); i++ {
		line := lines[i]
		if strings.HasPrefix(line, "diff --git ") {
			rest := strings.TrimPrefix(line, "diff --git ")
			parts := strings.SplitN(rest, " ", 2)
			if len(parts) == 2 {
				a := rewriteDiffPathTokenForGitRoot(parts[0], prefix, repoBase)
				b := rewriteDiffPathTokenForGitRoot(parts[1], prefix, repoBase)
				lines[i] = "diff --git " + a + " " + b
			}
			continue
		}
		if strings.HasPrefix(line, "--- ") || strings.HasPrefix(line, "+++ ") {
			head := line[:4]
			p := strings.TrimSpace(line[4:])
			if p == "/dev/null" {
				continue
			}
			lines[i] = head + " " + rewriteDiffPathTokenForGitRoot(p, prefix, repoBase)
			continue
		}
		if strings.HasPrefix(line, "rename from ") {
			p := strings.TrimPrefix(line, "rename from ")
			lines[i] = "rename from " + ensureGitRootRelPath(p, prefix, repoBase)
			continue
		}
		if strings.HasPrefix(line, "rename to ") {
			p := strings.TrimPrefix(line, "rename to ")
			lines[i] = "rename to " + ensureGitRootRelPath(p, prefix, repoBase)
			continue
		}
	}
	return strings.Join(lines, "\n")
}

func rewriteDiffPathTokenForGitRoot(tok string, prefix string, repoBase string) string {
	tok = strings.TrimSpace(tok)
	if tok == "" || tok == "/dev/null" {
		return tok
	}
	switch {
	case strings.HasPrefix(tok, "a/"):
		return "a/" + ensureGitRootRelPath(strings.TrimPrefix(tok, "a/"), prefix, repoBase)
	case strings.HasPrefix(tok, "b/"):
		return "b/" + ensureGitRootRelPath(strings.TrimPrefix(tok, "b/"), prefix, repoBase)
	}
	return ensureGitRootRelPath(tok, prefix, repoBase)
}

func ensureGitRootRelPath(rel string, prefix string, repoBase string) string {
	rel = strings.TrimSpace(strings.ReplaceAll(rel, "\\", "/"))
	rel = strings.TrimPrefix(rel, "./")
	rel = strings.TrimLeft(rel, "/")
	prefix = strings.Trim(prefix, "/")
	if prefix == "" {
		return rel
	}
	pfx := prefix + "/"
	if strings.HasPrefix(rel, pfx) {
		return rel
	}
	if repoBase != "" {
		basePrefix := filepath.ToSlash(strings.Trim(repoBase, "/")) + "/"
		if strings.HasPrefix(rel, basePrefix) {
			rel = strings.TrimPrefix(rel, basePrefix)
		}
	}
	return pfx + rel
}

func rewriteUnifiedDiffPaths(repo string, patch string) string {
	base := filepath.Base(repo)
	if strings.TrimSpace(base) == "" || base == "." || base == string(os.PathSeparator) {
		return patch
	}
	prefix := base + string(os.PathSeparator)
	lines := strings.Split(patch, "\n")
	for i := 0; i < len(lines); i++ {
		line := lines[i]
		if strings.HasPrefix(line, "diff --git ") {
			rest := strings.TrimPrefix(line, "diff --git ")
			parts := strings.SplitN(rest, " ", 2)
			if len(parts) == 2 {
				a := rewriteDiffPathToken(parts[0], prefix)
				b := rewriteDiffPathToken(parts[1], prefix)
				lines[i] = "diff --git " + a + " " + b
			}
			continue
		}
		if strings.HasPrefix(line, "--- ") || strings.HasPrefix(line, "+++ ") {
			head := line[:4]
			p := strings.TrimSpace(line[4:])
			if p == "/dev/null" {
				continue
			}
			lines[i] = head + " " + rewriteDiffPathToken(p, prefix)
			continue
		}
		if strings.HasPrefix(line, "rename from ") {
			p := strings.TrimPrefix(line, "rename from ")
			lines[i] = "rename from " + rewriteRelPath(p, prefix)
			continue
		}
		if strings.HasPrefix(line, "rename to ") {
			p := strings.TrimPrefix(line, "rename to ")
			lines[i] = "rename to " + rewriteRelPath(p, prefix)
			continue
		}
	}
	return strings.Join(lines, "\n")
}

func rewriteDiffPathToken(tok string, prefix string) string {
	tok = strings.TrimSpace(tok)
	if tok == "" {
		return tok
	}
	if strings.HasPrefix(tok, "a/") {
		return "a/" + rewriteRelPath(strings.TrimPrefix(tok, "a/"), prefix)
	}
	if strings.HasPrefix(tok, "b/") {
		return "b/" + rewriteRelPath(strings.TrimPrefix(tok, "b/"), prefix)
	}
	return rewriteRelPath(tok, prefix)
}

func rewriteRelPath(rel string, prefix string) string {
	rel = strings.TrimLeft(rel, string(os.PathSeparator))
	if strings.HasPrefix(rel, prefix) {
		return strings.TrimPrefix(rel, prefix)
	}
	return rel
}

type addOnlyPatchFile struct {
	Path    string
	Content string
}

const (
	controlledRewriteMaxPatchBytes = 256 * 1024
	controlledRewriteMaxFiles      = 12
	controlledRewriteSearchWindow  = 200
)

type unifiedHunk struct {
	OldStart int
	OldCount int
	NewStart int
	NewCount int
	Lines    []string
}

type unifiedFilePatch struct {
	Path  string
	Hunks []unifiedHunk
}

func applyAddOnlyPatchFallback(repo string, patch string) error {
	files, ok := parseAddOnlyPatchFiles(patch)
	if !ok || len(files) == 0 {
		return fmt.Errorf("not an add-only patch")
	}
	repoAbs, err := filepath.Abs(repo)
	if err != nil {
		return err
	}
	for _, file := range files {
		rel := strings.TrimSpace(file.Path)
		if rel == "" {
			return fmt.Errorf("empty file path in add-only patch")
		}
		target := filepath.Clean(filepath.Join(repoAbs, filepath.FromSlash(rel)))
		if target != repoAbs && !strings.HasPrefix(target, repoAbs+string(os.PathSeparator)) {
			return fmt.Errorf("add-only patch path escapes repo: %s", rel)
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(target, []byte(file.Content), 0o644); err != nil {
			return err
		}
	}
	return nil
}

func applyControlledRewritePatch(repo string, patch string) error {
	if strings.TrimSpace(patch) == "" {
		return fmt.Errorf("empty patch")
	}
	if len(patch) > controlledRewriteMaxPatchBytes {
		return fmt.Errorf("patch too large for controlled rewrite")
	}
	files, err := parseUnifiedPatchForRewrite(patch)
	if err != nil {
		return err
	}
	if len(files) == 0 {
		return fmt.Errorf("no rewrite-able file patch")
	}
	if len(files) > controlledRewriteMaxFiles {
		return fmt.Errorf("too many files for controlled rewrite")
	}

	repoAbs, err := filepath.Abs(repo)
	if err != nil {
		return err
	}
	for _, fp := range files {
		target, err := safeJoinRepoPath(repoAbs, fp.Path)
		if err != nil {
			return err
		}
		currentBytes, readErr := os.ReadFile(target)
		if readErr != nil && !os.IsNotExist(readErr) {
			return readErr
		}
		currentLines, hadFinalNewline := splitTextLines(string(currentBytes))
		rewrittenLines, err := applyHunksWithFuzzy(fp.Path, currentLines, fp.Hunks)
		if err != nil {
			return fmt.Errorf("%s: %w", fp.Path, err)
		}
		text := strings.Join(rewrittenLines, "\n")
		if hadFinalNewline || len(rewrittenLines) > 0 {
			text += "\n"
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(target, []byte(text), 0o644); err != nil {
			return err
		}
	}
	return nil
}

func parseUnifiedPatchForRewrite(patch string) ([]unifiedFilePatch, error) {
	lines := strings.Split(strings.ReplaceAll(patch, "\r\n", "\n"), "\n")
	out := make([]unifiedFilePatch, 0, 4)
	i := 0
	for i < len(lines) {
		line := strings.TrimSpace(lines[i])
		if line == "" || strings.HasPrefix(line, "diff --git ") || strings.HasPrefix(line, "index ") {
			i++
			continue
		}
		if line == "GIT binary patch" {
			return nil, fmt.Errorf("binary patch is not supported")
		}
		if !strings.HasPrefix(line, "--- ") {
			i++
			continue
		}
		if i+1 >= len(lines) || !strings.HasPrefix(strings.TrimSpace(lines[i+1]), "+++ ") {
			return nil, fmt.Errorf("invalid unified patch header")
		}
		rel := stripUnifiedDiffPathToken(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(lines[i+1]), "+++ ")))
		if rel == "" {
			return nil, fmt.Errorf("invalid patch path")
		}
		i += 2
		fp := unifiedFilePatch{Path: rel}
		for i < len(lines) {
			cur := lines[i]
			trim := strings.TrimSpace(cur)
			if strings.HasPrefix(trim, "--- ") && i+1 < len(lines) && strings.HasPrefix(strings.TrimSpace(lines[i+1]), "+++ ") {
				break
			}
			if strings.HasPrefix(trim, "diff --git ") {
				break
			}
			if strings.HasPrefix(trim, "index ") || strings.HasPrefix(trim, "new file mode") || strings.HasPrefix(trim, "deleted file mode") {
				i++
				continue
			}
			if isUnifiedHunkHeader(trim) {
				oldStart, oldCount, newStart, newCount, ok := parseHunkHeaderFull(trim)
				if !ok {
					return nil, fmt.Errorf("invalid hunk header")
				}
				h := unifiedHunk{
					OldStart: oldStart,
					OldCount: oldCount,
					NewStart: newStart,
					NewCount: newCount,
					Lines:    make([]string, 0, oldCount+newCount+4),
				}
				i++
				for i < len(lines) {
					body := lines[i]
					bodyTrim := strings.TrimSpace(body)
					if isUnifiedHunkHeader(bodyTrim) {
						break
					}
					if strings.HasPrefix(bodyTrim, "--- ") && i+1 < len(lines) && strings.HasPrefix(strings.TrimSpace(lines[i+1]), "+++ ") {
						break
					}
					if strings.HasPrefix(bodyTrim, "diff --git ") {
						break
					}
					if strings.HasPrefix(body, "\\ No newline at end of file") {
						i++
						continue
					}
					if body == "" {
						if i == len(lines)-1 {
							break
						}
						h.Lines = append(h.Lines, " ")
						i++
						continue
					}
					switch body[0] {
					case ' ', '+', '-':
						h.Lines = append(h.Lines, body)
					default:
						return nil, fmt.Errorf("unsupported hunk line prefix: %q", body[:1])
					}
					i++
				}
				fp.Hunks = append(fp.Hunks, h)
				continue
			}
			if trim == "" {
				i++
				continue
			}
			return nil, fmt.Errorf("unsupported patch line: %s", trim)
		}
		if len(fp.Hunks) == 0 {
			return nil, fmt.Errorf("no hunks for file %s", fp.Path)
		}
		out = append(out, fp)
	}
	return out, nil
}

func applyHunksWithFuzzy(path string, lines []string, hunks []unifiedHunk) ([]string, error) {
	out := make([]string, len(lines))
	copy(out, lines)
	delta := 0
	for _, h := range hunks {
		oldChunk, newChunk := materializeHunkChunks(h.Lines)
		hint := h.OldStart - 1 + delta
		pos, ok := locateHunkPosition(out, h.Lines, oldChunk, hint)
		if !ok {
			if hunkAlreadyApplied(out, newChunk, hint) {
				continue
			}
			if rewritten, applied := applyInsertOnlyMarkdownHunkFallback(path, out, h.Lines); applied {
				out = rewritten
				delta += len(newChunk) - len(oldChunk)
				continue
			}
			if rewritten, applied := applyLeadingWhitespaceNormalizedHunkFallback(path, out, oldChunk, newChunk); applied {
				out = rewritten
				delta += len(newChunk) - len(oldChunk)
				continue
			}
			return nil, fmt.Errorf("cannot locate hunk old_start=%d", h.OldStart)
		}
		out = replaceChunk(out, pos, len(oldChunk), newChunk)
		delta += len(newChunk) - len(oldChunk)
	}
	return out, nil
}

func materializeHunkChunks(lines []string) ([]string, []string) {
	oldChunk := make([]string, 0, len(lines))
	newChunk := make([]string, 0, len(lines))
	for _, line := range lines {
		if line == "" {
			oldChunk = append(oldChunk, "")
			newChunk = append(newChunk, "")
			continue
		}
		switch line[0] {
		case ' ':
			v := line[1:]
			oldChunk = append(oldChunk, v)
			newChunk = append(newChunk, v)
		case '-':
			oldChunk = append(oldChunk, line[1:])
		case '+':
			newChunk = append(newChunk, line[1:])
		}
	}
	return oldChunk, newChunk
}

func locateHunkPosition(lines []string, hunkLines []string, oldChunk []string, hint int) (int, bool) {
	if len(oldChunk) == 0 {
		return clampInt(hint, 0, len(lines)), true
	}
	if hint >= 0 && hint+len(oldChunk) <= len(lines) && chunksEqual(lines[hint:hint+len(oldChunk)], oldChunk) {
		return hint, true
	}
	if hint >= 0 && hint <= len(lines) {
		low := clampInt(hint-controlledRewriteSearchWindow, 0, len(lines))
		high := clampInt(hint+controlledRewriteSearchWindow, 0, len(lines))
		if pos, ok := findNearestChunkPosition(lines, oldChunk, hint, low, high); ok {
			return pos, true
		}
	}
	if pos, ok := findUniqueChunkPosition(lines, oldChunk); ok {
		return pos, true
	}
	return locateHunkPositionByUniqueAnchor(lines, hunkLines, len(oldChunk))
}

func hunkAlreadyApplied(lines []string, newChunk []string, hint int) bool {
	if len(newChunk) == 0 {
		return false
	}
	if hint >= 0 && hint+len(newChunk) <= len(lines) && chunksEqual(lines[hint:hint+len(newChunk)], newChunk) {
		return true
	}
	_, ok := findUniqueChunkPosition(lines, newChunk)
	return ok
}

func findNearestChunkPosition(lines []string, chunk []string, hint int, low int, high int) (int, bool) {
	if len(chunk) == 0 {
		return clampInt(hint, 0, len(lines)), true
	}
	matches := make([]int, 0, 2)
	for pos := low; pos+len(chunk) <= len(lines) && pos <= high; pos++ {
		if chunksEqual(lines[pos:pos+len(chunk)], chunk) {
			matches = append(matches, pos)
		}
	}
	if len(matches) != 1 {
		return 0, false
	}
	return matches[0], true
}

func findUniqueChunkPosition(lines []string, chunk []string) (int, bool) {
	matches := findChunkMatches(lines, chunk)
	if len(matches) != 1 {
		return 0, false
	}
	return matches[0], true
}

func findChunkMatches(lines []string, chunk []string) []int {
	if len(chunk) == 0 {
		return []int{0}
	}
	matches := make([]int, 0, 2)
	for pos := 0; pos+len(chunk) <= len(lines); pos++ {
		if chunksEqual(lines[pos:pos+len(chunk)], chunk) {
			matches = append(matches, pos)
		}
	}
	return matches
}

type hunkContextBlock struct {
	OldOffset int
	Lines     []string
}

func locateHunkPositionByUniqueAnchor(lines []string, hunkLines []string, oldChunkLen int) (int, bool) {
	blocks := buildHunkContextBlocks(hunkLines)
	bestLen := 0
	for _, block := range blocks {
		if len(block.Lines) > bestLen {
			bestLen = len(block.Lines)
		}
	}
	if bestLen == 0 {
		return 0, false
	}
	for blockLen := bestLen; blockLen >= 1; blockLen-- {
		for _, block := range blocks {
			if len(block.Lines) != blockLen {
				continue
			}
			pos, ok := findUniqueChunkPosition(lines, block.Lines)
			if !ok {
				continue
			}
			start := pos - block.OldOffset
			if !hunkContextMatchesAt(lines, hunkLines, start, oldChunkLen) {
				continue
			}
			return start, true
		}
	}
	return 0, false
}

func applyInsertOnlyMarkdownHunkFallback(path string, lines []string, hunkLines []string) ([]string, bool) {
	if !isMarkdownPath(path) || !isInsertOnlyHunk(hunkLines) {
		return nil, false
	}
	anchor := trailingNonEmptyContextBlock(hunkLines)
	if len(anchor) == 0 {
		return nil, false
	}
	pos, ok := findUniqueNormalizedChunkPosition(lines, anchor, normalizeMarkdownContextLine)
	if !ok {
		return nil, false
	}
	added := addedLinesFromHunk(hunkLines)
	if len(added) == 0 {
		return nil, false
	}
	for i := 0; i < trailingBlankContextLineCount(hunkLines); i++ {
		added = append([]string{""}, added...)
	}
	insertPos := pos + len(anchor)
	out := make([]string, 0, len(lines)+len(added))
	out = append(out, lines[:insertPos]...)
	out = append(out, added...)
	out = append(out, lines[insertPos:]...)
	return out, true
}

func buildHunkContextBlocks(hunkLines []string) []hunkContextBlock {
	blocks := make([]hunkContextBlock, 0, 4)
	oldOffset := 0
	for i := 0; i < len(hunkLines); {
		line := hunkLines[i]
		if line == "" {
			line = " "
		}
		if line[0] != ' ' {
			if line[0] == '-' {
				oldOffset++
			}
			i++
			continue
		}
		block := hunkContextBlock{
			OldOffset: oldOffset,
			Lines:     make([]string, 0, 4),
		}
		for i < len(hunkLines) {
			cur := hunkLines[i]
			if cur == "" {
				cur = " "
			}
			if cur[0] != ' ' {
				break
			}
			block.Lines = append(block.Lines, cur[1:])
			oldOffset++
			i++
		}
		if len(block.Lines) > 0 {
			blocks = append(blocks, block)
		}
	}
	return blocks
}

func hunkContextMatchesAt(lines []string, hunkLines []string, start int, oldChunkLen int) bool {
	if start < 0 || start+oldChunkLen > len(lines) {
		return false
	}
	oldOffset := 0
	for _, line := range hunkLines {
		if line == "" {
			line = " "
		}
		switch line[0] {
		case ' ':
			if lines[start+oldOffset] != line[1:] {
				return false
			}
			oldOffset++
		case '-':
			oldOffset++
		case '+':
		default:
			return false
		}
	}
	return true
}

func isInsertOnlyHunk(hunkLines []string) bool {
	hasAddition := false
	for _, line := range hunkLines {
		if line == "" {
			line = " "
		}
		switch line[0] {
		case '-':
			return false
		case '+':
			hasAddition = true
		}
	}
	return hasAddition
}

func trailingNonEmptyContextBlock(hunkLines []string) []string {
	oldChunk, _ := materializeHunkChunks(hunkLines)
	for len(oldChunk) > 0 && strings.TrimSpace(oldChunk[len(oldChunk)-1]) == "" {
		oldChunk = oldChunk[:len(oldChunk)-1]
	}
	if len(oldChunk) == 0 {
		return nil
	}
	start := len(oldChunk) - 1
	for start >= 0 && strings.TrimSpace(oldChunk[start]) != "" {
		start--
	}
	block := make([]string, len(oldChunk[start+1:]))
	copy(block, oldChunk[start+1:])
	return block
}

func trailingBlankContextLineCount(hunkLines []string) int {
	oldChunk, _ := materializeHunkChunks(hunkLines)
	count := 0
	for i := len(oldChunk) - 1; i >= 0; i-- {
		if strings.TrimSpace(oldChunk[i]) != "" {
			break
		}
		count++
	}
	return count
}

func addedLinesFromHunk(hunkLines []string) []string {
	out := make([]string, 0, len(hunkLines))
	for _, line := range hunkLines {
		if line == "" {
			continue
		}
		if line[0] == '+' {
			out = append(out, line[1:])
		}
	}
	return out
}

func findUniqueNormalizedChunkPosition(lines []string, chunk []string, normalize func(string) string) (int, bool) {
	if len(chunk) == 0 {
		return 0, false
	}
	match := -1
	for pos := 0; pos+len(chunk) <= len(lines); pos++ {
		if !chunksEqualNormalized(lines[pos:pos+len(chunk)], chunk, normalize) {
			continue
		}
		if match != -1 {
			return 0, false
		}
		match = pos
	}
	if match == -1 {
		return 0, false
	}
	return match, true
}

func chunksEqualNormalized(a []string, b []string, normalize func(string) string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if normalize(a[i]) != normalize(b[i]) {
			return false
		}
	}
	return true
}

func normalizeMarkdownContextLine(line string) string {
	line = strings.ReplaceAll(line, "`", "")
	return strings.Join(strings.Fields(strings.TrimSpace(line)), " ")
}

func applyLeadingWhitespaceNormalizedHunkFallback(path string, lines []string, oldChunk []string, newChunk []string) ([]string, bool) {
	if !isLeadingWhitespaceNormalizedPath(path) || len(oldChunk) == 0 {
		return nil, false
	}
	pos, ok := findUniqueNormalizedChunkPosition(lines, oldChunk, normalizeLeadingWhitespaceContextLine)
	if !ok {
		return nil, false
	}
	return replaceChunk(lines, pos, len(oldChunk), newChunk), true
}

func normalizeLeadingWhitespaceContextLine(line string) string {
	return strings.TrimLeft(line, " \t")
}

func isLeadingWhitespaceNormalizedPath(path string) bool {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".go", ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".rb", ".rs":
		return true
	default:
		return false
	}
}

func isMarkdownPath(path string) bool {
	return strings.EqualFold(filepath.Ext(path), ".md")
}

func replaceChunk(lines []string, start int, oldLen int, newChunk []string) []string {
	if start < 0 {
		start = 0
	}
	if start > len(lines) {
		start = len(lines)
	}
	end := start + oldLen
	if end > len(lines) {
		end = len(lines)
	}
	out := make([]string, 0, len(lines)-oldLen+len(newChunk))
	out = append(out, lines[:start]...)
	out = append(out, newChunk...)
	out = append(out, lines[end:]...)
	return out
}

func chunksEqual(a []string, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func safeJoinRepoPath(repoAbs, rel string) (string, error) {
	rel = strings.TrimSpace(filepath.ToSlash(rel))
	if rel == "" {
		return "", fmt.Errorf("empty path")
	}
	target := filepath.Clean(filepath.Join(repoAbs, filepath.FromSlash(rel)))
	if target != repoAbs && !strings.HasPrefix(target, repoAbs+string(os.PathSeparator)) {
		return "", fmt.Errorf("path escapes repo: %s", rel)
	}
	return target, nil
}

func splitTextLines(text string) ([]string, bool) {
	normalized := strings.ReplaceAll(text, "\r\n", "\n")
	if normalized == "" {
		return []string{}, false
	}
	hasFinalNewline := strings.HasSuffix(normalized, "\n")
	if hasFinalNewline {
		normalized = strings.TrimSuffix(normalized, "\n")
	}
	if normalized == "" {
		return []string{}, hasFinalNewline
	}
	return strings.Split(normalized, "\n"), hasFinalNewline
}

func clampInt(v, low, high int) int {
	if v < low {
		return low
	}
	if v > high {
		return high
	}
	return v
}

func parseAddOnlyPatchFiles(patch string) ([]addOnlyPatchFile, bool) {
	lines := strings.Split(strings.ReplaceAll(patch, "\r\n", "\n"), "\n")
	out := make([]addOnlyPatchFile, 0, 2)
	i := 0
	for i < len(lines) {
		line := strings.TrimSpace(lines[i])
		if !strings.HasPrefix(line, "--- ") {
			i++
			continue
		}
		if i+1 >= len(lines) || !strings.HasPrefix(strings.TrimSpace(lines[i+1]), "+++ ") {
			return nil, false
		}
		oldTok := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(lines[i]), "--- "))
		newTok := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(lines[i+1]), "+++ "))
		if oldTok == "/dev/null" {
			// Allowed, but we still require added content only.
		}
		rel := stripUnifiedDiffPathToken(newTok)
		if rel == "" {
			return nil, false
		}
		i += 2
		var content strings.Builder
		seenHunk := false
		for i < len(lines) {
			cur := lines[i]
			trim := strings.TrimSpace(cur)
			if strings.HasPrefix(trim, "--- ") && i+1 < len(lines) && strings.HasPrefix(strings.TrimSpace(lines[i+1]), "+++ ") {
				break
			}
			if strings.HasPrefix(trim, "diff --git ") {
				break
			}
			if isUnifiedHunkHeader(trim) {
				oldStart, oldCount, ok := parseOldRange(trim)
				if !ok || oldStart != 0 || oldCount != 0 {
					return nil, false
				}
				seenHunk = true
				i++
				continue
			}
			if strings.HasPrefix(cur, "\\ No newline at end of file") {
				i++
				continue
			}
			if cur == "" {
				i++
				continue
			}
			switch cur[0] {
			case '+':
				content.WriteString(strings.TrimPrefix(cur, "+"))
				content.WriteByte('\n')
			case ' ':
				return nil, false
			case '-':
				return nil, false
			default:
				return nil, false
			}
			i++
		}
		if !seenHunk {
			return nil, false
		}
		out = append(out, addOnlyPatchFile{
			Path:    rel,
			Content: content.String(),
		})
	}
	if len(out) == 0 {
		return nil, false
	}
	return out, true
}

func stripUnifiedDiffPathToken(tok string) string {
	p := strings.TrimSpace(tok)
	if p == "" || p == "/dev/null" {
		return ""
	}
	p = strings.TrimPrefix(p, "a/")
	p = strings.TrimPrefix(p, "b/")
	p = strings.TrimPrefix(p, "./")
	p = strings.ReplaceAll(p, "\\", "/")
	p = filepath.ToSlash(filepath.Clean(p))
	if p == "." || p == "/" || strings.HasPrefix(p, "../") {
		return ""
	}
	return strings.TrimPrefix(p, "/")
}

func parseOldRange(header string) (int, int, bool) {
	if !strings.HasPrefix(header, "@@ -") {
		return 0, 0, false
	}
	rest := strings.TrimPrefix(header, "@@ -")
	parts := strings.SplitN(rest, " +", 2)
	if len(parts) != 2 {
		return 0, 0, false
	}
	start, count, ok := parseRange(parts[0])
	if !ok {
		return 0, 0, false
	}
	return start, count, true
}

func fixHunkCounts(patch string) string {
	lines := strings.Split(patch, "\n")
	out := make([]string, 0, len(lines))
	for i := 0; i < len(lines); i++ {
		line := lines[i]
		oldStart, newStart, suffix, ok := parseHunkHeader(line)
		if !ok {
			out = append(out, line)
			continue
		}
		oldCount, newCount := 0, 0
		j := i + 1
		for ; j < len(lines); j++ {
			l := lines[j]
			if isUnifiedHunkHeader(strings.TrimSpace(l)) || strings.HasPrefix(l, "diff --git ") {
				break
			}
			if strings.HasPrefix(l, "--- ") && j > 0 && strings.HasPrefix(lines[j-1], "diff --git ") {
				break
			}
			if strings.HasPrefix(l, "\\ No newline at end of file") {
				continue
			}
			if l == "" {
				continue
			}
			switch l[0] {
			case ' ':
				oldCount++
				newCount++
			case '-':
				oldCount++
			case '+':
				newCount++
			}
		}
		out = append(out, fmt.Sprintf("@@ -%d,%d +%d,%d @@%s", oldStart, oldCount, newStart, newCount, suffix))
		for k := i + 1; k < j; k++ {
			out = append(out, lines[k])
		}
		i = j - 1
	}
	return strings.Join(out, "\n")
}

func parseHunkHeader(line string) (int, int, string, bool) {
	line = strings.TrimSpace(line)
	if line == "@@" {
		return 1, 1, "", true
	}
	if !strings.HasPrefix(line, "@@ -") {
		return 0, 0, "", false
	}
	rest := strings.TrimPrefix(line, "@@ -")
	parts := strings.SplitN(rest, " +", 2)
	if len(parts) != 2 {
		return 0, 0, "", false
	}
	oldPart := parts[0]
	parts2 := strings.SplitN(parts[1], " @@", 2)
	if len(parts2) != 2 {
		return 0, 0, "", false
	}
	newPart := parts2[0]
	suffix := parts2[1]
	oldStart, _, ok := parseRange(oldPart)
	if !ok {
		return 0, 0, "", false
	}
	newStart, _, ok := parseRange(newPart)
	if !ok {
		return 0, 0, "", false
	}
	return oldStart, newStart, suffix, true
}

func parseHunkHeaderFull(line string) (int, int, int, int, bool) {
	line = strings.TrimSpace(line)
	if line == "@@" {
		return 1, 0, 1, 0, true
	}
	if !strings.HasPrefix(line, "@@ -") {
		return 0, 0, 0, 0, false
	}
	rest := strings.TrimPrefix(line, "@@ -")
	parts := strings.SplitN(rest, " +", 2)
	if len(parts) != 2 {
		return 0, 0, 0, 0, false
	}
	oldPart := parts[0]
	parts2 := strings.SplitN(parts[1], " @@", 2)
	if len(parts2) != 2 {
		return 0, 0, 0, 0, false
	}
	newPart := parts2[0]
	oldStart, oldCount, ok := parseRange(oldPart)
	if !ok {
		return 0, 0, 0, 0, false
	}
	newStart, newCount, ok := parseRange(newPart)
	if !ok {
		return 0, 0, 0, 0, false
	}
	return oldStart, oldCount, newStart, newCount, true
}

func parseRange(s string) (int, int, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, 0, false
	}
	parts := strings.SplitN(s, ",", 2)
	start, err := strconv.Atoi(parts[0])
	if err != nil {
		return 0, 0, false
	}
	count := 1
	if len(parts) == 2 && strings.TrimSpace(parts[1]) != "" {
		n, err := strconv.Atoi(parts[1])
		if err != nil {
			return 0, 0, false
		}
		count = n
	}
	return start, count, true
}

func isUnifiedHunkHeader(line string) bool {
	line = strings.TrimSpace(line)
	return line == "@@" || strings.HasPrefix(line, "@@ -")
}

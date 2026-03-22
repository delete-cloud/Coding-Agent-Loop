package sqlite

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/kina/agent-coding-loop/internal/model"
)

const schemaSQL = `
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  spec_json TEXT NOT NULL,
  status TEXT NOT NULL,
  failure_reason TEXT NOT NULL DEFAULT '',
  branch TEXT NOT NULL DEFAULT '',
  commit_hash TEXT NOT NULL DEFAULT '',
  pr_url TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  iteration INTEGER NOT NULL,
  agent TEXT NOT NULL,
  decision TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  ended_at INTEGER NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  iteration INTEGER NOT NULL,
  tool TEXT NOT NULL,
  input_text TEXT NOT NULL,
  output_text TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  iteration INTEGER NOT NULL,
  decision TEXT NOT NULL,
  summary TEXT NOT NULL,
  findings_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS progress_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  iteration INTEGER NOT NULL DEFAULT 0,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_progress_events_run_id_id
  ON progress_events(run_id, id);
`

const sqliteBusyTimeoutMS = 5000

type Store struct {
	path string
}

type RunRecord struct {
	ID         string
	SpecJSON   string
	Status     string
	FailureReason string
	Branch     string
	CommitHash string
	PRURL      string
	Summary    string
	CreatedAt  int64
	UpdatedAt  int64
}

type StepRecord struct {
	RunID     string
	Iteration int
	Agent     string
	Decision  string
	Status    string
	StartedAt int64
	EndedAt   int64
}

type ToolCallRecord struct {
	RunID     string
	Iteration int
	Tool      string
	Input     string
	Output    string
	Status    string
	CreatedAt int64
}

type ReviewRecord struct {
	RunID        string
	Iteration    int
	Decision     string
	Summary      string
	FindingsJSON string
	CreatedAt    int64
}

type ArtifactRecord struct {
	RunID     string
	Kind      string
	Path      string
	Content   string
	CreatedAt int64
}

type Event struct {
	Type      string `json:"type"`
	Timestamp int64  `json:"timestamp"`
	Summary   string `json:"summary"`
}

func New(path string) (*Store, error) {
	if _, err := exec.LookPath("sqlite3"); err != nil {
		return nil, fmt.Errorf("sqlite3 binary not found: %w", err)
	}
	return &Store{path: path}, nil
}

func (s *Store) Migrate(ctx context.Context) error {
	_, _, err := s.run(ctx, schemaSQL)
	if err != nil {
		return err
	}
	if err := s.ensureRunsFailureReasonColumn(ctx); err != nil {
		return err
	}
	return s.backfillRunFailureReasons(ctx)
}

func (s *Store) CreateRun(ctx context.Context, spec model.RunSpec, status model.RunStatus) (string, error) {
	now := time.Now().UnixMilli()
	runID := newID("run")
	b, _ := json.Marshal(spec)
	sql := fmt.Sprintf(
		"INSERT INTO runs (id, spec_json, status, failure_reason, created_at, updated_at) VALUES (%s,%s,%s,%s,%d,%d);",
		q(runID), q(string(b)), q(string(status)), q(""), now, now,
	)
	_, _, err := s.run(ctx, sql)
	if err != nil {
		return "", err
	}
	return runID, nil
}

func (s *Store) UpdateRunStatus(ctx context.Context, runID string, status model.RunStatus, summary string) error {
	now := time.Now().UnixMilli()
	summary = sanitizeInline(summary)
	failureReason := deriveFailureReason(status, summary)
	sql := fmt.Sprintf("UPDATE runs SET status=%s, failure_reason=%s, summary=%s, updated_at=%d WHERE id=%s;", q(string(status)), q(failureReason), q(summary), now, q(runID))
	_, _, err := s.run(ctx, sql)
	return err
}

func (s *Store) UpdateRunMeta(ctx context.Context, runID, branch, commitHash, prURL string) error {
	now := time.Now().UnixMilli()
	sql := fmt.Sprintf(
		"UPDATE runs SET branch=%s, commit_hash=%s, pr_url=%s, updated_at=%d WHERE id=%s;",
		q(branch), q(commitHash), q(prURL), now, q(runID),
	)
	_, _, err := s.run(ctx, sql)
	return err
}

func (s *Store) GetRun(ctx context.Context, runID string) (RunRecord, error) {
	rows, err := s.query(ctx, "SELECT id, spec_json, status, failure_reason, branch, commit_hash, pr_url, summary, created_at, updated_at FROM runs WHERE id="+q(runID)+" LIMIT 1;")
	if err != nil {
		return RunRecord{}, err
	}
	if len(rows) == 0 {
		return RunRecord{}, fmt.Errorf("run not found: %s", runID)
	}
	r := rows[0]
	if len(r) < 10 {
		return RunRecord{}, fmt.Errorf("run row parse failed: expected 10 columns, got %d", len(r))
	}
	return RunRecord{
		ID:         r[0],
		SpecJSON:   r[1],
		Status:     r[2],
		FailureReason: r[3],
		Branch:     r[4],
		CommitHash: r[5],
		PRURL:      r[6],
		Summary:    r[7],
		CreatedAt:  parseInt64(r[8]),
		UpdatedAt:  parseInt64(r[9]),
	}, nil
}

func (s *Store) ensureRunsFailureReasonColumn(ctx context.Context) error {
	rows, err := s.query(ctx, "PRAGMA table_info(runs);")
	if err != nil {
		return err
	}
	for _, row := range rows {
		if len(row) > 1 && row[1] == "failure_reason" {
			return nil
		}
	}
	_, _, err = s.run(ctx, "ALTER TABLE runs ADD COLUMN failure_reason TEXT NOT NULL DEFAULT '';")
	return err
}

func (s *Store) backfillRunFailureReasons(ctx context.Context) error {
	rows, err := s.query(ctx, "SELECT id, status, summary FROM runs;")
	if err != nil {
		return err
	}
	for _, row := range rows {
		if len(row) < 3 {
			continue
		}
		failureReason := deriveFailureReason(model.RunStatus(strings.TrimSpace(row[1])), sanitizeInline(row[2]))
		sql := fmt.Sprintf("UPDATE runs SET failure_reason=%s WHERE id=%s;", q(failureReason), q(row[0]))
		if _, _, err := s.run(ctx, sql); err != nil {
			return err
		}
	}
	return nil
}

func deriveFailureReason(status model.RunStatus, summary string) string {
	switch status {
	case model.RunStatusFailed, model.RunStatusNeedsChange, model.RunStatusBlocked:
	default:
		return ""
	}
	lower := strings.ToLower(strings.TrimSpace(summary))
	switch {
	case strings.Contains(lower, "patch apply failed"):
		return "patch_apply"
	case strings.Contains(lower, "parse llm json failed"),
		strings.Contains(lower, "parse coder json failed"),
		strings.Contains(lower, "parse reviewer json failed"),
		(strings.Contains(lower, "encode repaired") && strings.Contains(lower, "json failed")):
		return "json_parse"
	case strings.Contains(lower, "doom-loop detected"), strings.Contains(lower, "doom loop"):
		return "doom_loop"
	case strings.Contains(lower, "max iterations"), strings.Contains(lower, "max iteration"):
		return "max_iterations"
	case strings.Contains(lower, "reviewer failed"):
		return "reviewer_error"
	case strings.Contains(lower, "coder failed"):
		return "coder_error"
	default:
		return "unclassified_failure"
	}
}

func sanitizeInline(s string) string {
	s = strings.ReplaceAll(s, "\r", " ")
	s = strings.ReplaceAll(s, "\n", " ")
	s = strings.ReplaceAll(s, "\x1f", " ")
	return strings.TrimSpace(s)
}

func (s *Store) InsertStep(ctx context.Context, rec StepRecord) error {
	sql := fmt.Sprintf(
		"INSERT INTO steps (run_id, iteration, agent, decision, status, started_at, ended_at) VALUES (%s,%d,%s,%s,%s,%d,%d);",
		q(rec.RunID), rec.Iteration, q(rec.Agent), q(rec.Decision), q(rec.Status), rec.StartedAt, rec.EndedAt,
	)
	_, _, err := s.run(ctx, sql)
	return err
}

func (s *Store) InsertToolCall(ctx context.Context, rec ToolCallRecord) error {
	sql := fmt.Sprintf(
		"INSERT INTO tool_calls (run_id, iteration, tool, input_text, output_text, status, created_at) VALUES (%s,%d,%s,%s,%s,%s,%d);",
		q(rec.RunID), rec.Iteration, q(rec.Tool), q(rec.Input), q(rec.Output), q(rec.Status), rec.CreatedAt,
	)
	_, _, err := s.run(ctx, sql)
	return err
}

func (s *Store) InsertReview(ctx context.Context, rec ReviewRecord) error {
	sql := fmt.Sprintf(
		"INSERT INTO reviews (run_id, iteration, decision, summary, findings_json, created_at) VALUES (%s,%d,%s,%s,%s,%d);",
		q(rec.RunID), rec.Iteration, q(rec.Decision), q(rec.Summary), q(rec.FindingsJSON), rec.CreatedAt,
	)
	_, _, err := s.run(ctx, sql)
	return err
}

func (s *Store) InsertArtifact(ctx context.Context, rec ArtifactRecord) error {
	sql := fmt.Sprintf(
		"INSERT INTO artifacts (run_id, kind, path, content, created_at) VALUES (%s,%s,%s,%s,%d);",
		q(rec.RunID), q(rec.Kind), q(rec.Path), q(rec.Content), rec.CreatedAt,
	)
	_, _, err := s.run(ctx, sql)
	return err
}

func (s *Store) GetRunEvents(ctx context.Context, runID string) ([]Event, error) {
	events := make([]Event, 0, 16)
	steps, err := s.query(ctx, "SELECT started_at, agent, decision FROM steps WHERE run_id="+q(runID)+";")
	if err != nil {
		return nil, err
	}
	for _, row := range steps {
		events = append(events, Event{Type: "step", Timestamp: parseInt64(row[0]), Summary: row[1] + ":" + row[2]})
	}
	calls, err := s.query(ctx, "SELECT created_at, tool, status FROM tool_calls WHERE run_id="+q(runID)+";")
	if err != nil {
		return nil, err
	}
	for _, row := range calls {
		events = append(events, Event{Type: "tool", Timestamp: parseInt64(row[0]), Summary: row[1] + ":" + row[2]})
	}
	reviews, err := s.query(ctx, "SELECT created_at, decision, summary FROM reviews WHERE run_id="+q(runID)+";")
	if err != nil {
		return nil, err
	}
	for _, row := range reviews {
		events = append(events, Event{Type: "review", Timestamp: parseInt64(row[0]), Summary: row[1] + ":" + row[2]})
	}
	arts, err := s.query(ctx, "SELECT created_at, kind, path FROM artifacts WHERE run_id="+q(runID)+";")
	if err != nil {
		return nil, err
	}
	for _, row := range arts {
		events = append(events, Event{Type: "artifact", Timestamp: parseInt64(row[0]), Summary: row[1] + ":" + row[2]})
	}
	sort.Slice(events, func(i, j int) bool { return events[i].Timestamp < events[j].Timestamp })
	return events, nil
}

func (s *Store) CountSteps(ctx context.Context, runID string) (int, error) {
	rows, err := s.query(ctx, "SELECT COUNT(1) FROM steps WHERE run_id="+q(runID)+";")
	if err != nil {
		return 0, err
	}
	if len(rows) == 0 || len(rows[0]) == 0 {
		return 0, nil
	}
	return int(parseInt64(rows[0][0])), nil
}

func (s *Store) MaxStepIteration(ctx context.Context, runID string) (int, error) {
	rows, err := s.query(ctx, "SELECT MAX(iteration) FROM steps WHERE run_id="+q(runID)+";")
	if err != nil {
		return 0, err
	}
	if len(rows) == 0 || len(rows[0]) == 0 {
		return 0, nil
	}
	return int(parseInt64(rows[0][0])), nil
}

func (s *Store) run(ctx context.Context, sql string) (string, string, error) {
	cmd := exec.CommandContext(ctx, "sqlite3", "-cmd", fmt.Sprintf(".timeout %d", sqliteBusyTimeoutMS), s.path, sql)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", string(out), fmt.Errorf("sqlite3 failed: %w: %s", err, string(out))
	}
	return string(out), "", nil
}

func (s *Store) query(ctx context.Context, sql string) ([][]string, error) {
	cmd := exec.CommandContext(ctx, "sqlite3", "-cmd", fmt.Sprintf(".timeout %d", sqliteBusyTimeoutMS), "-separator", "\x1f", "-noheader", s.path, sql)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("sqlite3 query failed: %w: %s", err, string(out))
	}
	raw := strings.TrimSpace(string(out))
	if raw == "" {
		return nil, nil
	}
	lines := strings.Split(raw, "\n")
	rows := make([][]string, 0, len(lines))
	for _, line := range lines {
		rows = append(rows, strings.Split(line, "\x1f"))
	}
	return rows, nil
}

func q(v string) string {
	return "'" + strings.ReplaceAll(v, "'", "''") + "'"
}

func parseInt64(v string) int64 {
	out, err := strconv.ParseInt(v, 10, 64)
	if err != nil {
		return 0
	}
	return out
}

func newID(prefix string) string {
	now := time.Now().UnixNano()
	return fmt.Sprintf("%s_%d", prefix, now)
}

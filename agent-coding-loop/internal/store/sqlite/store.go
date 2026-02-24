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
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  spec_json TEXT NOT NULL,
  status TEXT NOT NULL,
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
`

type Store struct {
	path string
}

type RunRecord struct {
	ID         string
	SpecJSON   string
	Status     string
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
	return err
}

func (s *Store) CreateRun(ctx context.Context, spec model.RunSpec, status model.RunStatus) (string, error) {
	now := time.Now().UnixMilli()
	runID := newID("run")
	b, _ := json.Marshal(spec)
	sql := fmt.Sprintf(
		"INSERT INTO runs (id, spec_json, status, created_at, updated_at) VALUES (%s,%s,%s,%d,%d);",
		q(runID), q(string(b)), q(string(status)), now, now,
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
	sql := fmt.Sprintf("UPDATE runs SET status=%s, summary=%s, updated_at=%d WHERE id=%s;", q(string(status)), q(summary), now, q(runID))
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
	rows, err := s.query(ctx, "SELECT id, spec_json, status, branch, commit_hash, pr_url, summary, created_at, updated_at FROM runs WHERE id="+q(runID)+" LIMIT 1;")
	if err != nil {
		return RunRecord{}, err
	}
	if len(rows) == 0 {
		return RunRecord{}, fmt.Errorf("run not found: %s", runID)
	}
	r := rows[0]
	if len(r) < 9 {
		return RunRecord{}, fmt.Errorf("run row parse failed: expected 9 columns, got %d", len(r))
	}
	return RunRecord{
		ID:         r[0],
		SpecJSON:   r[1],
		Status:     r[2],
		Branch:     r[3],
		CommitHash: r[4],
		PRURL:      r[5],
		Summary:    r[6],
		CreatedAt:  parseInt64(r[7]),
		UpdatedAt:  parseInt64(r[8]),
	}, nil
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
	cmd := exec.CommandContext(ctx, "sqlite3", s.path, sql)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", string(out), fmt.Errorf("sqlite3 failed: %w: %s", err, string(out))
	}
	return string(out), "", nil
}

func (s *Store) query(ctx context.Context, sql string) ([][]string, error) {
	cmd := exec.CommandContext(ctx, "sqlite3", "-separator", "\x1f", "-noheader", s.path, sql)
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

package service

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	agentpkg "github.com/kina/agent-coding-loop/internal/agent"
	"github.com/kina/agent-coding-loop/internal/config"
	gitpkg "github.com/kina/agent-coding-loop/internal/git"
	ghpkg "github.com/kina/agent-coding-loop/internal/github"
	"github.com/kina/agent-coding-loop/internal/kb"
	"github.com/kina/agent-coding-loop/internal/loop"
	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/skills"
	sqlite "github.com/kina/agent-coding-loop/internal/store/sqlite"
	"github.com/kina/agent-coding-loop/internal/tools"
)

type Service struct {
	cfg    *config.Config
	store  *sqlite.Store
	engine *loop.Engine
	skills *skills.Registry
}

func New(cfg *config.Config) (*Service, error) {
	if cfg == nil {
		return nil, fmt.Errorf("config is required")
	}
	if err := os.MkdirAll(filepath.Dir(cfg.DBPath), 0o755); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(cfg.Artifacts, 0o755); err != nil {
		return nil, err
	}

	runner := tools.NewRunner()
	coderRunner := tools.NewRunner(tools.WithReadOnly(true))
	reviewerRunner := tools.NewRunner(tools.WithReadOnly(true))
	store, err := sqlite.New(cfg.DBPath)
	if err != nil {
		return nil, err
	}
	if err := store.Migrate(context.Background()); err != nil {
		return nil, err
	}

	wd, _ := os.Getwd()
	skillRegistry := skills.NewRegistry(skills.DefaultSearchPaths(wd))
	_ = skillRegistry.Load()

	agentCfg := agentpkg.ClientConfig{
		BaseURL:      cfg.Model.BaseURL,
		Model:        cfg.Model.Model,
		APIKey:       cfg.Model.APIKey,
		ResponsesAPI: cfg.Model.ResponsesAPI,
	}
	kbClient := kb.NewClient(cfg.KB.BaseURL)
	engine := loop.NewEngine(loop.EngineDeps{
		Store:      store,
		Runner:     runner,
		Git:        gitpkg.NewClient(runner),
		GitHub:     ghpkg.NewClient(runner),
		KB:         kbClient,
		Coder:      agentpkg.NewCoder(agentCfg, agentpkg.WithRunner(coderRunner), agentpkg.WithSkills(skillRegistry), agentpkg.WithKB(kbClient)),
		Reviewer:   agentpkg.NewReviewer(agentCfg, agentpkg.WithRunner(reviewerRunner), agentpkg.WithSkills(skillRegistry), agentpkg.WithKB(kbClient)),
		Skills:     skillRegistry,
		Artifacts:  cfg.Artifacts,
		DoomThresh: 3,
	})

	return &Service{
		cfg:    cfg,
		store:  store,
		engine: engine,
		skills: skillRegistry,
	}, nil
}

func (s *Service) Run(ctx context.Context, spec model.RunSpec) (model.RunResult, error) {
	return s.engine.Run(ctx, spec)
}

func (s *Service) RunAsync(ctx context.Context, spec model.RunSpec) (string, error) {
	if err := spec.Validate(); err != nil {
		return "", err
	}
	runID, err := s.store.CreateRun(ctx, spec, model.RunStatusQueued)
	if err != nil {
		return "", err
	}
	go func() {
		_, _ = s.engine.RunWithID(context.Background(), runID, spec)
	}()
	return runID, nil
}

func (s *Service) Resume(ctx context.Context, runID string) (model.RunResult, error) {
	return s.engine.Resume(ctx, runID)
}

func (s *Service) Inspect(ctx context.Context, runID string) (sqlite.RunRecord, []sqlite.Event, error) {
	run, err := s.store.GetRun(ctx, runID)
	if err != nil {
		return sqlite.RunRecord{}, nil, err
	}
	events, err := s.store.GetRunEvents(ctx, runID)
	if err != nil {
		return sqlite.RunRecord{}, nil, err
	}
	return run, events, nil
}

func (s *Service) GetRun(ctx context.Context, runID string) (sqlite.RunRecord, error) {
	return s.store.GetRun(ctx, runID)
}

func (s *Service) GetRunEvents(ctx context.Context, runID string) ([]sqlite.Event, error) {
	return s.store.GetRunEvents(ctx, runID)
}

func (s *Service) ListSkills() []skills.SkillMeta {
	return s.skills.List()
}

func (s *Service) GetSkill(name string) (skills.SkillMeta, string, bool, error) {
	meta, ok := s.skills.Get(name)
	if !ok {
		return skills.SkillMeta{}, "", false, nil
	}
	content, found, err := s.skills.LoadContent(name)
	return meta, content, found, err
}

func DecodeSpec(raw string) (model.RunSpec, error) {
	var spec model.RunSpec
	if err := json.Unmarshal([]byte(raw), &spec); err != nil {
		return model.RunSpec{}, err
	}
	return spec, nil
}

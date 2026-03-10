package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/kina/agent-coding-loop/internal/config"
	httpapi "github.com/kina/agent-coding-loop/internal/http"
	"github.com/kina/agent-coding-loop/internal/model"
	"github.com/kina/agent-coding-loop/internal/service"
)

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}
	ctx := context.Background()
	sub := os.Args[1]
	switch sub {
	case "run":
		runCmd(ctx, os.Args[2:])
	case "serve":
		serveCmd(ctx, os.Args[2:])
	case "resume":
		resumeCmd(ctx, os.Args[2:])
	case "inspect":
		inspectCmd(ctx, os.Args[2:])
	case "help", "-h", "--help":
		printUsage()
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", sub)
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Println(`agent-loop commands:
  run --goal "..." [--repo PATH] [--config FILE] [--pr-mode auto|live|dry-run] [--retrieval-mode off|prefetch]
  serve [--listen 127.0.0.1:8787] [--config FILE]
  resume --run-id ID [--config FILE]
  inspect --run-id ID [--config FILE]`)
}

func runCmd(ctx context.Context, args []string) {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	goal := fs.String("goal", "", "run goal")
	repo := fs.String("repo", "", "target repo")
	cfgPath := fs.String("config", "", "config file")
	prMode := fs.String("pr-mode", "auto", "auto|live|dry-run")
	retrievalMode := fs.String("retrieval-mode", "off", "off|prefetch")
	maxIterations := fs.Int("max-iterations", 5, "max iterations")
	testCmd := fs.String("test-cmd", "", "explicit test command")
	lintCmd := fs.String("lint-cmd", "", "explicit lint command")
	buildCmd := fs.String("build-cmd", "", "explicit build command")
	if err := fs.Parse(args); err != nil {
		fatal(err)
	}
	if strings.TrimSpace(*goal) == "" {
		fatal(fmt.Errorf("--goal is required"))
	}
	cfg, svc := mustService(*cfgPath)
	mode, err := model.ParsePRMode(*prMode)
	if err != nil {
		fatal(err)
	}
	retrieval, err := model.ParseRetrievalMode(*retrievalMode)
	if err != nil {
		fatal(err)
	}
	spec := model.RunSpec{
		Goal:          *goal,
		Repo:          *repo,
		PRMode:        mode,
		RetrievalMode: retrieval,
		MaxIterations: *maxIterations,
		Provider:      cfg.Model.Provider,
		Model:         cfg.Model.Model,
		Commands: model.CommandSet{
			Test:  splitCommand(*testCmd),
			Lint:  splitCommand(*lintCmd),
			Build: splitCommand(*buildCmd),
		},
	}
	result, err := svc.Run(ctx, spec)
	if err != nil {
		fatal(err)
	}
	printJSON(result)
}

func serveCmd(ctx context.Context, args []string) {
	fs := flag.NewFlagSet("serve", flag.ExitOnError)
	cfgPath := fs.String("config", "", "config file")
	listen := fs.String("listen", "", "listen addr")
	if err := fs.Parse(args); err != nil {
		fatal(err)
	}
	cfg, svc := mustService(*cfgPath)
	addr := cfg.ListenAddr
	if strings.TrimSpace(*listen) != "" {
		addr = *listen
	}
	server := httpapi.NewServer(svc)
	fmt.Printf("agent-loop server listening on %s\n", addr)
	if err := server.ListenAndServe(ctx, addr); err != nil && err.Error() != "http: Server closed" {
		fatal(err)
	}
}

func resumeCmd(ctx context.Context, args []string) {
	fs := flag.NewFlagSet("resume", flag.ExitOnError)
	runID := fs.String("run-id", "", "run id")
	cfgPath := fs.String("config", "", "config file")
	if err := fs.Parse(args); err != nil {
		fatal(err)
	}
	if strings.TrimSpace(*runID) == "" {
		fatal(fmt.Errorf("--run-id is required"))
	}
	_, svc := mustService(*cfgPath)
	result, err := svc.Resume(ctx, *runID)
	if err != nil {
		fatal(err)
	}
	printJSON(result)
}

func inspectCmd(ctx context.Context, args []string) {
	fs := flag.NewFlagSet("inspect", flag.ExitOnError)
	runID := fs.String("run-id", "", "run id")
	cfgPath := fs.String("config", "", "config file")
	if err := fs.Parse(args); err != nil {
		fatal(err)
	}
	if strings.TrimSpace(*runID) == "" {
		fatal(fmt.Errorf("--run-id is required"))
	}
	_, svc := mustService(*cfgPath)
	run, events, err := svc.Inspect(ctx, *runID)
	if err != nil {
		fatal(err)
	}
	printJSON(map[string]any{"run": run, "events": events})
}

func splitCommand(v string) []string {
	v = strings.TrimSpace(v)
	if v == "" {
		return nil
	}
	return []string{v}
}

func mustService(cfgPath string) (*config.Config, *service.Service) {
	cfg, err := config.Load(cfgPath)
	if err != nil {
		fatal(err)
	}
	svc, err := service.New(cfg)
	if err != nil {
		fatal(err)
	}
	return cfg, svc
}

func printJSON(v any) {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}

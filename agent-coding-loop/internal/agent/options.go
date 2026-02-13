package agent

import (
	"github.com/kina/agent-coding-loop/internal/skills"
	"github.com/kina/agent-coding-loop/internal/tools"
)

type Option func(*deps)

type deps struct {
	runner *tools.Runner
	skills *skills.Registry
}

func WithRunner(r *tools.Runner) Option {
	return func(d *deps) {
		d.runner = r
	}
}

func WithSkills(reg *skills.Registry) Option {
	return func(d *deps) {
		d.skills = reg
	}
}

func applyOptions(opts []Option) deps {
	d := deps{}
	for _, opt := range opts {
		if opt == nil {
			continue
		}
		opt(&d)
	}
	return d
}

package tools

import (
	"fmt"

	"github.com/kina/agent-coding-loop/internal/skills"
)

func ListSkills(reg *skills.Registry) []skills.SkillMeta {
	if reg == nil {
		return nil
	}
	return reg.List()
}

func ViewSkill(reg *skills.Registry, name, section string, toc bool) (string, error) {
	if reg == nil {
		return "", fmt.Errorf("skills registry is nil")
	}
	body, found, err := reg.LoadContent(name)
	if err != nil {
		return "", err
	}
	if !found {
		return "", fmt.Errorf("skill not found: %s", name)
	}
	if toc {
		return skills.ExtractTOC(body), nil
	}
	if section != "" {
		out := skills.ExtractSection(body, section)
		if out == "" {
			return "", fmt.Errorf("section not found: %s", section)
		}
		return out, nil
	}
	return body, nil
}

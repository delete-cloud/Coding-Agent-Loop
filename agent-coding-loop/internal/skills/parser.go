package skills

import (
	"bytes"
	"fmt"
	"strings"
)

type SkillMeta struct {
	Name        string `yaml:"name" json:"name"`
	Description string `yaml:"description" json:"description"`
	Path        string `json:"path"`
}

func (m SkillMeta) Validate() error {
	if strings.TrimSpace(m.Name) == "" {
		return fmt.Errorf("skill name is required")
	}
	if strings.TrimSpace(m.Description) == "" {
		return fmt.Errorf("skill description is required")
	}
	return nil
}

func ParseSkillContent(data []byte) (SkillMeta, string, error) {
	parts := bytes.Split(data, []byte("\n"))
	if len(parts) == 0 || strings.TrimSpace(string(parts[0])) != "---" {
		return SkillMeta{}, "", fmt.Errorf("missing frontmatter")
	}
	end := -1
	for i := 1; i < len(parts); i++ {
		if strings.TrimSpace(string(parts[i])) == "---" {
			end = i
			break
		}
	}
	if end == -1 {
		return SkillMeta{}, "", fmt.Errorf("unterminated frontmatter")
	}
	fmRaw := string(bytes.Join(parts[1:end], []byte("\n")))
	meta, err := parseFrontmatter(fmRaw)
	if err != nil {
		return SkillMeta{}, "", err
	}
	if err := meta.Validate(); err != nil {
		return SkillMeta{}, "", err
	}
	body := strings.TrimSpace(string(bytes.Join(parts[end+1:], []byte("\n"))))
	return meta, body, nil
}

func parseFrontmatter(raw string) (SkillMeta, error) {
	meta := SkillMeta{}
	lines := strings.Split(raw, "\n")
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		parts := strings.SplitN(trimmed, ":", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		value := strings.Trim(strings.TrimSpace(parts[1]), `"'`)
		switch key {
		case "name":
			meta.Name = value
		case "description":
			meta.Description = value
		}
	}
	return meta, nil
}

func ExtractTOC(body string) string {
	lines := strings.Split(body, "\n")
	out := make([]string, 0, len(lines))
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if !strings.HasPrefix(trimmed, "#") {
			continue
		}
		level := 0
		for ; level < len(trimmed) && trimmed[level] == '#'; level++ {
		}
		heading := strings.TrimSpace(trimmed[level:])
		if heading == "" {
			continue
		}
		indent := strings.Repeat(" ", max(0, (level-1)*2))
		out = append(out, indent+strings.Repeat("#", level)+" "+heading)
	}
	return strings.Join(out, "\n")
}

func ExtractSection(body, heading string) string {
	lines := strings.Split(body, "\n")
	needle := strings.ToLower(strings.TrimSpace(heading))
	in := false
	level := 0
	out := make([]string, 0, 16)
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "#") {
			curLevel := 0
			for ; curLevel < len(trimmed) && trimmed[curLevel] == '#'; curLevel++ {
			}
			name := strings.ToLower(strings.TrimSpace(trimmed[curLevel:]))
			if name == needle {
				in = true
				level = curLevel
				out = append(out, line)
				continue
			}
			if in && curLevel <= level {
				break
			}
		}
		if in {
			out = append(out, line)
		}
	}
	return strings.TrimSpace(strings.Join(out, "\n"))
}

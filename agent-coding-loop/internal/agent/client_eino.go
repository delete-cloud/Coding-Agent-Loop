//go:build eino

package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/cloudwego/eino-ext/components/model/openai"
	"github.com/cloudwego/eino/schema"
)

type ClientConfig struct {
	BaseURL string
	Model   string
	APIKey  string
}

func (c ClientConfig) Ready() bool {
	return strings.TrimSpace(c.BaseURL) != "" && strings.TrimSpace(c.Model) != ""
}

func (c ClientConfig) CompleteJSON(ctx context.Context, systemPrompt, userPrompt string, out any) error {
	if !c.Ready() {
		return fmt.Errorf("llm client not configured")
	}
	cm, err := openai.NewChatModel(ctx, &openai.ChatModelConfig{
		BaseURL: c.BaseURL,
		Model:   c.Model,
		APIKey:  c.APIKey,
	})
	if err != nil {
		return err
	}
	resp, err := cm.Generate(ctx, []*schema.Message{
		schema.SystemMessage(systemPrompt),
		schema.UserMessage(userPrompt),
	})
	if err != nil {
		return err
	}
	content := extractJSON(resp.Content)
	if err := json.Unmarshal([]byte(content), out); err != nil {
		return fmt.Errorf("parse llm json failed: %w; content=%s", err, resp.Content)
	}
	return nil
}

func extractJSON(content string) string {
	trimmed := strings.TrimSpace(content)
	if strings.HasPrefix(trimmed, "```") {
		trimmed = strings.TrimPrefix(trimmed, "```json")
		trimmed = strings.TrimPrefix(trimmed, "```")
		trimmed = strings.TrimSuffix(trimmed, "```")
		trimmed = strings.TrimSpace(trimmed)
	}
	return trimmed
}

package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/cloudwego/eino-ext/components/model/openai"
	modelpkg "github.com/cloudwego/eino/components/model"
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

func (c ClientConfig) newToolCallingModel(ctx context.Context) (modelpkg.ToolCallingChatModel, error) {
	cm, err := openai.NewChatModel(ctx, &openai.ChatModelConfig{
		BaseURL: c.BaseURL,
		Model:   c.Model,
		APIKey:  c.APIKey,
	})
	if err != nil {
		return nil, err
	}
	var out modelpkg.ToolCallingChatModel = cm
	if isDeepSeekBaseURL(c.BaseURL) {
		out = compatToolCallingModel{inner: out}
	}
	out = retryToolCallingModel{inner: out}
	return out, nil
}

func (c ClientConfig) CompleteJSON(ctx context.Context, systemPrompt, userPrompt string, out any) error {
	if !c.Ready() {
		return fmt.Errorf("llm client not configured")
	}
	cm, err := c.newToolCallingModel(ctx)
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

type compatToolCallingModel struct {
	inner modelpkg.ToolCallingChatModel
}

func (m compatToolCallingModel) Generate(ctx context.Context, input []*schema.Message, opts ...modelpkg.Option) (*schema.Message, error) {
	return m.inner.Generate(ctx, normalizeChatMessages(input), opts...)
}

func (m compatToolCallingModel) Stream(ctx context.Context, input []*schema.Message, opts ...modelpkg.Option) (*schema.StreamReader[*schema.Message], error) {
	return m.inner.Stream(ctx, normalizeChatMessages(input), opts...)
}

func (m compatToolCallingModel) WithTools(tools []*schema.ToolInfo) (modelpkg.ToolCallingChatModel, error) {
	out, err := m.inner.WithTools(tools)
	if err != nil {
		return nil, err
	}
	return compatToolCallingModel{inner: out}, nil
}

func normalizeChatMessages(in []*schema.Message) []*schema.Message {
	out := make([]*schema.Message, 0, len(in))
	for _, msg := range in {
		if msg == nil {
			out = append(out, nil)
			continue
		}
		needsContent := false
		if msg.Role == schema.Assistant && len(msg.ToolCalls) > 0 && strings.TrimSpace(msg.Content) == "" {
			needsContent = true
		}
		if msg.Role == schema.Tool && strings.TrimSpace(msg.Content) == "" {
			needsContent = true
		}
		if !needsContent {
			out = append(out, msg)
			continue
		}
		cp := *msg
		cp.Content = " "
		out = append(out, &cp)
	}
	return out
}

func isDeepSeekBaseURL(baseURL string) bool {
	return strings.Contains(strings.ToLower(baseURL), "deepseek")
}

type retryToolCallingModel struct {
	inner modelpkg.ToolCallingChatModel
}

func (m retryToolCallingModel) Generate(ctx context.Context, input []*schema.Message, opts ...modelpkg.Option) (*schema.Message, error) {
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				if lastErr != nil {
					return nil, lastErr
				}
				return nil, ctx.Err()
			case <-time.After(time.Duration(attempt) * 400 * time.Millisecond):
			}
		}
		resp, err := m.inner.Generate(ctx, input, opts...)
		if err == nil {
			return resp, nil
		}
		lastErr = err
		if !isRetryableLLMError(err) {
			return nil, err
		}
	}
	return nil, lastErr
}

func (m retryToolCallingModel) Stream(ctx context.Context, input []*schema.Message, opts ...modelpkg.Option) (*schema.StreamReader[*schema.Message], error) {
	return m.inner.Stream(ctx, input, opts...)
}

func (m retryToolCallingModel) WithTools(tools []*schema.ToolInfo) (modelpkg.ToolCallingChatModel, error) {
	out, err := m.inner.WithTools(tools)
	if err != nil {
		return nil, err
	}
	return retryToolCallingModel{inner: out}, nil
}

func isRetryableLLMError(err error) bool {
	if err == nil {
		return false
	}
	s := strings.ToLower(err.Error())
	if strings.Contains(s, "tls: bad record mac") {
		return true
	}
	if strings.Contains(s, "connection reset") {
		return true
	}
	if strings.Contains(s, "broken pipe") {
		return true
	}
	if strings.Contains(s, "timeout") {
		return true
	}
	if strings.Contains(s, "temporary") {
		return true
	}
	return false
}

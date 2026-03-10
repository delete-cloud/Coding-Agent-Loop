package agent

import (
	"context"
	"encoding/json"
	"errors"
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

const defaultModelTimeout = 90 * time.Second
const maxJSONRepairAttempts = 3
const maxDiagnosticContentPreview = 512

func (c ClientConfig) Ready() bool {
	return strings.TrimSpace(c.BaseURL) != "" && strings.TrimSpace(c.Model) != ""
}

func (c ClientConfig) newToolCallingModel(ctx context.Context) (modelpkg.ToolCallingChatModel, error) {
	if err := c.validateAuth(); err != nil {
		return nil, err
	}
	cm, err := openai.NewChatModel(ctx, &openai.ChatModelConfig{
		BaseURL: c.BaseURL,
		Model:   c.Model,
		APIKey:  c.APIKey,
		Timeout: defaultModelTimeout,
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
	return completeJSONWithModel(ctx, cm, systemPrompt, userPrompt, out)
}

func (c ClientConfig) validateAuth() error {
	baseURL := strings.TrimSpace(c.BaseURL)
	if baseURL == "" {
		return nil
	}
	if strings.TrimSpace(c.APIKey) != "" {
		return nil
	}
	if isLikelyLocalBaseURL(baseURL) {
		return nil
	}
	return fmt.Errorf("OPENAI_API_KEY is required for remote openai-compatible base URL %q", baseURL)
}

func completeJSONWithModel(ctx context.Context, cm modelpkg.ToolCallingChatModel, systemPrompt, userPrompt string, out any) error {
	if cm == nil {
		return fmt.Errorf("llm model is nil")
	}
	base := []*schema.Message{
		schema.SystemMessage(systemPrompt),
		schema.UserMessage(userPrompt),
	}
	messages := append([]*schema.Message{}, base...)

	lastRaw := ""
	var lastErr error
	for attempt := 0; attempt < maxJSONRepairAttempts; attempt++ {
		resp, err := cm.Generate(ctx, messages)
		if err != nil {
			return fmt.Errorf("model_generate attempt=%d: %s", attempt+1, formatDiagnosticError(err))
		}
		lastRaw = ""
		if resp != nil {
			lastRaw = strings.TrimSpace(resp.Content)
		}
		content := extractJSON(lastRaw)
		if err := json.Unmarshal([]byte(content), out); err == nil {
			return nil
		} else {
			lastErr = err
		}

		if attempt >= maxJSONRepairAttempts-1 {
			break
		}
		messages = append([]*schema.Message{}, base...)
		if lastRaw != "" {
			messages = append(messages, schema.AssistantMessage(lastRaw, nil))
		}
		messages = append(messages, schema.UserMessage(buildJSONRepairPrompt(lastRaw)))
	}
	return fmt.Errorf("parse llm json failed: %w; content=%s", lastErr, truncateDiagnosticPreview(lastRaw))
}

func buildJSONRepairPrompt(previous string) string {
	preview := strings.TrimSpace(previous)
	if len(preview) > 2000 {
		preview = preview[:2000]
	}
	if preview == "" {
		return "Your previous response was empty or invalid JSON. Return ONLY a valid JSON object now. No markdown, no explanations."
	}
	return "Your previous response was not valid JSON. Rewrite it as valid JSON only.\n" +
		"Rules:\n" +
		"- Output only JSON.\n" +
		"- No markdown fences.\n" +
		"- No prose before or after JSON.\n" +
		"Previous response:\n" + preview
}

func formatDiagnosticError(err error) string {
	if err == nil {
		return "unknown error"
	}
	return fmt.Sprintf("type=%T; causes=%s", err, strings.Join(flattenDiagnosticErrorMessages(err), " -> "))
}

func flattenDiagnosticErrorMessages(err error) []string {
	if err == nil {
		return []string{"unknown error"}
	}
	out := make([]string, 0, 4)
	for current := err; current != nil; current = errors.Unwrap(current) {
		out = append(out, summarizeSingleDiagnosticError(current))
	}
	if len(out) == 0 {
		return []string{fmt.Sprintf("empty model error (type=%T)", err)}
	}
	return out
}

func summarizeSingleDiagnosticError(err error) string {
	if err == nil {
		return "unknown error"
	}
	if apiErr, ok := err.(*openai.APIError); ok {
		return fmt.Sprintf(
			"openai_api_error(type=%T, http_status_code=%d, http_status=%q, api_error_type=%q, api_error_code=%q, param=%q, message=%q)",
			err,
			apiErr.HTTPStatusCode,
			apiErr.HTTPStatus,
			apiErr.Type,
			fmt.Sprint(apiErr.Code),
			stringPtrValue(apiErr.Param),
			strings.TrimSpace(apiErr.Message),
		)
	}
	msg := strings.TrimSpace(err.Error())
	if msg == "" {
		return fmt.Sprintf("empty model error (type=%T)", err)
	}
	return msg
}

func truncateDiagnosticPreview(raw string) string {
	preview := strings.TrimSpace(raw)
	if len(preview) <= maxDiagnosticContentPreview {
		return preview
	}
	return preview[:maxDiagnosticContentPreview] + "...(truncated)"
}

func stringPtrValue(v *string) string {
	if v == nil {
		return ""
	}
	return *v
}

func extractJSON(content string) string {
	trimmed := strings.TrimSpace(content)
	if trimmed == "" {
		return trimmed
	}
	if strings.HasPrefix(trimmed, "```") {
		trimmed = strings.TrimPrefix(trimmed, "```json")
		trimmed = strings.TrimPrefix(trimmed, "```JSON")
		trimmed = strings.TrimPrefix(trimmed, "```")
		if idx := strings.LastIndex(trimmed, "```"); idx >= 0 {
			trimmed = trimmed[:idx]
		}
		trimmed = strings.TrimSpace(trimmed)
	}
	if js, ok := findFirstJSONValue(trimmed); ok {
		return js
	}
	return trimmed
}

func findFirstJSONValue(s string) (string, bool) {
	for i := 0; i < len(s); i++ {
		ch := s[i]
		if ch != '{' && ch != '[' {
			continue
		}
		if out, ok := extractBalancedJSON(s, i); ok {
			return out, true
		}
	}
	return "", false
}

func extractBalancedJSON(s string, start int) (string, bool) {
	depth := 0
	inString := false
	escaped := false
	for i := start; i < len(s); i++ {
		ch := s[i]
		if inString {
			if escaped {
				escaped = false
				continue
			}
			if ch == '\\' {
				escaped = true
				continue
			}
			if ch == '"' {
				inString = false
			}
			continue
		}
		switch ch {
		case '"':
			inString = true
		case '{', '[':
			depth++
		case '}', ']':
			depth--
			if depth < 0 {
				return "", false
			}
			if depth == 0 {
				candidate := strings.TrimSpace(s[start : i+1])
				if json.Valid([]byte(candidate)) {
					return candidate, true
				}
				return "", false
			}
		}
	}
	return "", false
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

func isLikelyLocalBaseURL(baseURL string) bool {
	lower := strings.ToLower(strings.TrimSpace(baseURL))
	return strings.Contains(lower, "127.0.0.1") ||
		strings.Contains(lower, "localhost") ||
		strings.Contains(lower, "[::1]") ||
		strings.HasPrefix(lower, "http://0.0.0.0")
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
	var apiErr *openai.APIError
	if errors.As(err, &apiErr) && apiErr != nil {
		if apiErr.HTTPStatusCode == 0 &&
			strings.TrimSpace(apiErr.HTTPStatus) == "" &&
			strings.TrimSpace(apiErr.Type) == "" &&
			strings.TrimSpace(apiErr.Message) == "" &&
			strings.TrimSpace(stringPtrValue(apiErr.Param)) == "" {
			return true
		}
	}
	s := strings.ToLower(err.Error())
	if strings.Contains(s, "tls: bad record mac") {
		return true
	}
	if strings.Contains(s, "connection reset") {
		return true
	}
	if strings.Contains(s, "can't assign requested address") {
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
	if strings.Contains(s, "unexpected eof") || strings.Contains(s, ": eof") || strings.HasSuffix(strings.TrimSpace(s), "eof") {
		return true
	}
	for _, code := range []string{"520", "521", "522", "523", "524", "525", "526", "502", "503", "504"} {
		if strings.Contains(s, "status code: "+code) || strings.Contains(s, "status: "+code) {
			return true
		}
	}
	if strings.Contains(s, "invalid character '<'") && strings.Contains(s, "looking for beginning of value") {
		return true
	}
	if strings.Contains(s, "bad gateway") || strings.Contains(s, "gateway timeout") {
		return true
	}
	return false
}

package agent

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	modelpkg "github.com/cloudwego/eino/components/model"
	"github.com/cloudwego/eino/schema"
)

func TestExtractJSONFindsEmbeddedObject(t *testing.T) {
	raw := "I'll help.\n{\"summary\":\"ok\",\"patch\":\"\"}\nThanks."
	got := extractJSON(raw)
	if !strings.Contains(got, "\"summary\":\"ok\"") {
		t.Fatalf("expected extracted json object, got %q", got)
	}
}

func TestExtractJSONCodeFence(t *testing.T) {
	raw := "```json\n{\"decision\":\"approve\"}\n```"
	got := extractJSON(raw)
	if got != "{\"decision\":\"approve\"}" {
		t.Fatalf("unexpected extract result: %q", got)
	}
}

func TestIsRetryableLLMErrorGatewayAndEOF(t *testing.T) {
	cases := []error{
		errors.New("Post \"https://x/v1/chat/completions\": EOF"),
		errors.New("status code: 520, body: <html>..."),
		errors.New("status code: 524, body: <html>..."),
		errors.New("invalid character '<' looking for beginning of value"),
	}
	for _, c := range cases {
		if !isRetryableLLMError(c) {
			t.Fatalf("expected retryable for %q", c.Error())
		}
	}
	if isRetryableLLMError(errors.New("invalid api key")) {
		t.Fatalf("invalid api key should not be retryable")
	}
}

func TestCoderFallsBackWhenModelUnavailable(t *testing.T) {
	c := NewCoder(ClientConfig{
		BaseURL: "http://127.0.0.1:1/v1",
		Model:   "claude-sonnet-4-6",
		APIKey:  "x",
	})
	ctx, cancel := context.WithTimeout(context.Background(), 1200*time.Millisecond)
	defer cancel()
	out, err := c.Generate(ctx, CoderInput{Goal: "touch README"})
	if err != nil {
		t.Fatalf("expected fallback without hard error, got %v", err)
	}
	if strings.TrimSpace(out.Summary) == "" {
		t.Fatalf("expected non-empty summary")
	}
}

func TestReviewerFallsBackWhenModelUnavailable(t *testing.T) {
	r := NewReviewer(ClientConfig{
		BaseURL: "http://127.0.0.1:1/v1",
		Model:   "claude-sonnet-4-6",
		APIKey:  "x",
	})
	ctx, cancel := context.WithTimeout(context.Background(), 1200*time.Millisecond)
	defer cancel()
	out, err := r.Review(ctx, ReviewInput{Goal: "check", RepoRoot: ".", CommandOutput: "PASS"})
	if err != nil {
		t.Fatalf("expected fallback without hard error, got %v", err)
	}
	if strings.TrimSpace(out.Decision) == "" {
		t.Fatalf("expected non-empty decision")
	}
}

type fakeToolCallingModel struct {
	responses []string
	calls     int
}

func (f *fakeToolCallingModel) Generate(_ context.Context, _ []*schema.Message, _ ...modelpkg.Option) (*schema.Message, error) {
	f.calls++
	if len(f.responses) == 0 {
		return schema.AssistantMessage("", nil), nil
	}
	idx := f.calls - 1
	if idx >= len(f.responses) {
		idx = len(f.responses) - 1
	}
	return schema.AssistantMessage(f.responses[idx], nil), nil
}

func (f *fakeToolCallingModel) Stream(_ context.Context, _ []*schema.Message, _ ...modelpkg.Option) (*schema.StreamReader[*schema.Message], error) {
	return nil, errors.New("stream not implemented in fake model")
}

func (f *fakeToolCallingModel) WithTools(_ []*schema.ToolInfo) (modelpkg.ToolCallingChatModel, error) {
	return f, nil
}

func TestCompleteJSONRepairsPlainTextResponse(t *testing.T) {
	model := &fakeToolCallingModel{
		responses: []string{
			"I'll review this code change first.",
			`{"decision":"approve","summary":"ok"}`,
		},
	}
	var out map[string]any
	err := completeJSONWithModel(context.Background(), model, "system", "user", &out)
	if err != nil {
		t.Fatalf("completeJSONWithModel should recover non-json response, got %v", err)
	}
	if model.calls < 2 {
		t.Fatalf("expected at least 2 model calls, got %d", model.calls)
	}
	decision, _ := out["decision"].(string)
	if strings.TrimSpace(decision) != "approve" {
		t.Fatalf("expected decision=approve, got %q", decision)
	}
}

func TestCompleteJSONFailsAfterRepairAttempts(t *testing.T) {
	model := &fakeToolCallingModel{
		responses: []string{
			"not json 1",
			"not json 2",
			"not json 3",
		},
	}
	var out map[string]any
	err := completeJSONWithModel(context.Background(), model, "system", "user", &out)
	if err == nil {
		t.Fatalf("expected parse error after retries")
	}
	if !strings.Contains(strings.ToLower(err.Error()), "parse llm json failed") {
		t.Fatalf("expected parse llm json failed error, got %v", err)
	}
	if model.calls != 3 {
		t.Fatalf("expected 3 attempts, got %d", model.calls)
	}
}

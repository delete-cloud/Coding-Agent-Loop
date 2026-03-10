package agent

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	openaiext "github.com/cloudwego/eino-ext/components/model/openai"
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
		errors.New("Post \"https://x/v1/chat/completions\": dial tcp 198.18.0.171:443: connect: can't assign requested address"),
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

func TestIsRetryableLLMErrorTreatsEmptyOpenAIAPIErrorAsTransportFlake(t *testing.T) {
	err := &openaiext.APIError{}
	if !isRetryableLLMError(err) {
		t.Fatalf("expected empty APIError shell to be retryable")
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
	errs      []error
	calls     int
}

func (f *fakeToolCallingModel) Generate(_ context.Context, _ []*schema.Message, _ ...modelpkg.Option) (*schema.Message, error) {
	f.calls++
	if len(f.errs) > 0 {
		idx := f.calls - 1
		if idx >= len(f.errs) {
			idx = len(f.errs) - 1
		}
		if f.errs[idx] != nil {
			return nil, f.errs[idx]
		}
	}
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

type emptyToolCallingModelError struct{}

func (emptyToolCallingModelError) Error() string { return "" }

func TestCompleteJSONFormatsGenerateError(t *testing.T) {
	model := &fakeToolCallingModel{
		errs: []error{errors.New("rate limit exceeded")},
	}
	var out map[string]any
	err := completeJSONWithModel(context.Background(), model, "system", "user", &out)
	if err == nil {
		t.Fatalf("expected generate error")
	}
	msg := err.Error()
	if !strings.Contains(msg, "model_generate") {
		t.Fatalf("expected model_generate stage in %q", msg)
	}
	if !strings.Contains(msg, "attempt=1") {
		t.Fatalf("expected attempt number in %q", msg)
	}
	if !strings.Contains(msg, "rate limit exceeded") {
		t.Fatalf("expected original error message in %q", msg)
	}
}

func TestCompleteJSONFormatsEmptyGenerateError(t *testing.T) {
	model := &fakeToolCallingModel{
		errs: []error{emptyToolCallingModelError{}},
	}
	var out map[string]any
	err := completeJSONWithModel(context.Background(), model, "system", "user", &out)
	if err == nil {
		t.Fatalf("expected generate error")
	}
	msg := err.Error()
	if !strings.Contains(msg, "empty model error") {
		t.Fatalf("expected empty model error fallback in %q", msg)
	}
	if !strings.Contains(msg, "agent.emptyToolCallingModelError") {
		t.Fatalf("expected type name in %q", msg)
	}
}

func TestCompleteJSONFormatsWrappedGenerateErrorCauseChain(t *testing.T) {
	model := &fakeToolCallingModel{
		errs: []error{fmt.Errorf("tool-calling chat failed: %w", errors.New("upstream 429"))},
	}
	var out map[string]any
	err := completeJSONWithModel(context.Background(), model, "system", "user", &out)
	if err == nil {
		t.Fatalf("expected generate error")
	}
	msg := err.Error()
	if !strings.Contains(msg, "tool-calling chat failed") {
		t.Fatalf("expected wrapped error message in %q", msg)
	}
	if !strings.Contains(msg, "upstream 429") {
		t.Fatalf("expected unwrapped cause in %q", msg)
	}
}

func TestCompleteJSONTruncatesParseFailureContent(t *testing.T) {
	long := strings.Repeat("x", 3000)
	model := &fakeToolCallingModel{
		responses: []string{long, long, long},
	}
	var out map[string]any
	err := completeJSONWithModel(context.Background(), model, "system", "user", &out)
	if err == nil {
		t.Fatalf("expected parse error after retries")
	}
	msg := err.Error()
	if !strings.Contains(msg, "content=") {
		t.Fatalf("expected content preview in %q", msg)
	}
	if strings.Contains(msg, strings.Repeat("x", 2500)) {
		t.Fatalf("expected parse failure content to be truncated, got %q", msg)
	}
}

func TestFormatDiagnosticErrorIncludesOpenAIAPIErrorFields(t *testing.T) {
	err := &openaiext.APIError{
		Code:           "invalid_api_key",
		Message:        "bad api key",
		Type:           "invalid_request_error",
		HTTPStatus:     "401 Unauthorized",
		HTTPStatusCode: 401,
	}
	msg := formatDiagnosticError(err)
	for _, want := range []string{
		"type=*openai.APIError",
		"http_status_code=401",
		"http_status=\"401 Unauthorized\"",
		"api_error_type=\"invalid_request_error\"",
		"api_error_code=\"invalid_api_key\"",
		"message=\"bad api key\"",
	} {
		if !strings.Contains(msg, want) {
			t.Fatalf("expected %q in %q", want, msg)
		}
	}
}

func TestFormatDiagnosticErrorKeepsWrapperAndFormatsInnerAPIErrorSeparately(t *testing.T) {
	err := fmt.Errorf("node wrapper: %w", &openaiext.APIError{
		Message:        "bad api key",
		Type:           "invalid_request_error",
		HTTPStatus:     "401 Unauthorized",
		HTTPStatusCode: 401,
	})
	msg := formatDiagnosticError(err)
	if !strings.Contains(msg, "causes=node wrapper: error, status code: 401, status: 401 Unauthorized, message: bad api key -> openai_api_error(") {
		t.Fatalf("expected wrapper message followed by structured API error, got %q", msg)
	}
	if strings.Contains(msg, "openai_api_error(type=*fmt.wrapError") {
		t.Fatalf("did not expect wrapper type to be formatted as APIError, got %q", msg)
	}
}

func TestCompleteJSONRequiresAPIKeyForRemoteBaseURL(t *testing.T) {
	cfg := ClientConfig{
		BaseURL: "https://right.codes/claude/v1",
		Model:   "claude-haiku-4-5",
	}
	var out map[string]any
	err := cfg.CompleteJSON(context.Background(), "system", "user", &out)
	if err == nil {
		t.Fatal("expected missing api key error")
	}
	msg := err.Error()
	if !strings.Contains(msg, "OPENAI_API_KEY") {
		t.Fatalf("expected OPENAI_API_KEY guidance, got %q", msg)
	}
	if !strings.Contains(msg, "remote") {
		t.Fatalf("expected remote base url guidance, got %q", msg)
	}
}

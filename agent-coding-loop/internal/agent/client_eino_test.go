package agent

import (
	"context"
	"encoding/json"
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

type badJSONMarshaler struct{}

func (badJSONMarshaler) MarshalJSON() ([]byte, error) {
	return nil, errors.New("bad json value")
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

func TestCompleteJSONWithGeneratorRepairsPlainTextResponse(t *testing.T) {
	model := &fakeToolCallingModel{
		responses: []string{
			"I'll think first.",
			`{"summary":"ok","patch":"","commands":[]}`,
		},
	}
	var out map[string]any
	err := completeJSONWithGenerator(context.Background(), func(ctx context.Context, messages []*schema.Message) (*schema.Message, error) {
		return model.Generate(ctx, messages)
	}, "system", "user", &out)
	if err != nil {
		t.Fatalf("completeJSONWithGenerator should recover non-json response, got %v", err)
	}
	if model.calls < 2 {
		t.Fatalf("expected at least 2 model calls, got %d", model.calls)
	}
	summary, _ := out["summary"].(string)
	if strings.TrimSpace(summary) != "ok" {
		t.Fatalf("expected summary=ok, got %q", summary)
	}
}

func TestCompleteJSONWithGeneratorRawPreservesLeadingAndTrailingWhitespace(t *testing.T) {
	rawWithWhitespace := "\n  {\"summary\":\"ok\",\"patch\":\"\",\"commands\":[]}  \n"
	model := &fakeToolCallingModel{
		responses: []string{rawWithWhitespace},
	}
	var out map[string]any
	raw, err := completeJSONWithGeneratorRaw(context.Background(), func(ctx context.Context, messages []*schema.Message) (*schema.Message, error) {
		return model.Generate(ctx, messages)
	}, "system", "user", &out)
	if err != nil {
		t.Fatalf("completeJSONWithGeneratorRaw: %v", err)
	}
	if raw != rawWithWhitespace {
		t.Fatalf("expected raw response to preserve whitespace, got %q want %q", raw, rawWithWhitespace)
	}
	summary, _ := out["summary"].(string)
	if summary != "ok" {
		t.Fatalf("expected parsed summary=ok, got %q", summary)
	}
}

func TestCompleteJSONWithRawReturnsMarshalErrorForTestShim(t *testing.T) {
	c := ClientConfig{
		completeJSONForTest: func(_ context.Context, _, _ string, out any) error {
			wire, ok := out.(*interface{})
			if !ok {
				return fmt.Errorf("unexpected out type %T", out)
			}
			*wire = map[string]any{"summary": badJSONMarshaler{}}
			return nil
		},
	}

	var out any
	raw, err := c.CompleteJSONWithRaw(context.Background(), "system", "user", &out)
	if err == nil {
		t.Fatalf("expected marshal error")
	}
	if raw != "" {
		t.Fatalf("expected empty raw on marshal error, got %q", raw)
	}
	if !strings.Contains(strings.ToLower(err.Error()), "bad json value") {
		t.Fatalf("expected marshal error to surface, got %v", err)
	}
}

func TestCompleteJSONWithGeneratorFailsAfterRepairAttempts(t *testing.T) {
	model := &fakeToolCallingModel{
		responses: []string{
			"not json 1",
			"not json 2",
			"not json 3",
		},
	}
	var out map[string]any
	err := completeJSONWithGenerator(context.Background(), func(ctx context.Context, messages []*schema.Message) (*schema.Message, error) {
		return model.Generate(ctx, messages)
	}, "system", "user", &out)
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

func TestCoderGenerateInvalidToolCallingJSONDoesNotRegenerateAgent(t *testing.T) {
	model := &fakeToolCallingModel{
		responses: []string{"not json"},
	}
	var repairCalls int
	c := NewCoder(ClientConfig{
		BaseURL: "http://example.com",
		Model:   "test-model",
		newToolCallingModelForTest: func(context.Context) (modelpkg.ToolCallingChatModel, error) {
			return model, nil
		},
		completeJSONForTest: func(_ context.Context, _, _ string, out any) error {
			repairCalls++
			return json.Unmarshal([]byte(`{"summary":"ok","patch":"","commands":[]}`), out)
		},
	})

	out, err := c.Generate(context.Background(), CoderInput{
		Goal:        "touch README.md",
		RepoSummary: t.TempDir(),
	})
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if model.calls != 1 {
		t.Fatalf("expected single tool-calling generate, got %d", model.calls)
	}
	if repairCalls < 1 {
		t.Fatalf("expected at least one no-tool JSON repair, got %d", repairCalls)
	}
	if out.UsedFallback {
		t.Fatalf("expected repaired tool-calling result without outer fallback, got %+v", out)
	}
	if strings.TrimSpace(out.Summary) != "ok" {
		t.Fatalf("expected repaired summary, got %+v", out)
	}
}

func TestReviewerReviewInvalidToolCallingJSONDoesNotRegenerateAgent(t *testing.T) {
	model := &fakeToolCallingModel{
		responses: []string{"not json"},
	}
	var repairCalls int
	r := NewReviewer(ClientConfig{
		BaseURL: "http://example.com",
		Model:   "test-model",
		newToolCallingModelForTest: func(context.Context) (modelpkg.ToolCallingChatModel, error) {
			return model, nil
		},
		completeJSONForTest: func(_ context.Context, _, _ string, out any) error {
			repairCalls++
			return json.Unmarshal([]byte(`{"decision":"comment","summary":"ok","findings":[],"review_markdown":""}`), out)
		},
	})

	out, err := r.Review(context.Background(), ReviewInput{
		Goal:          "check",
		RepoRoot:      t.TempDir(),
		CommandOutput: "PASS",
	})
	if err != nil {
		t.Fatalf("Review: %v", err)
	}
	if model.calls != 1 {
		t.Fatalf("expected single tool-calling generate, got %d", model.calls)
	}
	if repairCalls < 1 {
		t.Fatalf("expected at least one no-tool JSON repair, got %d", repairCalls)
	}
	if out.UsedFallback {
		t.Fatalf("expected repaired tool-calling result without outer fallback, got %+v", out)
	}
	if strings.TrimSpace(out.Summary) != "ok" {
		t.Fatalf("expected repaired summary, got %+v", out)
	}
}

func TestCoderGenerateWithEinoWrapsStructuredOutputStageAndPreview(t *testing.T) {
	long := strings.Repeat("x", 3000)
	model := &fakeToolCallingModel{
		responses: []string{long},
	}
	c := NewCoder(ClientConfig{
		BaseURL: "http://example.com",
		Model:   "test-model",
		newToolCallingModelForTest: func(context.Context) (modelpkg.ToolCallingChatModel, error) {
			return model, nil
		},
		completeJSONForTest: func(_ context.Context, _, _ string, out any) error {
			wire, ok := out.(*map[string]any)
			if !ok {
				return fmt.Errorf("unexpected out type %T", out)
			}
			*wire = map[string]any{"summary": badJSONMarshaler{}}
			return nil
		},
	})

	_, err := c.generateWithEino(context.Background(), CoderInput{
		Goal:        "touch README.md",
		RepoSummary: t.TempDir(),
	})
	if err == nil {
		t.Fatalf("expected structured output error")
	}
	msg := err.Error()
	if !strings.Contains(msg, "encode repaired coder json failed") {
		t.Fatalf("expected encode stage label in %q", msg)
	}
	if !strings.Contains(msg, "content=") {
		t.Fatalf("expected content preview in %q", msg)
	}
	if strings.Contains(msg, strings.Repeat("x", 2500)) {
		t.Fatalf("expected truncated content preview, got %q", msg)
	}
}

func TestReviewerReviewWithEinoWrapsStructuredOutputStageAndPreview(t *testing.T) {
	long := strings.Repeat("y", 3000)
	model := &fakeToolCallingModel{
		responses: []string{long},
	}
	r := NewReviewer(ClientConfig{
		BaseURL: "http://example.com",
		Model:   "test-model",
		newToolCallingModelForTest: func(context.Context) (modelpkg.ToolCallingChatModel, error) {
			return model, nil
		},
		completeJSONForTest: func(_ context.Context, _, _ string, out any) error {
			wire, ok := out.(*map[string]any)
			if !ok {
				return fmt.Errorf("unexpected out type %T", out)
			}
			*wire = map[string]any{"decision": badJSONMarshaler{}}
			return nil
		},
	})

	_, err := r.reviewWithEino(context.Background(), ReviewInput{
		Goal:          "check",
		RepoRoot:      t.TempDir(),
		CommandOutput: "PASS",
	})
	if err == nil {
		t.Fatalf("expected structured output error")
	}
	msg := err.Error()
	if !strings.Contains(msg, "encode repaired reviewer json failed") {
		t.Fatalf("expected encode stage label in %q", msg)
	}
	if !strings.Contains(msg, "content=") {
		t.Fatalf("expected content preview in %q", msg)
	}
	if strings.Contains(msg, strings.Repeat("y", 2500)) {
		t.Fatalf("expected truncated content preview, got %q", msg)
	}
}

func TestReviewerFallbackCompletionPreservesEinoStructuredOutputDiagnostics(t *testing.T) {
	long := strings.Repeat("z", 3000)
	model := &fakeToolCallingModel{
		responses: []string{long},
	}
	var completeCalls int
	r := NewReviewer(ClientConfig{
		BaseURL: "http://example.com",
		Model:   "test-model",
		newToolCallingModelForTest: func(context.Context) (modelpkg.ToolCallingChatModel, error) {
			return model, nil
		},
		completeJSONForTest: func(_ context.Context, system, _ string, out any) error {
			completeCalls++
			if strings.Contains(system, "repair invalid JSON responses") {
				wire, ok := out.(*map[string]any)
				if !ok {
					return fmt.Errorf("unexpected repair out type %T", out)
				}
				*wire = map[string]any{"decision": badJSONMarshaler{}}
				return nil
			}
			switch v := out.(type) {
			case *interface{}:
				*v = map[string]any{
					"decision":        "comment",
					"summary":         "fallback ok",
					"review_markdown": "fallback markdown",
					"findings":        []map[string]any{},
				}
				return nil
			case *map[string]any:
				*v = map[string]any{
					"decision":        "comment",
					"summary":         "fallback ok",
					"review_markdown": "fallback markdown",
					"findings":        []map[string]any{},
				}
				return nil
			}
			return fmt.Errorf("unexpected completion out type %T", out)
		},
	})

	out, err := r.Review(context.Background(), ReviewInput{
		Goal:          "check",
		RepoRoot:      t.TempDir(),
		CommandOutput: "PASS",
	})
	if err != nil {
		t.Fatalf("Review: %v", err)
	}
	if !out.UsedFallback || out.FallbackSource != "client_completion" {
		t.Fatalf("expected client completion fallback, got %+v", out)
	}
	if completeCalls < 2 {
		t.Fatalf("expected repair and fallback completion calls, got %d", completeCalls)
	}
	if !strings.Contains(out.Markdown, "encode repaired reviewer json failed") {
		t.Fatalf("expected reviewer markdown to preserve eino diagnostics, got %q", out.Markdown)
	}
	if !strings.Contains(out.Markdown, "content=") {
		t.Fatalf("expected reviewer markdown to preserve content preview, got %q", out.Markdown)
	}
	if strings.Contains(out.Markdown, strings.Repeat("z", 2500)) {
		t.Fatalf("expected reviewer markdown preview to be truncated, got %q", out.Markdown)
	}
}

func TestCoderFallbackCompletionRecorderCapturesRawResponse(t *testing.T) {
	rawResponse := `{"summary":"fallback ok","patch":"","commands":[]}`
	records := make([]PromptCallRecord, 0, 4)
	ctx := WithPromptCallRecorder(context.Background(), func(_ context.Context, rec PromptCallRecord) {
		records = append(records, rec)
	})
	c := NewCoder(ClientConfig{
		BaseURL: "http://example.com",
		Model:   "test-model",
		newToolCallingModelForTest: func(context.Context) (modelpkg.ToolCallingChatModel, error) {
			return nil, errors.New("tool path unavailable")
		},
		completeJSONWithRawForTest: func(_ context.Context, _, _ string, out any) (string, error) {
			switch v := out.(type) {
			case *interface{}:
				*v = map[string]any{"summary": "fallback ok", "patch": "", "commands": []string{}}
			default:
				return "", fmt.Errorf("unexpected completion out type %T", out)
			}
			return rawResponse, nil
		},
	})

	out, err := c.Generate(ctx, CoderInput{
		Goal:        "validate repo",
		RepoSummary: t.TempDir(),
	})
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if !out.UsedFallback || out.FallbackSource != "client_completion" {
		t.Fatalf("expected client completion fallback, got %+v", out)
	}
	var sawCompleted bool
	for _, rec := range records {
		if rec.Tool != "coder_prompt" || rec.Path != "client_completion" || rec.Status != "completed" {
			continue
		}
		sawCompleted = true
		if rec.RawResponse != rawResponse {
			t.Fatalf("expected raw response %q, got %q", rawResponse, rec.RawResponse)
		}
		if strings.TrimSpace(rec.SystemPrompt) == "" || strings.TrimSpace(rec.UserPrompt) == "" {
			t.Fatalf("expected prompts in recorder event, got %+v", rec)
		}
	}
	if !sawCompleted {
		t.Fatalf("expected completed coder_prompt record for client_completion, got %+v", records)
	}
}

func TestReviewerRecorderMarksEinoDecodeFailureAsErrorWithRawResponse(t *testing.T) {
	rawEino := "not json from reviewer"
	rawFallback := `{"decision":"comment","summary":"fallback ok","review_markdown":"fallback markdown","findings":[]}`
	records := make([]PromptCallRecord, 0, 8)
	ctx := WithPromptCallRecorder(context.Background(), func(_ context.Context, rec PromptCallRecord) {
		records = append(records, rec)
	})
	model := &fakeToolCallingModel{
		responses: []string{rawEino},
	}
	r := NewReviewer(ClientConfig{
		BaseURL: "http://example.com",
		Model:   "test-model",
		newToolCallingModelForTest: func(context.Context) (modelpkg.ToolCallingChatModel, error) {
			return model, nil
		},
		completeJSONWithRawForTest: func(_ context.Context, system, _ string, out any) (string, error) {
			if strings.Contains(system, "repair invalid JSON responses") {
				return "", errors.New("repair failed")
			}
			switch v := out.(type) {
			case *interface{}:
				*v = map[string]any{
					"decision":        "comment",
					"summary":         "fallback ok",
					"review_markdown": "fallback markdown",
					"findings":        []map[string]any{},
				}
			default:
				return "", fmt.Errorf("unexpected completion out type %T", out)
			}
			return rawFallback, nil
		},
	})

	out, err := r.Review(ctx, ReviewInput{
		Goal:          "check",
		RepoRoot:      t.TempDir(),
		CommandOutput: "PASS",
	})
	if err != nil {
		t.Fatalf("Review: %v", err)
	}
	if !out.UsedFallback || out.FallbackSource != "client_completion" {
		t.Fatalf("expected client completion fallback, got %+v", out)
	}

	var sawEinoError, sawFallbackCompleted, sawWrongEinoCompleted bool
	for _, rec := range records {
		switch {
		case rec.Tool == "reviewer_prompt" && rec.Path == "eino_tool_call" && rec.Status == "error":
			sawEinoError = true
			if rec.RawResponse != rawEino {
				t.Fatalf("expected raw eino response %q, got %q", rawEino, rec.RawResponse)
			}
		case rec.Tool == "reviewer_prompt" && rec.Path == "eino_tool_call" && rec.Status == "completed":
			sawWrongEinoCompleted = true
		case rec.Tool == "reviewer_prompt" && rec.Path == "client_completion" && rec.Status == "completed":
			sawFallbackCompleted = true
			if rec.RawResponse != rawFallback {
				t.Fatalf("expected raw fallback response %q, got %q", rawFallback, rec.RawResponse)
			}
		}
	}
	if !sawEinoError || sawWrongEinoCompleted || !sawFallbackCompleted {
		t.Fatalf("expected eino error + fallback completed without false eino completed, got %+v", records)
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

package agent

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// responsesTransport is an http.RoundTripper that translates between
// OpenAI chat/completions format (used by go-openai SDK) and the
// responses API format (used by endpoints like cc2.caaa.tech).
//
// It intercepts outgoing requests to /chat/completions, rewrites the
// URL to /responses, transforms the request body, and converts the
// response back to chat/completions format so the SDK can parse it.
type responsesTransport struct {
	inner http.RoundTripper
}

func newResponsesTransport(inner http.RoundTripper) *responsesTransport {
	if inner == nil {
		inner = http.DefaultTransport
	}
	return &responsesTransport{inner: inner}
}

func (t *responsesTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	// Only intercept chat/completions requests.
	if !strings.HasSuffix(req.URL.Path, "/chat/completions") {
		return t.inner.RoundTrip(req)
	}

	// Reject streaming — not supported via this adapter.
	body, err := io.ReadAll(req.Body)
	req.Body.Close()
	if err != nil {
		return nil, fmt.Errorf("responses_transport: read request body: %w", err)
	}

	var chatReq chatCompletionsRequest
	if err := json.Unmarshal(body, &chatReq); err != nil {
		return nil, fmt.Errorf("responses_transport: decode chat request: %w", err)
	}
	if chatReq.Stream {
		return nil, fmt.Errorf("responses_transport: streaming is not supported via responses API adapter")
	}

	// Transform request.
	respReq := transformChatToResponses(chatReq)
	respBody, err := json.Marshal(respReq)
	if err != nil {
		return nil, fmt.Errorf("responses_transport: encode responses request: %w", err)
	}

	// Rewrite URL: /v1/chat/completions → /v1/responses
	newURL := *req.URL
	newURL.Path = strings.Replace(newURL.Path, "/chat/completions", "/responses", 1)

	newReq, err := http.NewRequestWithContext(req.Context(), req.Method, newURL.String(), bytes.NewReader(respBody))
	if err != nil {
		return nil, fmt.Errorf("responses_transport: create request: %w", err)
	}
	// Copy headers.
	for k, vs := range req.Header {
		for _, v := range vs {
			newReq.Header.Add(k, v)
		}
	}
	newReq.ContentLength = int64(len(respBody))

	// Send request.
	resp, err := t.inner.RoundTrip(newReq)
	if err != nil {
		return resp, err
	}

	// Only transform successful JSON responses (2xx).
	// Non-2xx (3xx redirects, 4xx, 5xx) are returned as-is for SDK error handling.
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return resp, nil
	}

	// Read and transform response.
	respBodyBytes, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return nil, fmt.Errorf("responses_transport: read response body: %w", err)
	}

	// Guard against non-JSON responses (HTML error pages, plain text, etc.).
	ct := resp.Header.Get("Content-Type")
	if ct != "" && !strings.Contains(ct, "json") {
		preview := string(respBodyBytes)
		if len(preview) > 256 {
			preview = preview[:256]
		}
		return nil, fmt.Errorf("responses_transport: unexpected content-type %q (status %d, body: %s)", ct, resp.StatusCode, strings.TrimSpace(preview))
	}

	var respAPI responsesAPIResponse
	if err := json.Unmarshal(respBodyBytes, &respAPI); err != nil {
		preview := string(respBodyBytes)
		if len(preview) > 256 {
			preview = preview[:256]
		}
		return nil, fmt.Errorf("responses_transport: decode responses reply (status %d, content-type %q, body: %s): %w", resp.StatusCode, ct, strings.TrimSpace(preview), err)
	}

	chatResp := transformResponsesToChatCompletions(respAPI)
	chatRespBody, err := json.Marshal(chatResp)
	if err != nil {
		return nil, fmt.Errorf("responses_transport: encode chat response: %w", err)
	}

	resp.Body = io.NopCloser(bytes.NewReader(chatRespBody))
	resp.ContentLength = int64(len(chatRespBody))
	resp.Header.Set("Content-Length", fmt.Sprint(len(chatRespBody)))
	return resp, nil
}

// ── Request types (chat/completions → responses) ──

type chatCompletionsRequest struct {
	Model               string                   `json:"model"`
	Messages            []chatMessage            `json:"messages"`
	Tools               []chatTool               `json:"tools,omitempty"`
	ToolChoice          any                      `json:"tool_choice,omitempty"`
	Temperature         *float64                 `json:"temperature,omitempty"`
	TopP                *float64                 `json:"top_p,omitempty"`
	MaxTokens           *int                     `json:"max_tokens,omitempty"`
	MaxCompletionTokens *int                     `json:"max_completion_tokens,omitempty"`
	Stream              bool                     `json:"stream,omitempty"`
	Stop                any                      `json:"stop,omitempty"`
	N                   *int                     `json:"n,omitempty"`
	Extra               map[string]json.RawMessage `json:"-"`
}

type chatMessage struct {
	Role       string          `json:"role"`
	Content    any             `json:"content"`
	ToolCalls  []chatToolCall  `json:"tool_calls,omitempty"`
	ToolCallID string          `json:"tool_call_id,omitempty"`
}

type chatToolCall struct {
	ID       string           `json:"id"`
	Type     string           `json:"type"`
	Function chatFunctionCall `json:"function"`
}

type chatFunctionCall struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

type chatTool struct {
	Type     string        `json:"type"`
	Function *chatFunction `json:"function,omitempty"`
}

type chatFunction struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	Parameters  json.RawMessage `json:"parameters,omitempty"`
	Strict      *bool           `json:"strict,omitempty"`
}

// ── Responses API types ──

type responsesAPIRequest struct {
	Model           string        `json:"model"`
	Input           []any         `json:"input"`
	Tools           []respTool    `json:"tools,omitempty"`
	ToolChoice      any           `json:"tool_choice,omitempty"`
	Temperature     *float64      `json:"temperature,omitempty"`
	TopP            *float64      `json:"top_p,omitempty"`
	MaxOutputTokens *int          `json:"max_output_tokens,omitempty"`
	Store           bool          `json:"store"`
}

type respTool struct {
	Type        string          `json:"type"`
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	Parameters  json.RawMessage `json:"parameters,omitempty"`
	Strict      *bool           `json:"strict,omitempty"`
}

type respInputMessage struct {
	Role    string `json:"role"`
	Content any    `json:"content"`
}

type respFunctionCall struct {
	Type      string `json:"type"`
	CallID    string `json:"call_id"`
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

type respFunctionCallOutput struct {
	Type   string `json:"type"`
	CallID string `json:"call_id"`
	Output string `json:"output"`
}

type responsesAPIResponse struct {
	ID        string            `json:"id"`
	Object    string            `json:"object"`
	CreatedAt int64             `json:"created_at"`
	Model     string            `json:"model"`
	Status    string            `json:"status"`
	Output    []json.RawMessage `json:"output"`
	Usage     *respUsage        `json:"usage,omitempty"`
}

type respUsage struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
	TotalTokens  int `json:"total_tokens"`
}

type respOutputMessage struct {
	Type    string              `json:"type"`
	ID      string              `json:"id"`
	Role    string              `json:"role"`
	Status  string              `json:"status"`
	Content []respContentPart   `json:"content"`
}

type respContentPart struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type respOutputFunctionCall struct {
	Type      string `json:"type"`
	ID        string `json:"id"`
	CallID    string `json:"call_id"`
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
	Status    string `json:"status"`
}

// ── Chat/Completions response types ──

type chatCompletionsResponse struct {
	ID      string            `json:"id"`
	Object  string            `json:"object"`
	Created int64             `json:"created"`
	Model   string            `json:"model"`
	Choices []chatChoice      `json:"choices"`
	Usage   *chatUsage        `json:"usage,omitempty"`
}

type chatChoice struct {
	Index        int              `json:"index"`
	Message      chatChoiceMsg    `json:"message"`
	FinishReason string           `json:"finish_reason"`
}

type chatChoiceMsg struct {
	Role      string         `json:"role"`
	Content   *string        `json:"content"`
	ToolCalls []chatToolCall `json:"tool_calls,omitempty"`
}

type chatUsage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

// ── Transformation functions ──

func transformChatToResponses(chat chatCompletionsRequest) responsesAPIRequest {
	req := responsesAPIRequest{
		Model:       chat.Model,
		Temperature: chat.Temperature,
		TopP:        chat.TopP,
		ToolChoice:  transformToolChoice(chat.ToolChoice),
		Store:       false,
	}

	// max_tokens / max_completion_tokens → max_output_tokens
	if chat.MaxCompletionTokens != nil {
		req.MaxOutputTokens = chat.MaxCompletionTokens
	} else if chat.MaxTokens != nil {
		req.MaxOutputTokens = chat.MaxTokens
	}

	// Transform messages → input items.
	for _, msg := range chat.Messages {
		switch msg.Role {
		case "system", "user":
			req.Input = append(req.Input, respInputMessage{
				Role:    msg.Role,
				Content: msg.Content,
			})
		case "assistant":
			if len(msg.ToolCalls) > 0 {
				// Assistant message with tool calls → function_call items in input.
				for _, tc := range msg.ToolCalls {
					req.Input = append(req.Input, respFunctionCall{
						Type:      "function_call",
						CallID:    tc.ID,
						Name:      tc.Function.Name,
						Arguments: tc.Function.Arguments,
					})
				}
			} else {
				req.Input = append(req.Input, respInputMessage{
					Role:    "assistant",
					Content: msg.Content,
				})
			}
		case "tool":
			content := ""
			if msg.Content != nil {
				if s, ok := msg.Content.(string); ok {
					content = s
				} else {
					b, _ := json.Marshal(msg.Content)
					content = string(b)
				}
			}
			req.Input = append(req.Input, respFunctionCallOutput{
				Type:   "function_call_output",
				CallID: msg.ToolCallID,
				Output: content,
			})
		}
	}

	// Transform tools: flatten function wrapper.
	for _, t := range chat.Tools {
		if t.Function == nil {
			continue
		}
		req.Tools = append(req.Tools, respTool{
			Type:        "function",
			Name:        t.Function.Name,
			Description: t.Function.Description,
			Parameters:  t.Function.Parameters,
			Strict:      t.Function.Strict,
		})
	}

	return req
}

func transformResponsesToChatCompletions(resp responsesAPIResponse) chatCompletionsResponse {
	chatResp := chatCompletionsResponse{
		ID:      resp.ID,
		Object:  "chat.completion",
		Created: resp.CreatedAt,
		Model:   resp.Model,
	}
	if chatResp.Created == 0 {
		chatResp.Created = time.Now().Unix()
	}
	if resp.Usage != nil {
		chatResp.Usage = &chatUsage{
			PromptTokens:     resp.Usage.InputTokens,
			CompletionTokens: resp.Usage.OutputTokens,
			TotalTokens:      resp.Usage.TotalTokens,
		}
	}

	// Parse output items.
	var contentParts []string
	var toolCalls []chatToolCall

	for _, raw := range resp.Output {
		var base struct {
			Type string `json:"type"`
		}
		if err := json.Unmarshal(raw, &base); err != nil {
			continue
		}

		switch base.Type {
		case "message":
			var msg respOutputMessage
			if err := json.Unmarshal(raw, &msg); err != nil {
				continue
			}
			for _, part := range msg.Content {
				if part.Type == "output_text" || part.Type == "text" {
					contentParts = append(contentParts, part.Text)
				}
			}
		case "function_call":
			var fc respOutputFunctionCall
			if err := json.Unmarshal(raw, &fc); err != nil {
				continue
			}
			callID := fc.CallID
			if callID == "" {
				callID = fc.ID
			}
			toolCalls = append(toolCalls, chatToolCall{
				ID:   callID,
				Type: "function",
				Function: chatFunctionCall{
					Name:      fc.Name,
					Arguments: fc.Arguments,
				},
			})
		}
	}

	choice := chatChoice{Index: 0}
	choice.Message.Role = "assistant"

	if len(toolCalls) > 0 {
		choice.Message.ToolCalls = toolCalls
		choice.FinishReason = "tool_calls"
		// Some SDKs expect content to be null when tool_calls present.
		choice.Message.Content = nil
	} else {
		joined := strings.Join(contentParts, "")
		choice.Message.Content = &joined
		choice.FinishReason = "stop"
	}

	chatResp.Choices = []chatChoice{choice}
	return chatResp
}

// transformToolChoice converts chat/completions tool_choice format to responses format.
// Scalar values ("auto", "none", "required") pass through unchanged.
// Specific function selection: {"type":"function","function":{"name":"X"}}
// is flattened to:            {"type":"function","name":"X"}
func transformToolChoice(tc any) any {
	if tc == nil {
		return nil
	}
	// String values ("auto", "none", "required") pass through.
	if _, ok := tc.(string); ok {
		return tc
	}
	// Map: check for nested function object.
	m, ok := tc.(map[string]any)
	if !ok {
		return tc
	}
	fn, hasFn := m["function"]
	if !hasFn {
		return tc
	}
	fnMap, ok := fn.(map[string]any)
	if !ok {
		return tc
	}
	name, _ := fnMap["name"].(string)
	if name == "" {
		return tc
	}
	return map[string]string{
		"type": "function",
		"name": name,
	}
}

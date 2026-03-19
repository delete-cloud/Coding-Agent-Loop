package agent

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestTransformChatToResponses_TextMessage(t *testing.T) {
	chat := chatCompletionsRequest{
		Model: "gpt-4o",
		Messages: []chatMessage{
			{Role: "system", Content: "You are helpful."},
			{Role: "user", Content: "Hello"},
		},
		Tools: []chatTool{
			{
				Type: "function",
				Function: &chatFunction{
					Name:        "get_weather",
					Description: "Get weather",
					Parameters:  json.RawMessage(`{"type":"object","properties":{"loc":{"type":"string"}}}`),
				},
			},
		},
	}
	maxTok := 1000
	chat.MaxTokens = &maxTok

	resp := transformChatToResponses(chat)

	if resp.Model != "gpt-4o" {
		t.Errorf("model = %q, want gpt-4o", resp.Model)
	}
	if resp.Store != false {
		t.Error("store should be false")
	}
	if resp.MaxOutputTokens == nil || *resp.MaxOutputTokens != 1000 {
		t.Errorf("max_output_tokens = %v, want 1000", resp.MaxOutputTokens)
	}
	if len(resp.Input) != 2 {
		t.Fatalf("input len = %d, want 2", len(resp.Input))
	}
	// Check tools flattened.
	if len(resp.Tools) != 1 {
		t.Fatalf("tools len = %d, want 1", len(resp.Tools))
	}
	if resp.Tools[0].Name != "get_weather" {
		t.Errorf("tool name = %q, want get_weather", resp.Tools[0].Name)
	}
}

func TestTransformChatToResponses_ToolCallMessages(t *testing.T) {
	chat := chatCompletionsRequest{
		Model: "gpt-4o",
		Messages: []chatMessage{
			{Role: "user", Content: "What's the weather?"},
			{
				Role: "assistant",
				ToolCalls: []chatToolCall{
					{
						ID:   "call_123",
						Type: "function",
						Function: chatFunctionCall{
							Name:      "get_weather",
							Arguments: `{"loc":"Paris"}`,
						},
					},
				},
			},
			{
				Role:       "tool",
				ToolCallID: "call_123",
				Content:    "22°C, sunny",
			},
		},
	}

	resp := transformChatToResponses(chat)

	if len(resp.Input) != 3 {
		t.Fatalf("input len = %d, want 3", len(resp.Input))
	}

	// Item 1: function_call
	fc, ok := resp.Input[1].(respFunctionCall)
	if !ok {
		t.Fatalf("input[1] type = %T, want respFunctionCall", resp.Input[1])
	}
	if fc.Type != "function_call" || fc.CallID != "call_123" || fc.Name != "get_weather" {
		t.Errorf("function_call = %+v", fc)
	}

	// Item 2: function_call_output
	fco, ok := resp.Input[2].(respFunctionCallOutput)
	if !ok {
		t.Fatalf("input[2] type = %T, want respFunctionCallOutput", resp.Input[2])
	}
	if fco.Type != "function_call_output" || fco.CallID != "call_123" || fco.Output != "22°C, sunny" {
		t.Errorf("function_call_output = %+v", fco)
	}
}

func TestTransformResponsesToChatCompletions_TextOutput(t *testing.T) {
	resp := responsesAPIResponse{
		ID:        "resp_abc",
		Object:    "response",
		CreatedAt: 1700000000,
		Model:     "gpt-4o",
		Status:    "completed",
		Output: []json.RawMessage{
			json.RawMessage(`{"type":"message","id":"msg_1","role":"assistant","status":"completed","content":[{"type":"output_text","text":"Hello!"}]}`),
		},
		Usage: &respUsage{InputTokens: 10, OutputTokens: 5, TotalTokens: 15},
	}

	chat := transformResponsesToChatCompletions(resp)

	if chat.ID != "resp_abc" {
		t.Errorf("id = %q, want resp_abc", chat.ID)
	}
	if chat.Object != "chat.completion" {
		t.Errorf("object = %q, want chat.completion", chat.Object)
	}
	if len(chat.Choices) != 1 {
		t.Fatalf("choices len = %d, want 1", len(chat.Choices))
	}
	choice := chat.Choices[0]
	if choice.FinishReason != "stop" {
		t.Errorf("finish_reason = %q, want stop", choice.FinishReason)
	}
	if choice.Message.Content == nil || *choice.Message.Content != "Hello!" {
		t.Errorf("content = %v, want Hello!", choice.Message.Content)
	}
	if chat.Usage == nil || chat.Usage.PromptTokens != 10 || chat.Usage.CompletionTokens != 5 {
		t.Errorf("usage = %+v", chat.Usage)
	}
}

func TestTransformResponsesToChatCompletions_ToolCallOutput(t *testing.T) {
	resp := responsesAPIResponse{
		ID:        "resp_xyz",
		CreatedAt: 1700000000,
		Model:     "gpt-4o",
		Output: []json.RawMessage{
			json.RawMessage(`{"type":"function_call","id":"fc_1","call_id":"call_456","name":"get_weather","arguments":"{\"loc\":\"Paris\"}","status":"completed"}`),
		},
	}

	chat := transformResponsesToChatCompletions(resp)

	if len(chat.Choices) != 1 {
		t.Fatalf("choices len = %d, want 1", len(chat.Choices))
	}
	choice := chat.Choices[0]
	if choice.FinishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want tool_calls", choice.FinishReason)
	}
	if choice.Message.Content != nil {
		t.Errorf("content should be nil for tool_calls, got %v", choice.Message.Content)
	}
	if len(choice.Message.ToolCalls) != 1 {
		t.Fatalf("tool_calls len = %d, want 1", len(choice.Message.ToolCalls))
	}
	tc := choice.Message.ToolCalls[0]
	if tc.ID != "call_456" || tc.Function.Name != "get_weather" {
		t.Errorf("tool_call = %+v", tc)
	}
}

func TestTransformToolChoice(t *testing.T) {
	tests := []struct {
		name string
		in   any
		want any
	}{
		{"nil", nil, nil},
		{"auto string", "auto", "auto"},
		{"none string", "none", "none"},
		{"required string", "required", "required"},
		{
			"specific function",
			map[string]any{
				"type":     "function",
				"function": map[string]any{"name": "get_weather"},
			},
			map[string]string{"type": "function", "name": "get_weather"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := transformToolChoice(tt.in)
			gotJSON, _ := json.Marshal(got)
			wantJSON, _ := json.Marshal(tt.want)
			if string(gotJSON) != string(wantJSON) {
				t.Errorf("transformToolChoice(%v) = %s, want %s", tt.in, gotJSON, wantJSON)
			}
		})
	}
}

func TestTransformChatToResponses_ToolChoiceSpecificFunction(t *testing.T) {
	chat := chatCompletionsRequest{
		Model: "gpt-4o",
		Messages: []chatMessage{
			{Role: "user", Content: "Hello"},
		},
		Tools: []chatTool{
			{Type: "function", Function: &chatFunction{Name: "get_weather", Description: "Get weather"}},
		},
		ToolChoice: map[string]any{
			"type":     "function",
			"function": map[string]any{"name": "get_weather"},
		},
	}

	resp := transformChatToResponses(chat)

	// Verify tool_choice was flattened.
	tcJSON, _ := json.Marshal(resp.ToolChoice)
	var tc map[string]string
	if err := json.Unmarshal(tcJSON, &tc); err != nil {
		t.Fatalf("tool_choice should be flat map: %v", err)
	}
	if tc["type"] != "function" || tc["name"] != "get_weather" {
		t.Errorf("tool_choice = %s, want {type:function, name:get_weather}", tcJSON)
	}
}

// TestRoundTrip_URLRewrite verifies the transport rewrites URL and transforms body.
func TestRoundTrip_URLRewrite(t *testing.T) {
	var gotPath string
	var gotBody []byte

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotBody, _ = io.ReadAll(r.Body)

		// Return a minimal responses API response.
		resp := responsesAPIResponse{
			ID:        "resp_test",
			Object:    "response",
			CreatedAt: 1700000000,
			Model:     "gpt-4o",
			Status:    "completed",
			Output: []json.RawMessage{
				json.RawMessage(`{"type":"message","id":"msg_1","role":"assistant","status":"completed","content":[{"type":"output_text","text":"Hi"}]}`),
			},
			Usage: &respUsage{InputTokens: 5, OutputTokens: 2, TotalTokens: 7},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	transport := newResponsesTransport(server.Client().Transport)
	client := &http.Client{Transport: transport}

	chatReq := chatCompletionsRequest{
		Model: "gpt-4o",
		Messages: []chatMessage{
			{Role: "user", Content: "Hi"},
		},
	}
	body, _ := json.Marshal(chatReq)
	req, _ := http.NewRequest("POST", server.URL+"/v1/chat/completions", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		t.Fatalf("RoundTrip error: %v", err)
	}
	defer resp.Body.Close()

	// Verify URL was rewritten.
	if gotPath != "/v1/responses" {
		t.Errorf("server saw path = %q, want /v1/responses", gotPath)
	}

	// Verify request body was transformed (has "input" not "messages").
	var reqJSON map[string]any
	if err := json.Unmarshal(gotBody, &reqJSON); err != nil {
		t.Fatalf("unmarshal request: %v", err)
	}
	if _, ok := reqJSON["input"]; !ok {
		t.Error("request body should have 'input' field")
	}
	if _, ok := reqJSON["messages"]; ok {
		t.Error("request body should not have 'messages' field")
	}
	if store, ok := reqJSON["store"].(bool); !ok || store != false {
		t.Errorf("store = %v, want false", reqJSON["store"])
	}

	// Verify response was transformed back to chat/completions format.
	respBody, _ := io.ReadAll(resp.Body)
	var chatResp chatCompletionsResponse
	if err := json.Unmarshal(respBody, &chatResp); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}
	if chatResp.Object != "chat.completion" {
		t.Errorf("response object = %q, want chat.completion", chatResp.Object)
	}
	if len(chatResp.Choices) != 1 || chatResp.Choices[0].Message.Content == nil || *chatResp.Choices[0].Message.Content != "Hi" {
		t.Errorf("response content unexpected: %+v", chatResp)
	}
}

// TestRoundTrip_ToolCallingRoundTrip tests a full tool-calling conversation.
func TestRoundTrip_ToolCallingRoundTrip(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var reqJSON map[string]any
		json.Unmarshal(body, &reqJSON)

		// Check if this is the initial call or the tool result call.
		input := reqJSON["input"].([]any)
		hasFunctionOutput := false
		for _, item := range input {
			if m, ok := item.(map[string]any); ok {
				if m["type"] == "function_call_output" {
					hasFunctionOutput = true
				}
			}
		}

		var resp responsesAPIResponse
		if hasFunctionOutput {
			// Second call: return text.
			resp = responsesAPIResponse{
				ID: "resp_2", Object: "response", CreatedAt: 1700000000, Model: "gpt-4o",
				Output: []json.RawMessage{
					json.RawMessage(`{"type":"message","id":"msg_2","role":"assistant","status":"completed","content":[{"type":"output_text","text":"It is 22°C in Paris."}]}`),
				},
			}
		} else {
			// First call: return tool call.
			resp = responsesAPIResponse{
				ID: "resp_1", Object: "response", CreatedAt: 1700000000, Model: "gpt-4o",
				Output: []json.RawMessage{
					json.RawMessage(`{"type":"function_call","id":"fc_1","call_id":"call_789","name":"get_weather","arguments":"{\"loc\":\"Paris\"}","status":"completed"}`),
				},
			}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	transport := newResponsesTransport(server.Client().Transport)
	client := &http.Client{Transport: transport}

	// First request: user message → expects tool call back.
	chatReq1 := chatCompletionsRequest{
		Model: "gpt-4o",
		Messages: []chatMessage{
			{Role: "user", Content: "Weather in Paris?"},
		},
		Tools: []chatTool{
			{Type: "function", Function: &chatFunction{Name: "get_weather", Description: "Get weather", Parameters: json.RawMessage(`{}`)}},
		},
	}
	body1, _ := json.Marshal(chatReq1)
	req1, _ := http.NewRequest("POST", server.URL+"/v1/chat/completions", strings.NewReader(string(body1)))
	req1.Header.Set("Content-Type", "application/json")

	resp1, err := client.Do(req1)
	if err != nil {
		t.Fatalf("first call error: %v", err)
	}
	resp1Body, _ := io.ReadAll(resp1.Body)
	resp1.Body.Close()

	var chatResp1 chatCompletionsResponse
	json.Unmarshal(resp1Body, &chatResp1)

	if chatResp1.Choices[0].FinishReason != "tool_calls" {
		t.Fatalf("expected tool_calls, got %q", chatResp1.Choices[0].FinishReason)
	}
	tc := chatResp1.Choices[0].Message.ToolCalls[0]

	// Second request: include tool result → expects text back.
	chatReq2 := chatCompletionsRequest{
		Model: "gpt-4o",
		Messages: []chatMessage{
			{Role: "user", Content: "Weather in Paris?"},
			{
				Role:      "assistant",
				ToolCalls: []chatToolCall{tc},
			},
			{
				Role:       "tool",
				ToolCallID: tc.ID,
				Content:    "22°C, sunny",
			},
		},
	}
	body2, _ := json.Marshal(chatReq2)
	req2, _ := http.NewRequest("POST", server.URL+"/v1/chat/completions", strings.NewReader(string(body2)))
	req2.Header.Set("Content-Type", "application/json")

	resp2, err := client.Do(req2)
	if err != nil {
		t.Fatalf("second call error: %v", err)
	}
	resp2Body, _ := io.ReadAll(resp2.Body)
	resp2.Body.Close()

	var chatResp2 chatCompletionsResponse
	json.Unmarshal(resp2Body, &chatResp2)

	if chatResp2.Choices[0].FinishReason != "stop" {
		t.Fatalf("expected stop, got %q", chatResp2.Choices[0].FinishReason)
	}
	if chatResp2.Choices[0].Message.Content == nil || *chatResp2.Choices[0].Message.Content != "It is 22°C in Paris." {
		t.Errorf("unexpected content: %v", chatResp2.Choices[0].Message.Content)
	}
}

// TestRoundTrip_StreamRejected ensures streaming is explicitly rejected.
func TestRoundTrip_StreamRejected(t *testing.T) {
	transport := newResponsesTransport(http.DefaultTransport)
	client := &http.Client{Transport: transport}

	chatReq := chatCompletionsRequest{
		Model:  "gpt-4o",
		Stream: true,
		Messages: []chatMessage{
			{Role: "user", Content: "Hi"},
		},
	}
	body, _ := json.Marshal(chatReq)
	req, _ := http.NewRequest("POST", "http://localhost:1/v1/chat/completions", strings.NewReader(string(body)))

	_, err := client.Do(req)
	if err == nil || !strings.Contains(err.Error(), "streaming is not supported") {
		t.Errorf("expected streaming error, got: %v", err)
	}
}

// TestRoundTrip_NonChatEndpointPassthrough ensures non-chat endpoints are not intercepted.
func TestRoundTrip_NonChatEndpointPassthrough(t *testing.T) {
	var gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.WriteHeader(200)
		w.Write([]byte(`{"ok":true}`))
	}))
	defer server.Close()

	transport := newResponsesTransport(server.Client().Transport)
	client := &http.Client{Transport: transport}

	req, _ := http.NewRequest("GET", server.URL+"/v1/models", nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatalf("error: %v", err)
	}
	resp.Body.Close()

	if gotPath != "/v1/models" {
		t.Errorf("path = %q, want /v1/models (should not be rewritten)", gotPath)
	}
}

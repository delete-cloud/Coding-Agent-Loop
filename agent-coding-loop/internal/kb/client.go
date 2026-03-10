package kb

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

const kbSearchMaxAttempts = 3

type Client struct {
	BaseURL    string
	HTTPClient *http.Client
}

func NewClient(baseURL string) *Client {
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	return &Client{
		BaseURL: baseURL,
		HTTPClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

type SearchRequest struct {
	Query     string `json:"query"`
	TopK      int    `json:"top_k,omitempty"`
	QueryType string `json:"query_type,omitempty"`
	Where     string `json:"where,omitempty"`
}

type SearchHit struct {
	ID      string   `json:"id"`
	Path    string   `json:"path"`
	Heading string   `json:"heading"`
	Start   int      `json:"start"`
	End     int      `json:"end"`
	Text    string   `json:"text"`
	Score   *float64 `json:"score,omitempty"`
}

type SearchResponse struct {
	Hits []SearchHit `json:"hits"`
}

func (c *Client) Search(ctx context.Context, req SearchRequest) (SearchResponse, error) {
	if strings.TrimSpace(c.BaseURL) == "" {
		return SearchResponse{}, fmt.Errorf("kb base_url is empty")
	}
	if c.HTTPClient == nil {
		c.HTTPClient = &http.Client{Timeout: 30 * time.Second}
	}
	b, err := json.Marshal(req)
	if err != nil {
		return SearchResponse{}, err
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/search", bytes.NewReader(b))
	if err != nil {
		return SearchResponse{}, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	var resp *http.Response
	for attempt := 0; attempt < kbSearchMaxAttempts; attempt++ {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return SearchResponse{}, ctx.Err()
			case <-time.After(time.Duration(attempt) * 250 * time.Millisecond):
			}
			httpReq, err = http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/search", bytes.NewReader(b))
			if err != nil {
				return SearchResponse{}, err
			}
			httpReq.Header.Set("Content-Type", "application/json")
		}
		resp, err = c.HTTPClient.Do(httpReq)
		if err == nil {
			break
		}
		if !isRetryableKBTransportError(err) || attempt >= kbSearchMaxAttempts-1 {
			return SearchResponse{}, err
		}
	}
	defer resp.Body.Close()
	body, _ := readAllLimit(resp, 2<<20)
	if resp.StatusCode/100 != 2 {
		return SearchResponse{}, fmt.Errorf("kb search failed: status=%d body=%s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	var out SearchResponse
	if err := json.Unmarshal(body, &out); err != nil {
		return SearchResponse{}, fmt.Errorf("decode kb search response failed: %w; body=%s", err, strings.TrimSpace(string(body)))
	}
	return out, nil
}

func isRetryableKBTransportError(err error) bool {
	if err == nil {
		return false
	}
	s := strings.ToLower(strings.TrimSpace(err.Error()))
	if strings.Contains(s, "can't assign requested address") {
		return true
	}
	if strings.Contains(s, "unexpected eof") || strings.Contains(s, ": eof") || strings.HasSuffix(s, "eof") {
		return true
	}
	if strings.Contains(s, "connection reset") || strings.Contains(s, "broken pipe") {
		return true
	}
	if strings.Contains(s, "timeout") || strings.Contains(s, "temporary") {
		return true
	}
	return false
}

type IndexRequest struct {
	Roots        []string `json:"roots,omitempty"`
	Exts         []string `json:"exts,omitempty"`
	ChunkSize    int      `json:"chunk_size,omitempty"`
	Overlap      int      `json:"overlap,omitempty"`
	MaxFileBytes int      `json:"max_file_bytes,omitempty"`
}

type IndexResponse struct {
	Indexed int    `json:"indexed"`
	DBPath  string `json:"db_path"`
	Table   string `json:"table"`
}

func (c *Client) Index(ctx context.Context, req IndexRequest) (IndexResponse, error) {
	if strings.TrimSpace(c.BaseURL) == "" {
		return IndexResponse{}, fmt.Errorf("kb base_url is empty")
	}
	if c.HTTPClient == nil {
		c.HTTPClient = &http.Client{Timeout: 30 * time.Second}
	}
	b, err := json.Marshal(req)
	if err != nil {
		return IndexResponse{}, err
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/index", bytes.NewReader(b))
	if err != nil {
		return IndexResponse{}, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	resp, err := c.HTTPClient.Do(httpReq)
	if err != nil {
		return IndexResponse{}, err
	}
	defer resp.Body.Close()
	body, _ := readAllLimit(resp, 2<<20)
	if resp.StatusCode/100 != 2 {
		return IndexResponse{}, fmt.Errorf("kb index failed: status=%d body=%s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	var out IndexResponse
	if err := json.Unmarshal(body, &out); err != nil {
		return IndexResponse{}, fmt.Errorf("decode kb index response failed: %w; body=%s", err, strings.TrimSpace(string(body)))
	}
	return out, nil
}

func readAllLimit(resp *http.Response, max int64) ([]byte, error) {
	if resp == nil || resp.Body == nil {
		return nil, nil
	}
	var b bytes.Buffer
	_, err := b.ReadFrom(&limitedReader{r: resp.Body, max: max})
	return b.Bytes(), err
}

type limitedReader struct {
	r   interface{ Read([]byte) (int, error) }
	max int64
	n   int64
}

func (r *limitedReader) Read(p []byte) (int, error) {
	if r.max > 0 && r.n >= r.max {
		return 0, fmt.Errorf("response too large")
	}
	if r.max > 0 && int64(len(p)) > (r.max-r.n) {
		p = p[:r.max-r.n]
	}
	n, err := r.r.Read(p)
	r.n += int64(n)
	return n, err
}

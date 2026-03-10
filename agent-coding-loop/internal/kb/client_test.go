package kb

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

type retryRoundTripper struct {
	count int
}

func (r *retryRoundTripper) RoundTrip(_ *http.Request) (*http.Response, error) {
	r.count++
	if r.count < 3 {
		return nil, io.EOF
	}
	body := bytes.NewBufferString(`{"hits":[{"id":"a","path":"x.md","heading":"h","start":0,"end":10,"text":"t"}]}`)
	return &http.Response{
		StatusCode: 200,
		Body:       io.NopCloser(body),
		Header:     make(http.Header),
	}, nil
}

func TestClientSearch(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/search", func(w http.ResponseWriter, r *http.Request) {
		var in SearchRequest
		_ = json.NewDecoder(r.Body).Decode(&in)
		_ = json.NewEncoder(w).Encode(SearchResponse{
			Hits: []SearchHit{
				{ID: "a", Path: "x.md", Heading: "h", Start: 0, End: 10, Text: "t"},
			},
		})
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	c := NewClient(srv.URL)
	out, err := c.Search(context.Background(), SearchRequest{Query: "q", TopK: 3})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(out.Hits) != 1 {
		t.Fatalf("expected 1 hit, got %d", len(out.Hits))
	}
	if out.Hits[0].Path != "x.md" {
		t.Fatalf("unexpected path: %s", out.Hits[0].Path)
	}
}

func TestClientIndex(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/index", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(IndexResponse{Indexed: 2, DBPath: "p", Table: "t"})
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	c := NewClient(srv.URL)
	out, err := c.Index(context.Background(), IndexRequest{Roots: []string{"."}})
	if err != nil {
		t.Fatalf("Index: %v", err)
	}
	if out.Indexed != 2 {
		t.Fatalf("expected indexed=2, got %d", out.Indexed)
	}
}

func TestClientSearchRetriesTransientTransportError(t *testing.T) {
	rt := &retryRoundTripper{}
	c := &Client{
		BaseURL: "http://example.test",
		HTTPClient: &http.Client{
			Transport: rt,
		},
	}
	out, err := c.Search(context.Background(), SearchRequest{Query: "q", TopK: 3})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if rt.count != 3 {
		t.Fatalf("expected 3 attempts, got %d", rt.count)
	}
	if len(out.Hits) != 1 || out.Hits[0].Path != "x.md" {
		t.Fatalf("unexpected search result: %+v", out)
	}
}

package sonic

import (
	"encoding/json"
	"fmt"
)

// JSONNode is a minimal compatible return object for GetFromString used by Eino.
type JSONNode struct {
	value any
}

func (n JSONNode) MarshalJSON() ([]byte, error) {
	return json.Marshal(n.value)
}

func Marshal(v any) ([]byte, error) {
	return json.Marshal(v)
}

func MarshalString(v any) (string, error) {
	b, err := json.Marshal(v)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

func MarshalIndent(v any, prefix, indent string) ([]byte, error) {
	return json.MarshalIndent(v, prefix, indent)
}

func Unmarshal(data []byte, v any) error {
	return json.Unmarshal(data, v)
}

func UnmarshalString(data string, v any) error {
	return json.Unmarshal([]byte(data), v)
}

func GetFromString(data string, path ...any) (JSONNode, error) {
	var current any
	if err := json.Unmarshal([]byte(data), &current); err != nil {
		return JSONNode{}, err
	}
	for _, p := range path {
		switch key := p.(type) {
		case string:
			obj, ok := current.(map[string]any)
			if !ok {
				return JSONNode{}, fmt.Errorf("path segment %q expects object", key)
			}
			next, ok := obj[key]
			if !ok {
				return JSONNode{}, fmt.Errorf("path segment %q not found", key)
			}
			current = next
		case int:
			arr, ok := current.([]any)
			if !ok {
				return JSONNode{}, fmt.Errorf("path segment %d expects array", key)
			}
			if key < 0 || key >= len(arr) {
				return JSONNode{}, fmt.Errorf("path index %d out of range", key)
			}
			current = arr[key]
		default:
			return JSONNode{}, fmt.Errorf("unsupported path segment type %T", p)
		}
	}
	return JSONNode{value: current}, nil
}

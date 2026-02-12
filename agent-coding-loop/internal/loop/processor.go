package loop

import "fmt"

type DoomLoopDetector struct {
	threshold int
	lastTool  string
	lastInput string
	count     int
}

func NewDoomLoopDetector(threshold int) *DoomLoopDetector {
	if threshold < 1 {
		threshold = 3
	}
	return &DoomLoopDetector{threshold: threshold}
}

func (d *DoomLoopDetector) Observe(tool string, input any) bool {
	serialized := fmt.Sprintf("%v", input)
	if d.lastTool == tool && d.lastInput == serialized {
		d.count++
	} else {
		d.lastTool = tool
		d.lastInput = serialized
		d.count = 1
	}
	return d.count >= d.threshold
}

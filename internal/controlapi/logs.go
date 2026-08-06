package controlapi

import (
	"sync"
	"time"
)

type LogEntry struct {
	Time    time.Time `json:"time"`
	Level   string    `json:"level"`
	Message string    `json:"message"`
}

type LogRing struct {
	mu       sync.RWMutex
	capacity int
	items    []LogEntry
}

func NewLogRing(capacity int) *LogRing {
	if capacity < 1 {
		capacity = 500
	}
	return &LogRing{capacity: capacity}
}

func (r *LogRing) Add(level, message string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items = append(r.items, LogEntry{Time: time.Now().UTC(), Level: level, Message: message})
	if extra := len(r.items) - r.capacity; extra > 0 {
		copy(r.items, r.items[extra:])
		r.items = r.items[:r.capacity]
	}
}

func (r *LogRing) Tail(limit int) []LogEntry {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if limit < 1 || limit > len(r.items) {
		limit = len(r.items)
	}
	start := len(r.items) - limit
	return append([]LogEntry(nil), r.items[start:]...)
}

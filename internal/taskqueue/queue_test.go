package taskqueue

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestQueueRunsOnlyRegisteredTaskTypesAndPersists(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tasks.json")
	queue, err := Open(path, 4)
	if err != nil {
		t.Fatal(err)
	}
	defer queue.Close()
	if _, err := queue.Enqueue("unknown", nil); err == nil {
		t.Fatal("expected unsupported task error")
	}
	if err := queue.Register("compatibility-check", func(_ context.Context, input map[string]any) (any, error) {
		return map[string]any{"preset": input["preset"], "valid": true}, nil
	}); err != nil {
		t.Fatal(err)
	}
	task, err := queue.Enqueue("compatibility-check", map[string]any{"preset": "linux-firefox"})
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		current, ok := queue.Get(task.ID)
		if ok && current.Status == Completed {
			if current.Result.(map[string]any)["valid"] != true {
				t.Fatalf("unexpected result: %#v", current.Result)
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("task did not complete")
}

func TestInterruptedTasksArePersistedAsFailed(t *testing.T) {
	directory := t.TempDir()
	path := filepath.Join(directory, "tasks.json")
	state := `[{"id":"0123456789abcdef01234567","type":"compatibility-check","status":"running","createdAt":"2026-01-01T00:00:00Z"}]`
	if err := os.WriteFile(path, []byte(state), 0o644); err != nil {
		t.Fatal(err)
	}
	queue, err := Open(path, 1)
	if err != nil {
		t.Fatal(err)
	}
	queue.Close()
	task, ok := queue.Get("0123456789abcdef01234567")
	if !ok || task.Status != Failed || task.FinishedAt == nil {
		t.Fatalf("interrupted task was not failed: %#v", task)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("state mode=%o", info.Mode().Perm())
	}
}

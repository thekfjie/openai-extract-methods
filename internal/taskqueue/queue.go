package taskqueue

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

type Status string

const (
	Queued    Status = "queued"
	Running   Status = "running"
	Completed Status = "completed"
	Failed    Status = "failed"
)

type Task struct {
	ID         string         `json:"id"`
	Type       string         `json:"type"`
	Status     Status         `json:"status"`
	Input      map[string]any `json:"input,omitempty"`
	Result     any            `json:"result,omitempty"`
	Error      string         `json:"error,omitempty"`
	CreatedAt  time.Time      `json:"createdAt"`
	StartedAt  *time.Time     `json:"startedAt,omitempty"`
	FinishedAt *time.Time     `json:"finishedAt,omitempty"`
}

type Handler func(context.Context, map[string]any) (any, error)

type Queue struct {
	mu       sync.RWMutex
	path     string
	tasks    map[string]*Task
	order    []string
	handlers map[string]Handler
	pending  chan string
	ctx      context.Context
	cancel   context.CancelFunc
	wg       sync.WaitGroup
}

func Open(path string, capacity int) (*Queue, error) {
	if capacity < 1 {
		capacity = 64
	}
	ctx, cancel := context.WithCancel(context.Background())
	queue := &Queue{
		path: path, tasks: map[string]*Task{}, handlers: map[string]Handler{},
		pending: make(chan string, capacity), ctx: ctx, cancel: cancel,
	}
	if err := queue.load(); err != nil {
		cancel()
		return nil, err
	}
	queue.wg.Add(1)
	go queue.worker()
	return queue, nil
}

func (q *Queue) Register(taskType string, handler Handler) error {
	if taskType == "" || handler == nil {
		return errors.New("task type and handler are required")
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	if _, exists := q.handlers[taskType]; exists {
		return fmt.Errorf("task handler already registered: %s", taskType)
	}
	q.handlers[taskType] = handler
	return nil
}

func (q *Queue) Enqueue(taskType string, input map[string]any) (Task, error) {
	q.mu.Lock()
	if _, ok := q.handlers[taskType]; !ok {
		q.mu.Unlock()
		return Task{}, fmt.Errorf("unsupported task type: %s", taskType)
	}
	id, err := randomID()
	if err != nil {
		q.mu.Unlock()
		return Task{}, err
	}
	task := &Task{ID: id, Type: taskType, Status: Queued, Input: cloneMap(input), CreatedAt: time.Now().UTC()}
	q.tasks[id] = task
	q.order = append(q.order, id)
	if err := q.saveLocked(); err != nil {
		delete(q.tasks, id)
		q.order = q.order[:len(q.order)-1]
		q.mu.Unlock()
		return Task{}, err
	}
	result := cloneTask(task)
	q.mu.Unlock()
	select {
	case q.pending <- id:
		return result, nil
	case <-q.ctx.Done():
		return Task{}, errors.New("task queue is closed")
	}
}

func (q *Queue) Get(id string) (Task, bool) {
	q.mu.RLock()
	defer q.mu.RUnlock()
	task, ok := q.tasks[id]
	return cloneTask(task), ok
}

func (q *Queue) List(limit int) []Task {
	q.mu.RLock()
	defer q.mu.RUnlock()
	if limit < 1 || limit > len(q.order) {
		limit = len(q.order)
	}
	result := make([]Task, 0, limit)
	for index := len(q.order) - 1; index >= 0 && len(result) < limit; index-- {
		result = append(result, cloneTask(q.tasks[q.order[index]]))
	}
	return result
}

func (q *Queue) Close() {
	q.cancel()
	q.wg.Wait()
}

func (q *Queue) worker() {
	defer q.wg.Done()
	for {
		select {
		case <-q.ctx.Done():
			return
		case id := <-q.pending:
			q.run(id)
		}
	}
}

func (q *Queue) run(id string) {
	q.mu.Lock()
	task := q.tasks[id]
	if task == nil || task.Status != Queued {
		q.mu.Unlock()
		return
	}
	handler := q.handlers[task.Type]
	now := time.Now().UTC()
	task.Status, task.StartedAt = Running, &now
	_ = q.saveLocked()
	input := cloneMap(task.Input)
	q.mu.Unlock()

	result, err := handler(q.ctx, input)
	q.mu.Lock()
	finished := time.Now().UTC()
	task.FinishedAt = &finished
	if err != nil {
		task.Status, task.Error = Failed, err.Error()
	} else {
		task.Status, task.Result = Completed, result
	}
	_ = q.saveLocked()
	q.mu.Unlock()
}

func (q *Queue) load() error {
	data, err := os.ReadFile(q.path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read task state: %w", err)
	}
	var tasks []Task
	if err := json.Unmarshal(data, &tasks); err != nil {
		return fmt.Errorf("decode task state: %w", err)
	}
	sort.Slice(tasks, func(i, j int) bool { return tasks[i].CreatedAt.Before(tasks[j].CreatedAt) })
	changed := false
	for index := range tasks {
		task := tasks[index]
		if task.Status == Running || task.Status == Queued {
			now := time.Now().UTC()
			task.Status, task.Error, task.FinishedAt = Failed, "task interrupted by service restart", &now
			changed = true
		}
		q.tasks[task.ID] = &task
		q.order = append(q.order, task.ID)
	}
	if changed {
		return q.saveLocked()
	}
	return nil
}

func (q *Queue) saveLocked() error {
	directory := filepath.Dir(q.path)
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return fmt.Errorf("create task state directory: %w", err)
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		return fmt.Errorf("protect task state directory: %w", err)
	}
	tasks := make([]Task, 0, len(q.order))
	for _, id := range q.order {
		tasks = append(tasks, cloneTask(q.tasks[id]))
	}
	data, err := json.MarshalIndent(tasks, "", "  ")
	if err != nil {
		return fmt.Errorf("encode task state: %w", err)
	}
	temporary := q.path + ".tmp"
	if err := os.WriteFile(temporary, append(data, '\n'), 0o600); err != nil {
		return fmt.Errorf("write task state: %w", err)
	}
	if err := os.Chmod(temporary, 0o600); err != nil {
		return fmt.Errorf("protect task state: %w", err)
	}
	if err := os.Rename(temporary, q.path); err != nil {
		return fmt.Errorf("replace task state: %w", err)
	}
	return nil
}

func randomID() (string, error) {
	value := make([]byte, 12)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("generate task id: %w", err)
	}
	return hex.EncodeToString(value), nil
}

func cloneTask(task *Task) Task {
	if task == nil {
		return Task{}
	}
	result := *task
	result.Input = cloneMap(task.Input)
	return result
}

func cloneMap(value map[string]any) map[string]any {
	if value == nil {
		return nil
	}
	encoded, _ := json.Marshal(value)
	var result map[string]any
	_ = json.Unmarshal(encoded, &result)
	return result
}

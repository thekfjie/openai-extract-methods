package controlapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"automyai/internal/fingerprintpolicy"
	"automyai/internal/taskqueue"
)

const maxRequestBytes = 128 * 1024

type Application struct {
	Service string
	SDK     SDK
	Config  *ConfigStore
	Queue   *taskqueue.Queue
	Logs    *LogRing
	Started time.Time
}

func NewApplication(service string, sdk SDK, config *ConfigStore, queue *taskqueue.Queue, logs *LogRing, artifactDirs ...string) (*Application, error) {
	app := &Application{Service: service, SDK: sdk, Config: config, Queue: queue, Logs: logs, Started: time.Now().UTC()}
	artifactDir := ""
	if len(artifactDirs) > 0 {
		artifactDir = artifactDirs[0]
	}
	if err := queue.Register(CompatibilityTaskType, func(ctx context.Context, input map[string]any) (any, error) {
		logs.Add("info", "fingerprint compatibility check started")
		result, err := RunCompatibilityCheck(ctx, sdk, config.Get(), input)
		if err != nil {
			logs.Add("error", "fingerprint compatibility check failed: "+err.Error())
			return nil, err
		}
		logs.Add("info", fmt.Sprintf("fingerprint compatibility check completed: %d presets", len(result.Presets)))
		return result, nil
	}); err != nil {
		return nil, err
	}
	if err := queue.Register(ProfileGenerateTaskType, func(ctx context.Context, input map[string]any) (any, error) {
		logs.Add("info", "fingerprint profile generation started")
		result, err := RunProfileGeneration(ctx, sdk, config.Get(), input, artifactDir)
		if err != nil {
			logs.Add("error", "fingerprint profile generation failed: "+err.Error())
			return nil, err
		}
		logs.Add("info", fmt.Sprintf("fingerprint profile generation completed: %d profiles", result.Count))
		return result, nil
	}); err != nil {
		return nil, err
	}
	logs.Add("info", "OpenAI 3 Go Profile control core initialized")
	return app, nil
}

func (a *Application) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	writer.Header().Set("Cache-Control", "no-store")
	switch {
	case request.Method == http.MethodGet && request.URL.Path == "/api/health":
		a.health(writer)
	case request.Method == http.MethodGet && request.URL.Path == "/api/status":
		a.status(writer)
	case request.Method == http.MethodGet && request.URL.Path == "/api/logs":
		a.logs(writer, request)
	case request.Method == http.MethodGet && request.URL.Path == "/api/config":
		a.json(writer, http.StatusOK, map[string]any{"ok": true, "config": a.Config.Get()})
	case request.Method == http.MethodPost && request.URL.Path == "/api/config":
		a.updateConfig(writer, request)
	case request.Method == http.MethodGet && request.URL.Path == "/api/presets":
		a.presets(writer, request)
	case request.Method == http.MethodGet && request.URL.Path == "/api/tasks":
		a.listTasks(writer, request)
	case request.Method == http.MethodPost && request.URL.Path == "/api/tasks":
		a.createTask(writer, request)
	case request.Method == http.MethodGet && strings.HasPrefix(request.URL.Path, "/api/tasks/"):
		a.getTask(writer, strings.TrimPrefix(request.URL.Path, "/api/tasks/"))
	case request.Method == http.MethodPost && request.URL.Path == "/api/start":
		a.disabled(writer, "automatic registration was removed; use /api/tasks for local compatibility checks")
	case request.Method == http.MethodPost && request.URL.Path == "/api/stop":
		a.disabled(writer, "no registration process is managed by this service")
	case request.Method == http.MethodGet && request.URL.Path == "/api/accounts":
		a.disabled(writer, "account collection is not part of the Go control core")
	case request.Method == http.MethodGet && request.URL.Path == "/api/traffic":
		a.json(writer, http.StatusOK, map[string]any{"ok": true, "current": nil, "items": []any{}})
	case request.Method == http.MethodGet && request.URL.Path == "/":
		writer.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = io.WriteString(writer, "<!doctype html><html><body><h2>AutoMyAI OpenAI 3</h2><p>Go Profile control core.</p></body></html>")
	default:
		a.error(writer, http.StatusNotFound, "not found")
	}
}

func (a *Application) health(writer http.ResponseWriter) {
	tasks := a.Queue.List(1000)
	counts := taskCounts(tasks)
	a.json(writer, http.StatusOK, map[string]any{
		"ok":                  true,
		"service":             a.Service,
		"implementation":      "go",
		"mode":                "openai3-profile-control",
		"sdkAvailable":        a.SDK.Available(),
		"registrationEnabled": false,
		"pid":                 os.Getpid(),
		"uptimeSeconds":       int64(time.Since(a.Started).Seconds()),
		"tasks":               counts,
		"taskTypes":           []string{ProfileGenerateTaskType, CompatibilityTaskType},
	})
}

func (a *Application) status(writer http.ResponseWriter) {
	tasks := a.Queue.List(1000)
	counts := taskCounts(tasks)
	state := map[string]any{
		"running":             counts[string(taskqueue.Running)] > 0,
		"phase":               "ready",
		"pid":                 os.Getpid(),
		"queued":              counts[string(taskqueue.Queued)],
		"completed":           counts[string(taskqueue.Completed)],
		"failed":              counts[string(taskqueue.Failed)],
		"total":               len(tasks),
		"registrationEnabled": false,
	}
	a.json(writer, http.StatusOK, map[string]any{"ok": true, "state": state, "config": a.Config.Get()})
}

func taskCounts(tasks []taskqueue.Task) map[string]int {
	counts := map[string]int{"queued": 0, "running": 0, "completed": 0, "failed": 0}
	for _, task := range tasks {
		counts[string(task.Status)]++
	}
	return counts
}

func (a *Application) logs(writer http.ResponseWriter, request *http.Request) {
	limit := queryLimit(request, "tail", 200, 1000)
	a.json(writer, http.StatusOK, map[string]any{"ok": true, "logs": a.Logs.Tail(limit)})
}

func (a *Application) updateConfig(writer http.ResponseWriter, request *http.Request) {
	var config Config
	if err := decodeJSON(writer, request, &config); err != nil {
		a.error(writer, http.StatusBadRequest, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), 10*time.Second)
	defer cancel()
	presets, err := a.SDK.Presets(ctx)
	if err != nil {
		a.error(writer, http.StatusServiceUnavailable, "fingerprint SDK presets are unavailable")
		return
	}
	updated, err := a.Config.Set(config, presets)
	if err != nil {
		a.error(writer, http.StatusBadRequest, err.Error())
		return
	}
	a.Logs.Add("info", "control configuration updated")
	a.json(writer, http.StatusOK, map[string]any{"ok": true, "config": updated})
}

func (a *Application) presets(writer http.ResponseWriter, request *http.Request) {
	ctx, cancel := context.WithTimeout(request.Context(), 10*time.Second)
	defer cancel()
	presets, err := a.SDK.Presets(ctx)
	if err != nil {
		a.error(writer, http.StatusServiceUnavailable, "fingerprint SDK presets are unavailable")
		return
	}
	a.json(writer, http.StatusOK, map[string]any{
		"ok": true, "presets": append([]string{fingerprintpolicy.OpenAI3Preset}, presets...),
		"policy": map[string]any{
			"browser": "Chrome", "browserVersion": fingerprintpolicy.ChromeBrowserVersion,
			"operatingSystems": []string{"Windows", "macOS"}, "linuxAllowed": false,
		},
	})
}

type createTaskRequest struct {
	Type  string         `json:"type"`
	Input map[string]any `json:"input"`
}

func (a *Application) createTask(writer http.ResponseWriter, request *http.Request) {
	var payload createTaskRequest
	if err := decodeJSON(writer, request, &payload); err != nil {
		a.error(writer, http.StatusBadRequest, err.Error())
		return
	}
	if payload.Type == "" {
		payload.Type = CompatibilityTaskType
	}
	task, err := a.Queue.Enqueue(payload.Type, payload.Input)
	if err != nil {
		a.error(writer, http.StatusBadRequest, err.Error())
		return
	}
	a.Logs.Add("info", "task queued: "+task.ID)
	a.json(writer, http.StatusAccepted, map[string]any{"ok": true, "task": task})
}

func (a *Application) listTasks(writer http.ResponseWriter, request *http.Request) {
	limit := queryLimit(request, "limit", 100, 500)
	a.json(writer, http.StatusOK, map[string]any{"ok": true, "tasks": a.Queue.List(limit)})
}

func (a *Application) getTask(writer http.ResponseWriter, id string) {
	if id == "" || strings.Contains(id, "/") {
		a.error(writer, http.StatusNotFound, "task not found")
		return
	}
	task, ok := a.Queue.Get(id)
	if !ok {
		a.error(writer, http.StatusNotFound, "task not found")
		return
	}
	a.json(writer, http.StatusOK, map[string]any{"ok": true, "task": task})
}

func (a *Application) disabled(writer http.ResponseWriter, message string) {
	a.Logs.Add("warn", message)
	a.error(writer, http.StatusGone, message)
}

func (a *Application) json(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json; charset=utf-8")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(value)
}

func (a *Application) error(writer http.ResponseWriter, status int, message string) {
	a.json(writer, status, map[string]any{"ok": false, "error": message, "detail": message})
}

func decodeJSON(writer http.ResponseWriter, request *http.Request, destination any) error {
	body := http.MaxBytesReader(writer, request.Body, maxRequestBytes)
	defer body.Close()
	decoder := json.NewDecoder(body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return errors.New("request body must be a valid JSON object with supported fields")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("request body must contain exactly one JSON object")
	}
	return nil
}

func queryLimit(request *http.Request, name string, fallback, maximum int) int {
	value, err := strconv.Atoi(request.URL.Query().Get(name))
	if err != nil || value < 1 {
		return fallback
	}
	if value > maximum {
		return maximum
	}
	return value
}

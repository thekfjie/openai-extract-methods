package main

import (
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"automyai/internal/extractapi"
	"automyai/internal/extractmethods"
)

func main() {
	host := flag.String("host", envString("EXTRACT_API_HOST", "127.0.0.1"), "listen host")
	port := flag.Int("port", envInt("EXTRACT_API_PORT", 18794), "listen port")
	configPath := flag.String("config", envString("AUTOMYAI_CONFIG_PATH", "/app/config.json"), "AutoMyAI runtime config")
	dataPath := flag.String("data", envString("EXTRACT_API_DATA_PATH", "/app/data/extract-api/jobs.json"), "job history path")
	globalConcurrency := flag.Int("global-concurrency", envInt("EXTRACT_API_GLOBAL_CONCURRENCY", 32), "global item concurrency")
	flag.Parse()

	engine := extractmethods.NewEngine(*configPath)
	manager, err := extractmethods.NewJobManager(engine, *dataPath, *globalConcurrency)
	if err != nil {
		log.Fatalf("initialize extract manager: %v", err)
	}
	address := fmt.Sprintf("%s:%d", *host, *port)
	server := &http.Server{
		Addr: address, Handler: extractapi.NewServer(manager).Handler(),
		ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second,
		WriteTimeout: 30 * time.Second, IdleTimeout: 90 * time.Second,
	}
	log.Printf("AutoMyAI Go extraction API listening on %s", address)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("extract API: %v", err)
	}
}

func envString(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func envInt(key string, fallback int) int {
	value, err := strconv.Atoi(strings.TrimSpace(os.Getenv(key)))
	if err != nil || value < 1 || value > 65535 {
		return fallback
	}
	return value
}

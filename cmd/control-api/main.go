package main

import (
	"context"
	"errors"
	"flag"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"automyai/internal/controlapi"
	"automyai/internal/fingerprintconfig"
	"automyai/internal/fingerprintsdk"
	"automyai/internal/taskqueue"
)

func main() {
	service := flag.String("service", envOr("AUTOMYAI_CONTROL_SERVICE", "openai3"), "service name")
	host := flag.String("host", envOr("OPENAI3_HOST", "127.0.0.1"), "loopback listen host")
	port := flag.Int("port", envInt("OPENAI3_PORT", 0), "listen port from config/ports.env")
	dataDir := flag.String("data-dir", envOr("OPENAI3_DATA_DIR", "/opt/automyai/data/openai3-control"), "private runtime data directory")
	runtimeConfigPath := flag.String("runtime-config", envOr("AUTOMYAI_CONFIG", "/opt/automyai/config.json"), "shared runtime configuration file")
	sdkFlag := flag.String("sdk-dir", envOr("OAI_FINGERPRINT_SDK_DIR", ""), "fingerprint SDK directory")
	nodeFlag := flag.String("node", envOr("OAI_FINGERPRINT_NODE", "node"), "Node.js executable")
	flag.Parse()

	if !loopbackHost(*host) {
		log.Fatal("host must be loopback")
	}
	if *port < 1 || *port > 65535 {
		log.Fatal("port must be between 1 and 65535")
	}
	sdkDir, err := fingerprintsdk.FindSDKDir(*sdkFlag)
	if err != nil {
		log.Fatal(err)
	}
	node, err := exec.LookPath(*nodeFlag)
	if err != nil {
		log.Fatal("Node.js is not available")
	}
	runtimeConfig, err := fingerprintconfig.Load(*runtimeConfigPath)
	if err != nil {
		log.Fatal(err)
	}
	sdk := fingerprintsdk.Runner{
		Node:             node,
		SDKDir:           sdkDir,
		CloudEnabled:     runtimeConfig.CloudEnabled,
		CloudBaseURL:     runtimeConfig.CloudBaseURL,
		CloudHeadersFile: runtimeConfig.CloudHeadersFile,
		CloudOmitMAC:     runtimeConfig.CloudOmitMAC,
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	presets, err := sdk.Presets(ctx)
	cancel()
	if err != nil {
		log.Fatalf("list fingerprint presets: %v", err)
	}
	config, err := controlapi.OpenConfig(filepath.Join(*dataDir, "config.json"), presets)
	if err != nil {
		log.Fatal(err)
	}
	queue, err := taskqueue.Open(filepath.Join(*dataDir, "tasks.json"), 64)
	if err != nil {
		log.Fatal(err)
	}
	defer queue.Close()
	logs := controlapi.NewLogRing(500)
	app, err := controlapi.NewApplication(*service, sdk, config, queue, logs, filepath.Join(*dataDir, "profiles"))
	if err != nil {
		log.Fatal(err)
	}

	logger := log.New(os.Stdout, "[control-api] ", log.LstdFlags)
	server := &http.Server{
		Addr:              net.JoinHostPort(*host, strconv.Itoa(*port)),
		Handler:           app,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      45 * time.Second,
		IdleTimeout:       30 * time.Second,
	}
	go func() {
		logger.Printf("Go control core listening on %s", server.Addr)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Fatalf("listen: %v", err)
		}
	}()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	shutdown, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()
	if err := server.Shutdown(shutdown); err != nil {
		logger.Printf("shutdown: %v", err)
	}
}

func loopbackHost(host string) bool {
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func envOr(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func envInt(name string, fallback int) int {
	value, err := strconv.Atoi(strings.TrimSpace(os.Getenv(name)))
	if err == nil && value > 0 {
		return value
	}
	return fallback
}

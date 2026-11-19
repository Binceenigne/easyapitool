package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

func main() {
	configFile := flag.String("env", ".env", "path to the gateway .env file")
	flag.Parse()
	if err := run(*configFile); err != nil {
		log.Fatal(err)
	}
}

func run(configFile string) error {
	cfg, err := loadConfig(configFile)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(cfg.dataDir, 0700); err != nil {
		return err
	}
	if err := os.MkdirAll(cfg.workDir, 0700); err != nil {
		return err
	}
	if err := os.MkdirAll(cfg.assetDir, 0700); err != nil {
		return err
	}
	instance, err := os.MkdirTemp(cfg.assetDir, "mirra-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(instance)
	store, err := openStore(filepath.Join(cfg.dataDir, "gateway.db"), cfg.storageSecret)
	if err != nil {
		return err
	}
	defer store.db.Close()
	egress, err := loadEgress(context.Background(), store, cfg)
	if err != nil {
		return err
	}
	client := newUpstreamClient(cfg, egress)
	if cfg.openAIKey != "" {
		if err := store.saveUpstream(context.Background(), upstream{
			ID: "openai-env", Name: "OpenAI (.env)", URL: openAIBaseURL, Pool: cfg.openAIPool, Secret: cfg.openAIKey, Enabled: true,
		}); err != nil {
			return err
		}
	} else if _, err := store.db.Exec("UPDATE upstreams SET enabled=0 WHERE id='openai-env'"); err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	gateway := &gateway{cfg: cfg, store: store, client: client, egress: egress}
	if err := gateway.loadModels(context.Background()); err != nil {
		return err
	}
	mobile := newMobile(ctx, gateway, instance)
	gateway.onQuotaChange = mobile.quotaChanged
	gateway.onModelsChange = mobile.modelsChanged
	public := http.NewServeMux()
	gateway.registerProxy(public)
	mobile.registerRoutes(public)
	admin := newAdmin(gateway).routes(mobile)
	servers := []*http.Server{
		{Addr: cfg.listen, Handler: public, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 90 * time.Second},
		{Addr: cfg.adminListen, Handler: admin, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second},
	}
	errs := make(chan error, len(servers))
	for _, server := range servers {
		go func() {
			log.Printf("Mirra listening on %s", server.Addr)
			errs <- server.ListenAndServe()
		}()
	}
	select {
	case <-ctx.Done():
	case err = <-errs:
		stop()
	}
	shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	for _, server := range servers {
		if shutdownErr := server.Shutdown(shutdown); shutdownErr != nil {
			server.Close()
		}
	}
	mobile.workers.Wait()
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

type object = map[string]any

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	if err := encodeJSON(w, payload); err != nil {
		log.Printf("write JSON: %s", fmt.Sprint(err))
	}
}

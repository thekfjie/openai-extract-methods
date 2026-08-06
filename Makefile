.PHONY: build-go build-service check doctor up deploy deploy-all restart logs install-runtime

build-go:
	./scripts/build-go.sh $(SERVICE)

build-service:
	@test -n "$(SERVICE)" || (echo "Usage: make build-service SERVICE=extract-api" >&2; exit 2)
	./scripts/automyai-compose.sh build $(SERVICE)

check:
	./scripts/check.sh

doctor:
	./scripts/automyai-compose.sh doctor

up:
	./scripts/automyai-compose.sh up

deploy:
	@test -n "$(SERVICE)" || (echo "Usage: make deploy SERVICE=extract-api" >&2; exit 2)
	./scripts/automyai-compose.sh deploy $(SERVICE)

deploy-all:
	./scripts/automyai-compose.sh deploy-all

restart:
	./scripts/automyai-compose.sh restart automyai

logs:
	./scripts/automyai-compose.sh logs -f automyai

install-runtime:
	./scripts/install-runtime-config.sh --restart

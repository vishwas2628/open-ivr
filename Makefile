.PHONY: help install builder deploy publish reload status logs service-start service-stop \
        service-restart service-enable service-disable verify config creds sounds \
        grant-permissions clean clean-data distclean test lint format venv check

PYTHON  := .venv/bin/python
PIP     := .venv/bin/pip
SHELL   := /bin/bash
export PYTHONPATH := $(CURDIR)

help:          ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------- first run

venv:          ## Create .venv and install the Python dependencies
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "venv ready - next: make install"

install:       ## Full first-time system install (asks for sudo)
	@bash system/install.sh

grant-permissions: ## Re-grant asterisk group permissions (one-time, needs sudo)
	@sudo bash system/install.sh --grant-permissions

# ------------------------------------------------------------------- builder

builder:       ## Start the builder UI (http://localhost:8090)
	@$(PYTHON) -m openivr builder

builder-lan:   ## Start the builder bound to every interface (LAN access)
	@$(PYTHON) -m openivr builder --host 0.0.0.0

config:        ## Show where config.yaml lives and what is inside
	@$(PYTHON) - openivr config show

creds:         ## Reset the builder login (new random password, printed once)
	@$(PYTHON) -m openivr creds

# -------------------------------------------------------------------- deploy

deploy:        ## Render the builder JSON, copy it to Asterisk, reload
	@bash system/deploy.sh

publish:       ## Same as deploy but never reloads Asterisk
	@$(PYTHON) -m openivr publish --no-reload

staging:       ## Render into data/asterisk-build only (inspect before deploying)
	@$(PYTHON) -m openivr publish --staging

reload:        ## Reload Asterisk without restarting it
	@bash scripts/asterisk_reload.sh

sounds:        ## Copy data/sounds prompts into Asterisk's sounds folder
	@bash system/sync-sounds.sh copy

convert:       ## Convert one audio file: make convert FILE=x.mp3 [STEM=name]
	@test -n "$(FILE)" || { echo "usage: make convert FILE=input.mp3 [STEM=name]"; exit 2; }
	@bash scripts/convert_sound.sh "$(FILE)" "$(or $(STEM),$(basename $(FILE) .mp3))" "$(CURDIR)/data/sounds"

# -------------------------------------------------------------------- service

service-start: ## Install, enable and start the openivr systemd service (sudo)
	@sudo bash system/steps/95-systemd.sh

service-stop:  ## Stop the openivr service
	@sudo systemctl stop openivr

service-restart: ## Restart the openivr service
	@sudo systemctl restart openivr

service-enable: ## Enable the service at boot
	@sudo systemctl enable --now openivr

service-disable: ## Disable the service at boot
	@sudo systemctl disable --now openivr

status:        ## Show service, Asterisk and builder status
	@bash system/status.sh

logs:          ## Tail the openivr service log
	@journalctl -u openivr -f

verify:        ## Verify config, flow, prompts and ARI connectivity
	@$(PYTHON) -m openivr verify

test:          ## Run the test suite
	@$(PYTHON) -m pytest -q

lint:          ## Ruff (if installed)
	@$(PYTHON) -m ruff check openivr tests || echo "(ruff not installed - skipping)"

format:        ## Ruff format (if installed)
	@$(PYTHON) -m ruff format openivr tests || echo "(ruff not installed - skipping)"

check: lint test ## Lint and test

# ---------------------------------------------------------------------- clean

clean:         ## Remove build artefacts and caches
	@rm -rf data/asterisk-build .pytest_cache .ruff_cache
	@find . -name '__pycache__' -type d -prune -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.pyc' -delete

clean-data:    ## Remove ALL generated data (prompts, recordings, flow JSON, state)
	@read -p "Delete data/ (prompts, recordings, builder state)? [y/N] " a; \
	 [ "$$a" = "y" ] && rm -rf data system/.state || echo "kept data/"

distclean: clean ## Remove .venv as well
	@rm -rf .venv

# ------------------------------------------------------------------- helpers

$(PYTHON):
	@echo "no .venv yet - run: make venv" >&2; exit 1

openivr:       ## Run the CLI, e.g. make openivr ARGS="verify"
	@$(PYTHON) -m openivr $(ARGS)
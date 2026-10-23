.DEFAULT_GOAL := help
.PHONY: help setup lock test serve dev build install uninstall vendor

MOVI_VERSION := 0.4.1
MERMAID_VERSION := 11.4.1
VENDOR := tidyarr/web/vendor
VENDOR_FILES := $(VENDOR)/movi-element.js $(VENDOR)/movi.wasm $(VENDOR)/mermaid.js
PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin

help:  ## Show this usage
	@echo "Usage: make <target>"
	@echo
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup:  ## Create .venv and install the test and web UI dependencies
	./scripts/setup.sh

lock:  ## Update requirements.txt to the newest versions allowed by requirements.in (then run `make setup` and `make test`)
	uv pip compile requirements.in --universal --python-version 3.12 --no-header --upgrade -o requirements.txt

test:  ## Run the test suite (pass pytest args via ARGS, e.g. ARGS="-k triage")
	.venv/bin/python -m pytest $(ARGS)

serve: $(VENDOR_FILES)  ## Start the review web UI in this terminal and open it
	.venv/bin/python scripts/serve.py --foreground $(ARGS)

dev: $(VENDOR_FILES)  ## Like serve, but restarts on code changes and the page reloads itself (open the printed address once)
	TIDYARR_DEV_TOKEN=$$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(24))") \
		.venv/bin/watchfiles --filter python ".venv/bin/python scripts/serve.py --foreground --dev --no-open $(ARGS)" tidyarr scripts

build: $(VENDOR_FILES)  ## Bundle the app, web UI and Python into one executable at dist/tidyarr (for this OS only)
	.venv/bin/pyinstaller --onefile --clean --noconfirm --name tidyarr --paths $(CURDIR) \
		--add-data $(CURDIR)/tidyarr/web:tidyarr/web --collect-data guessit --collect-all babelfish \
		--specpath build scripts/serve.py

install:  ## Copy dist/tidyarr to ~/.local/bin (run `make build` first; PREFIX=/usr/local for a system-wide install)
	@test -f dist/tidyarr || { echo "dist/tidyarr is missing: run \`make build\` first"; exit 1; }
	install -Dm755 dist/tidyarr $(DESTDIR)$(BINDIR)/tidyarr
	@case ":$$PATH:" in *":$(BINDIR):"*) ;; *) echo "Note: $(BINDIR) is not on your PATH";; esac

uninstall:  ## Remove the installed tidyarr (same PREFIX as install)
	rm -f $(DESTDIR)$(BINDIR)/tidyarr

vendor: $(VENDOR_FILES)  ## Fetch the in-browser player and Mermaid only (CI uses this before building on each OS)

# The in-browser player (movi-player) and Mermaid, at pinned versions, from jsDelivr.
# They depend on this Makefile so bumping a version re-fetches them on the next serve/build.
$(VENDOR_FILES): Makefile

$(VENDOR)/movi-element.js:
	mkdir -p $(VENDOR)
	curl -fL https://cdn.jsdelivr.net/npm/movi-player@$(MOVI_VERSION)/dist/element.slim.js -o $@
$(VENDOR)/movi.wasm:
	mkdir -p $(VENDOR)
	curl -fL https://cdn.jsdelivr.net/npm/movi-player@$(MOVI_VERSION)/dist/movi.wasm -o $@
$(VENDOR)/mermaid.js:
	mkdir -p $(VENDOR)
	curl -fL https://cdn.jsdelivr.net/npm/mermaid@$(MERMAID_VERSION)/dist/mermaid.min.js -o $@

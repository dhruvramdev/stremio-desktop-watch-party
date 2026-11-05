.PHONY: all build shim test dist clean doctor

PYTHON ?= python3
PYINSTALLER ?= pyinstaller

# Fall back to uv if available
ifeq ($(shell which uv 2>/dev/null),)
SETUP_ENV =
RUN_PY = $(PYTHON)
RUN_PYINSTALLER = $(PYINSTALLER)
else
RUN_PY = uv run python
RUN_PYINSTALLER = uv run --with pyinstaller pyinstaller
endif

all: build

shim:
	$(MAKE) -C shim

build dist: shim
	@echo "==> Building standalone macOS executable (dist/watch-party)..."
	@if [ -z "$(shell which uv 2>/dev/null)" ] && [ -z "$(shell which pyinstaller 2>/dev/null)" ]; then \
		echo "Installing uv locally to build single-file binary..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
		export PATH="$$HOME/.local/bin:$$PATH"; \
		uv run --with pyinstaller pyinstaller \
			--noconfirm \
			--clean \
			--onefile \
			--name watch-party \
			--add-data "manifest.json:." \
			--add-binary "shim/libstremio-mpv-ipc-shim.dylib:shim" \
			bin/watch-party; \
	else \
		$(RUN_PYINSTALLER) \
			--noconfirm \
			--clean \
			--onefile \
			--name watch-party \
			--add-data "manifest.json:." \
			--add-binary "shim/libstremio-mpv-ipc-shim.dylib:shim" \
			bin/watch-party; \
	fi
	@rm -rf build watch-party.spec
	@echo "==> Standalone binary generated at: dist/watch-party"

test: shim
	@echo "==> Running test suite..."
	$(RUN_PY) -m unittest discover -s tests -v

doctor:
	@if [ -f dist/watch-party ]; then \
		./dist/watch-party doctor; \
	else \
		uv run watch-party doctor; \
	fi

clean:
	$(MAKE) -C shim clean
	rm -rf dist build watch-party.spec .venv


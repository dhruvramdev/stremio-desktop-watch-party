.PHONY: all build shim test dist clean doctor

PYTHON ?= uv run python
PYINSTALLER ?= uv run --with pyinstaller pyinstaller

all: build

shim:
	$(MAKE) -C shim

build dist: shim
	@echo "==> Building standalone macOS executable (dist/watch-party)..."
	@$(PYINSTALLER) \
		--noconfirm \
		--clean \
		--onefile \
		--name watch-party \
		--add-data "manifest.json:." \
		--add-binary "shim/libstremio-mpv-ipc-shim.dylib:shim" \
		bin/watch-party
	@rm -rf build watch-party.spec
	@echo "==> Standalone binary generated at: dist/watch-party"

test: shim
	@echo "==> Running test suite..."
	$(PYTHON) -m unittest discover -s tests -v

doctor:
	@if [ -f dist/watch-party ]; then \
		./dist/watch-party doctor; \
	else \
		uv run watch-party doctor; \
	fi

clean:
	$(MAKE) -C shim clean
	rm -rf dist build watch-party.spec .venv


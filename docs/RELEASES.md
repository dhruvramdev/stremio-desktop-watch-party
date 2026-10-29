# Releasing Standalone Binaries via GitHub Releases

You can automate building and releasing the standalone macOS binary using a simple GitHub Actions workflow. When you push a Git tag (e.g., `v1.0.0`), GitHub Actions will compile the binary on an Apple Silicon runner and attach `watch-party` to the GitHub release.

---

## GitHub Actions Workflow (`.github/workflows/release.yml`)

Create `.github/workflows/release.yml`:

```yaml
name: Release Standalone Binary

on:
  push:
    tags:
      - 'v*'

jobs:
  build-macos:
    runs-on: macos-latest
    permissions:
      contents: write

    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v2
        with:
          version: "latest"

      - name: Build standalone binary
        run: |
          make dist

      - name: Package binary into tarball
        run: |
          cd dist
          tar -czvf watch-party-macos-arm64.tar.gz watch-party
          shasum -a 256 watch-party-macos-arm64.tar.gz > watch-party-macos-arm64.tar.gz.sha256

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: |
            dist/watch-party-macos-arm64.tar.gz
            dist/watch-party-macos-arm64.tar.gz.sha256
          draft: false
          prerelease: false
```

---

## How Users Download & Run

Once released, non-technical users can:
1. Download `watch-party-macos-arm64.tar.gz` from your repository's **Releases** tab.
2. Unpack and run:
   ```bash
   tar -xvf watch-party-macos-arm64.tar.gz
   chmod +x watch-party
   ./watch-party start --role guest --room movie-night --secret s3cr3t --relay wss://your-tunnel.trycloudflare.com
   ```
No Python, Git, or developer tooling required.


<!-- macos-start -->
## Install on macOS

Option 1 — After the Homebrew PR is merged:

```bash
brew install unified-courier-tracker
```

Option 2 — Install directly from the branch (builds from source):

```bash
brew install --build-from-source https://raw.githubusercontent.com/anir0y/homebrew-core/unified-courier-tracker/Formula/unified-courier-tracker.rb
```

Option 3 — Tap the fork and install:

```bash
brew tap anir0y/homebrew-core https://github.com/anir0y/homebrew-core
brew install unified-courier-tracker
```

Option 4 — Manual install (no Homebrew formula):

```bash
brew install python
curl -L -o /usr/local/bin/unified-courier-tracker https://raw.githubusercontent.com/anir0y/unified-courier-tracker/main/track_shipments.py
chmod +x /usr/local/bin/unified-courier-tracker
unified-courier-tracker --help
```

Notes: The script uses only Python's standard library (urllib.request, urllib.error, json, argparse, os, curses, time, html.parser). Ensure Python 3.12+ is installed.

Last updated: 2026-01-29T13:03:48.701Z
<!-- macos-end -->

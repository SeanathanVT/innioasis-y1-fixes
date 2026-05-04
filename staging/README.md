# staging/

Default location for `rom.zip` (the official Innioasis Y1 OTA, MD5-validated against `KNOWN_FIRMWARES` in `apply.bash`). Drop the file here:

```bash
cp /path/to/rom.zip staging/
./apply.bash --all
```

Override with `./apply.bash --artifacts-dir <path>` to point at a different directory (e.g. on a separate drive, shared between checkouts, or kept outside the repo entirely).

The contents of this directory (other than this README) are `.gitignore`d so firmware never lands in commits. **`git clean -dfx` will nuke whatever you stage here** along with build artifacts — keep a backup of `rom.zip` if you'd rather not re-download.

See the top-level [README](../README.md) Quick start for the full flow.

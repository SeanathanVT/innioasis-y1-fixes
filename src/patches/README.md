# patches

Byte-level and smali patchers for Innioasis Y1 firmware binaries. Invoked by the top-level [`apply.bash`](../../apply.bash); each patcher can also be run standalone for inspection.

## Files

| Patcher | Target | Patches |
|---|---|---|
| **`patch_mtkbt.py`** | `mtkbt` (Bluetooth daemon) | V1 (AVRCP 1.0→1.3), V2 (AVCTP 1.0→1.2), S1 (replace 0x0311 SupportedFeatures slot with 0x0100 ServiceName pointing at existing "Advanced Audio" string), P1 (force fn 0x144bc op_code dispatch to PASSTHROUGH branch → bl 0x10404 → msg 519 emit) — **4 patches**. Pixel-1.3 SDP shape + bypass mtkbt's silent-drop of VENDOR_DEPENDENT 1.3+ COMMANDs. Wired by `apply.bash --avrcp`. |
| **`patch_libextavrcp_jni.py`** | `libextavrcp_jni.so` | R1 (redirect `bne.n 0x65bc` → `bl.w 0x7308`), T1 (GetCapabilities response trampoline at 0x7308 — overwrites `testparmnum`), T2 stub (4-byte `b.w extended_T2` at 0x72d4 + classInitNative `return 0` stub at 0x72d0 — overwrites `classInitNative`), extended_T2 + T4 + T5 (in LOAD #1 page-padding region at 0xac54 — read `y1-track-info` / `y1-trampoline-state`, emit INTERIM/CHANGED + multi-attribute GetElementAttributes responses, plus proactive CHANGED on Y1 track changes via the patched `notificationTrackChangedNative`), and **LOAD #1 program-header extension** (FileSiz/MemSiz 0xac54 → 0xaf4c to map the trampoline blob as R+E). The trampoline blob is built dynamically by `_iter15_trampolines.py` using a tiny Thumb-2 assembler (`_thumb2asm.py`). T1 hardware-verified iter5, T2 iter6, T4 stub iter9, T4 Title-only iter11 ("Y1 Test" on Sonos), T4 multi-attribute single-frame iter13 (Title + Artist + Album simultaneously), iter14b real metadata via Y1MediaBridge file-write, iter15 state-tracked CHANGED notifications, iter16 INTERIM/CHANGED track_id pinned to 0xFF×8 sentinel (protocol working), iter17a adds T5 reached via patched `notificationTrackChangedNative` so Y1MediaBridge's track-change broadcast emits CHANGED on the AVRCP wire proactively (pairs with the iter17a entry in `patch_mtkbt_odex.py` that NOPs the Java cardinality gate), iter17b restores T4's iter13 multi-attribute calling convention (`arg2=index, arg3=total`) so all three attributes pack into one msg=540 frame instead of three separate ones — the iter17a hardware test caught the regression as visible field-by-field flicker on Sonos. See [`ARCHITECTURE.md`](../../docs/ARCHITECTURE.md). Pairs with P1 in `patch_mtkbt.py`. Wired by `apply.bash --avrcp`. |
| **`patch_mtkbt_odex.py`** | `MtkBt.odex` | F1 (`getPreferVersion()` returns 14), F2 (`disable()` resets `sPlayServiceInterface`), iter17a (NOP `if-eqz` cardinality gate at 0x3c530 in `BTAvrcpMusicAdapter.handleKeyMessage` so `notificationTrackChangedNative` fires on every Y1MediaBridge track-change broadcast). Recomputes DEX adler32. Wired by `apply.bash --avrcp`. |
| **`patch_y1_apk.py`** | `com.innioasis.y1*.apk` | Smali patches A/B/C for Artist→Album navigation. Uses androguard + apktool. |
| **`patch_adbd.py`** | `/sbin/adbd` (boot.img) | *Unwired since v1.7.0; historical record only.* H1/H2/H3 — caused "device offline" on hardware. |
| **`patch_bootimg.py`** | `boot.img` | *Unwired since v1.7.0; historical record only.* Format-aware boot.img cpio patcher; called `patch_adbd.patch_bytes()`. |

Per-patch byte-level reference (offsets, before/after bytes, rationale): [`../../docs/PATCHES.md`](../../docs/PATCHES.md).

## Common interface

Each byte patcher (mtkbt / mtkbt_odex / libextavrcp_jni / adbd) takes:

```
python3 patch_<name>.py <stock-binary> [--output PATH] [--verify-only] [--skip-md5]
```

- Validates the stock input MD5 against a hardcoded expected value
- Verifies every patch site matches its `before` bytes; refuses to write on mismatch
- Detects "already patched" inputs (every site matches `after`) and exits 0 without writing
- Default output: `output/<name>.patched`

`patch_y1_apk.py` is script-style (no `--output`; output lands in `output/` relative to CWD) — see its docstring for invocation details.

## Manual invocation

Run from this directory so `output/` and `_patch_workdir/` (apktool scratch) land here:

```bash
( cd .. && cd src/patches && python3 patch_mtkbt.py /path/to/stock/mtkbt )
# → src/patches/output/mtkbt.patched
```

The top-level bash always invokes patchers from `src/patches/`; for manual runs it's a convention worth following so the bash can pick up the output if you switch to `--avrcp` afterwards.

## Idempotency

The bash's `patch_in_place_bytes` helper detects "already patched" exit-0-without-output and skips the write-back. Re-running `--avrcp` against an already-patched mount is a no-op.

## Requirements

- Python 3.8+, stdlib only, for all byte patchers.
- `patch_y1_apk.py` additionally requires Java 11+ (apktool), `androguard` (`pip install androguard`). apktool itself is downloaded into `_patch_workdir/` on first invocation.

## Status

Active patchers (wired into the bash):
- `patch_mtkbt.py`, `patch_mtkbt_odex.py`, `patch_libextavrcp_jni.py`, `patch_y1_apk.py`

Historical / unwired (kept for reference, *do not ship their output*):
- `patch_adbd.py`, `patch_bootimg.py` — both broke ADB protocol on hardware in every revision tried. Superseded by [`../su/`](../su/) (setuid `/system/xbin/su`) for the root-access goal.

## See also

- [`../../README.md`](../../README.md) — project overview
- [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) — **AVRCP metadata proxy architecture**: data-path diagram, trampoline chain (T1/T2/T4/T5), response builder calling conventions, ELF program-header surgery, code-cave inventory. Read this first if extending the trampoline chain or adding new PDU handlers.
- [`../../docs/PATCHES.md`](../../docs/PATCHES.md) — per-patch byte-level reference (offsets, before/after bytes, rationale)
- [`../../docs/PROXY-BUILD.md`](../../docs/PROXY-BUILD.md) — iteration plan, status checkboxes, pending work
- [`../../docs/DEX.md`](../../docs/DEX.md) — DEX-level analysis backing `patch_y1_apk.py`'s smali patches
- [`../../INVESTIGATION.md`](../../INVESTIGATION.md) — chronological investigation history (gdbserver capture iterations, dead-end paths, hypothesis evolution)
- [`../../CHANGELOG.md`](../../CHANGELOG.md) — top-level changelog

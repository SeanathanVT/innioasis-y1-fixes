# patches

Byte-level and smali patchers for Innioasis Y1 firmware binaries. Invoked by the top-level [`apply.bash`](../../apply.bash); each patcher can also be run standalone for inspection.

## Files

| Patcher | Target | Patches |
|---|---|---|
| **`patch_mtkbt.py`** | `mtkbt` (Bluetooth daemon) | V1 (AVRCP 1.0→1.3), V2 (AVCTP 1.0→1.2), S1 (replace 0x0311 SupportedFeatures slot with 0x0100 ServiceName pointing at existing "Advanced Audio" string), P1 (force fn 0x144bc op_code dispatch to PASSTHROUGH branch → bl 0x10404 → msg 519 emit) — **4 patches**. Pixel-1.3 SDP shape + bypass mtkbt's silent-drop of VENDOR_DEPENDENT 1.3+ COMMANDs. Wired by `apply.bash --avrcp`. |
| **`patch_libextavrcp_jni.py`** | `libextavrcp_jni.so` | R1 (redirect `bne.n 0x65bc` → `bl.w 0x7308`), T1 (GetCapabilities response trampoline at 0x7308 — overwrites `testparmnum`), T2 stub (4-byte `b.w extended_T2` at 0x72d4 + classInitNative `return 0` stub at 0x72d0 — overwrites `classInitNative`), extended_T2 + T4 + T5 + T_charset + T_battery + T6 + T8 + T9 (in LOAD #1 page-padding region at 0xac54 — read `y1-track-info` / `y1-trampoline-state`, emit INTERIM/CHANGED + multi-attribute GetElementAttributes responses, proactive CHANGED on Y1 track changes and play/pause edges via the patched `notificationTrackChangedNative` (T5) and `notificationPlayStatusChangedNative` (T9), plus iter19a's PDU 0x17/0x18 ack-responses for Inform PDU spec compliance), and **LOAD #1 program-header extension** (FileSiz/MemSiz 0xac54 → 0xb1c4 to map the trampoline blob as R+E). The trampoline blob is built dynamically by `_trampolines.py` using a tiny Thumb-2 assembler (`_thumb2asm.py`). Per AVRCP 1.4 §5.4.3.4 / §6.7.1 / §6.7.2 — see [`AVRCP13-COMPLIANCE-PLAN.md`](../../docs/AVRCP13-COMPLIANCE-PLAN.md) for spec mapping and [`../../docs/INVESTIGATION.md`](../../docs/INVESTIGATION.md) "Hardware test history per CT" for the empirical observations driving each iter. Pairs with P1 in `patch_mtkbt.py`. Wired by `apply.bash --avrcp`. |
| **`patch_mtkbt_odex.py`** | `MtkBt.odex` | F1 (`getPreferVersion()` returns 14), F2 (`disable()` resets `sPlayServiceInterface`), iter17a (NOP `if-eqz` cardinality gate at 0x3c530 in `BTAvrcpMusicAdapter.handleKeyMessage` so `notificationTrackChangedNative` fires on every Y1MediaBridge track-change broadcast), **iter22b** (NOP `if-eqz` cardinality gate at **0x3c4fe** in the sswitch_18a / event 0x01 case so `notificationPlayStatusChangedNative` fires on every Y1MediaBridge play/pause broadcast — pairs with T9 in `patch_libextavrcp_jni.py`). Recomputes DEX adler32. Wired by `apply.bash --avrcp`. |
| **`patch_y1_apk.py`** | `com.innioasis.y1*.apk` | Smali patches A/B/C for Artist→Album navigation, **Patch D** (iter21) bounding the FF/RW hold-loop in `PlayerService$startFastForward$1`/`$startRewind$1` to **50 iterations × 100 ms ≈ 5 s** and clearing `fastForwardLock` on cap (also flips `fastForwardLock` `private`→`public` so the lambda can `iput-boolean`), and **Patch E** (iter22d) expanding `PlayControllerReceiver`'s `KEY_PLAY → playOrPause()` branch to also accept `KEYCODE_MEDIA_PLAY` (126) and `KEYCODE_MEDIA_PAUSE` (127), covering CTs that issue discrete PASSTHROUGH 0x44 PLAY (per AVRCP 1.4 §11.1.2) which `libextavrcp_jni` maps to `KEYCODE_MEDIA_PLAY` rather than the `KEYCODE_MEDIA_PLAY_PAUSE` toggle. Uses androguard + apktool. |

Per-patch byte-level reference (offsets, before/after bytes, rationale): [`../../docs/PATCHES.md`](../../docs/PATCHES.md).

## Common interface

Each byte patcher (mtkbt / mtkbt_odex / libextavrcp_jni) takes:

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
- `patch_y1_apk.py` additionally requires Java 11–21 (apktool 2.9.3's bundled smali assembler can silently drop patches on Java 22+ — the patcher warns at startup and refuses to write the APK if its DEX-signature check fails) and `androguard` (`pip install androguard`). The apktool jar is downloaded once into `tools/apktool-2.9.3.jar` (md5-verified) and reused on subsequent runs. The decoded smali tree + rebuilt DEX live under `staging/y1-apk/` and are retained between runs for inspection; pass `--clean-staging` for a fresh decode. The script also pins the input APK to the stock 3.0.2 md5 by default — pass `--skip-md5` to bypass for diagnostic runs.

## Status

Active patchers (wired into the bash):
- `patch_mtkbt.py`, `patch_mtkbt_odex.py`, `patch_libextavrcp_jni.py`, `patch_y1_apk.py`

Earlier byte-patch attempts at `/sbin/adbd` (the H1/H2/H3 patches in `patch_adbd.py` / `patch_bootimg.py`, both broke ADB protocol on hardware) were removed in v2.1.0 and superseded by [`../su/`](../su/) (setuid `/system/xbin/su`). The historical analysis is preserved in [`../../CHANGELOG.md`](../../CHANGELOG.md) and [`../../docs/INVESTIGATION.md`](../../docs/INVESTIGATION.md) §"adbd Root Patches (H1/H2/H3)".

## See also

- [`../../README.md`](../../README.md) — project overview
- [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) — **AVRCP metadata proxy architecture**: data-path diagram, trampoline chain (T1/T2/T4/T5), response builder calling conventions, ELF program-header surgery, code-cave inventory. Read this first if extending the trampoline chain or adding new PDU handlers.
- [`../../docs/PATCHES.md`](../../docs/PATCHES.md) — per-patch byte-level reference (offsets, before/after bytes, rationale)
- [`../../docs/DEX.md`](../../docs/DEX.md) — DEX-level analysis backing `patch_y1_apk.py`'s smali patches
- [`../../docs/INVESTIGATION.md`](../../docs/INVESTIGATION.md) — chronological investigation history (gdbserver capture iterations, dead-end paths, hypothesis evolution)
- [`../../CHANGELOG.md`](../../CHANGELOG.md) — top-level changelog

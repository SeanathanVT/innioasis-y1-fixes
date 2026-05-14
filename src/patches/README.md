# patches

Byte-level and smali patchers for Innioasis Y1 firmware binaries. Invoked by the top-level [`apply.bash`](../../apply.bash); each patcher can also be run standalone for inspection.

## Files

| Patcher | Target | Wired by |
|---|---|---|
| **`patch_mtkbt.py`** | `mtkbt` (Bluetooth daemon) — SDP shape (V1/V2/V3/V4/V7/V8/S1) + activeVersion override (V6) + force-PASSTHROUGH-emit op_code dispatch (P1) + best-effort AVDTP sig 0x0c → sig 0x02 dispatch alias (V5) + RegNotif INTERIM/CHANGED dispatch cmp constant widened from 1 to 0x0F so wire ctype matches the JNI's reasonCode (M1) | `--avrcp` |
| **`patch_libextavrcp_jni.py`** | `libextavrcp_jni.so` — R1 redirect, T1/T2-stub trampolines, the dynamically-assembled trampoline blob in LOAD #1 padding (extended_T2/T4/T5/T_charset/T_battery/T_continuation/T6/T8/T9), U1 kernel-auto-repeat NOP, and the LOAD #1 program-header extension. Blob built by `_trampolines.py` using the Thumb-2 assembler in `_thumb2asm.py`. | `--avrcp` |
| **`patch_libextavrcp.py`** | `libextavrcp.so` — E1: 2-byte CBZ→NOP inside `btmtk_avrcp_send_get_element_attributes_rsp` so unsupported attributes emit with `AttributeValueLength=0` per AVRCP 1.3 §5.3.4 instead of being silently dropped. Lets strict CTs that gate their metadata-pane render on receiving every requested attribute back accept Y1's response. | `--avrcp` |
| **`patch_mtkbt_odex.py`** | `MtkBt.odex` — F1 (`getPreferVersion()` flag), F2 (`disable()` reset of `sPlayServiceInterface`), and two cardinality NOPs that wake `notificationTrackChangedNative` / `notificationPlayStatusChangedNative` on every `metachanged` / `playstatechanged` broadcast. Recomputes DEX adler32. | `--avrcp` |
| **`patch_y1_apk.py`** | `com.innioasis.y1*.apk` — smali patches A/B/C (Artist→Album navigation), Patch E (discrete PASSTHROUGH PLAY/PAUSE/STOP routing in `PlayControllerReceiver`), Patch H (`BaseActivity.dispatchKeyEvent` propagates unhandled media keys past the foreground activity). Uses androguard + apktool. | `--music-apk` (A/B/C/H), `--avrcp` (E) |
| **`patch_libaudio_a2dp.py`** | `libaudio.a2dp.default.so` — single-byte cond-flip in `A2dpAudioStreamOut::standby_l` so AudioFlinger's silence-timeout standby leaves the AVDTP source stream alive (no SUSPEND on the wire). Matches AVDTP 1.3 §8.13 / §8.15 expectation that PAUSED leaves the stream paused-but-up. | `--avrcp` |

Per-patch byte-level reference (offsets, before/after bytes, rationale, ICS row coverage, spec citations): [`../../docs/PATCHES.md`](../../docs/PATCHES.md).

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
- `patch_mtkbt.py`, `patch_mtkbt_odex.py`, `patch_libextavrcp_jni.py`, `patch_libaudio_a2dp.py`, `patch_y1_apk.py`

Root escalation is handled by [`../su/`](../su/) (setuid `/system/xbin/su`).

## See also

- [`../../README.md`](../../README.md) — project overview
- [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) — **AVRCP metadata proxy architecture**: data-path diagram, trampoline chain, response-builder calling conventions, ELF program-header surgery, code-cave inventory. Read this first if extending the trampoline chain or adding new PDU handlers.
- [`../../docs/PATCHES.md`](../../docs/PATCHES.md) — per-patch byte-level reference (offsets, before/after bytes, rationale)
- [`../../docs/INVESTIGATION.md`](../../docs/INVESTIGATION.md) — chronological investigation history (gdbserver capture work, dead-end paths, hypothesis evolution)
- [`../../CHANGELOG.md`](../../CHANGELOG.md) — top-level changelog

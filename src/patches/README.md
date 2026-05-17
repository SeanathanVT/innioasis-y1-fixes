# patches

Byte-level and smali patchers for Innioasis Y1 firmware binaries. Invoked by the top-level [`apply.bash`](../../apply.bash); each patcher can also be run standalone for inspection.

## Files

| Patcher | Target | Wired by |
|---|---|---|
| **`patch_mtkbt.py`** | `mtkbt` — SDP shape (V1/V2/V3/V4/V7/V8/S1), activeVersion override (V6), force-PASSTHROUGH-emit op_code dispatch (P1), best-effort AVDTP sig 0x0c → sig 0x02 alias (V5), M1 (widens the RegNotif INTERIM/CHANGED dispatcher cmp from 1 to 0x0F so wire ctype matches the JNI's reasonCode), M2 + M3 (Path A: bypass outbound-frame builder's chip-readiness list-check + chip-busy flag set for the rare msg_id ≤ 6 paths), M4 (Path B: same list-check bypass on the structurally-identical twin builder used for all common AVRCP responses — `msg=540` GEA, `msg=544` RegNotif INTERIM/CHANGED, `msg=522` GetCapabilities), and M6 (Path B TID slot: lift the AVCTP transaction-label from the IPC payload byte 5 into the chan-struct slot the wire-frame builder reads, so RegNotif INTERIM/CHANGED responses echo the originating RegisterNotification CMD's transId per AVRCP 1.3 §6.5 / §6.7.2). | `--avrcp` |
| **`patch_libextavrcp_jni.py`** | `libextavrcp_jni.so` — R1 redirect, T1/T2-stub trampolines, the dynamically-assembled trampoline blob (extended_T2/T4/T5/T_charset/T_battery/T_continuation/T6/T8/T9), U1 (kernel auto-repeat NOP), and the LOAD #1 program-header extension. Blob built by `_trampolines.py` via `_thumb2asm.py`. | `--avrcp` |
| **`patch_libextavrcp.py`** | `libextavrcp.so` — E1: 2-byte CBZ→NOP inside `btmtk_avrcp_send_get_element_attributes_rsp` so unsupported attributes emit with `AttributeValueLength=0` per AVRCP 1.3 §5.3.4 (stock silently drops them). | `--avrcp` |
| **`patch_mtkbt_odex.py`** | `MtkBt.odex` — F1 (`getPreferVersion()` flag), F2 (`disable()` resets `sPlayServiceInterface`), two cardinality NOPs that wake `notificationTrackChangedNative` / `notificationPlayStatusChangedNative` on every `metachanged` / `playstatechanged` broadcast. Recomputes DEX adler32. | `--avrcp` |
| **`patch_y1_apk.py`** | `com.innioasis.y1*.apk` — A/B/C (Artist→Album navigation), Patch E (discrete PASSTHROUGH PLAY/PAUSE/STOP routing), Patch H (`BaseActivity.dispatchKeyEvent` propagates unhandled media keys). Uses androguard + apktool. | `--music-apk` (A/B/C/H), `--avrcp` (E) |
| **`patch_libaudio_a2dp.py`** | `libaudio.a2dp.default.so` — single-byte cond-flip in `A2dpAudioStreamOut::standby_l` so AudioFlinger's silence-timeout standby leaves the AVDTP source stream alive (no SUSPEND on the wire). Matches AVDTP 1.3 §8.13 / §8.15. | `--avrcp` |

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
- `patch_y1_apk.py` additionally requires Java 11–21 (apktool 2.9.3's bundled smali assembler can silently drop patches on Java 22+ — the patcher refuses to write if its DEX-signature check fails) and `androguard` (`pip install androguard`). apktool is downloaded once into `tools/apktool-2.9.3.jar` and reused. Decoded smali + rebuilt DEX persist under `staging/y1-apk/` (`--clean-staging` forces a fresh decode). MD5 pinned to stock 3.0.2 / 3.0.7; `--skip-md5` bypasses.

## Debug logging

`apply.bash --debug` (or `KOENSAYR_DEBUG=1` in the env) routes diagnostic logging through three independent tags:

- **`Y1Patch :`** — Java-side. `patch_y1_apk.py` injects `Log.d("Y1Patch", …)` at every metadata-relevant entry point and inline `_dbgKV(key, long)` at diagnostic-critical sites in `TrackInfoWriter` + `PlaybackStateBridge.onPlayValue`. Tail with `adb logcat -s Y1Patch:*`.
- **`Y1T :`** — Native trampoline-side. `patch_libextavrcp_jni.py` splices `__android_log_print(INFO, "Y1T", fmt, value)` into T4 / T5 / T8 / T9 emit sites. Surfaces inbound `RegisterNotification` event_ids, outbound CHANGED arg values (PSTAT / POS / track UID), and per-attribute strlen for GEA responses. Tail with `adb logcat -s Y1T:*`; parse GEA wire-size predictions via [`../../tools/avrcp-wire-trace.py`](../../tools/avrcp-wire-trace.py).
- **mtkbt's own xlog stream (btlog).** Not affected by `--debug` — these are stock mtkbt internals (`avctpCB`, `[AVCTP] chid:`, `avrcp: sbunit type:`). Capture with `tools/dual-capture.sh` and parse via [`../../tools/btlog-parse.py`](../../tools/btlog-parse.py) (use `--avrcp` for the AVRCP/AVCTP-only preset that pairs cleanly with the `Y1T` logcat trace).

Release builds are byte-identical without the env var. Coverage list: [`../../docs/PATCHES.md`](../../docs/PATCHES.md) §"`--debug` instrumentation".

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

# patches

Byte-level and smali patchers for Innioasis Y1 firmware binaries. Invoked by the top-level [`apply.bash`](../../apply.bash); each patcher can also be run standalone for inspection.

## Files

| Patcher | Target | Patches |
|---|---|---|
| **`patch_mtkbt.py`** | `mtkbt` (Bluetooth daemon) | V1 (AVRCP 1.0→1.3), V2 (AVCTP 1.0→1.2), S1 (replace 0x0311 SupportedFeatures slot with 0x0100 ServiceName pointing at existing "Advanced Audio" string), P1 (force fn 0x144bc op_code dispatch to PASSTHROUGH branch → bl 0x10404 → msg 519 emit) — **4 patches**. AVRCP 1.3 reference SDP shape + bypass mtkbt's silent-drop of VENDOR_DEPENDENT 1.3+ COMMANDs. Wired by `apply.bash --avrcp`. |
| **`patch_libextavrcp_jni.py`** | `libextavrcp_jni.so` | R1 (redirect `bne.n 0x65bc` → `bl.w 0x7308`), T1 (GetCapabilities response trampoline at 0x7308 — overwrites `testparmnum`), T2 stub (4-byte `b.w extended_T2` at 0x72d4 + classInitNative `return 0` stub at 0x72d0 — overwrites `classInitNative`), extended_T2 + T4 + T5 + T_charset + T_battery + T_continuation + T6 + T8 + T9 (in LOAD #1 page-padding region at 0xac54 — read `y1-track-info` / `y1-trampoline-state`, emit INTERIM/CHANGED + 7-attribute GetElementAttributes responses covering all AVRCP 1.3 §5.3.4 attr IDs 0x01..0x07, proactive CHANGED on Y1 track changes and play/pause/battery edges via the patched `notificationTrackChangedNative` (T5: emits the §5.4.2 track-edge 3-tuple TRACK_REACHED_END (0x03 — gated on natural-end flag) + TRACK_CHANGED (0x02) + TRACK_REACHED_START (0x04) per ICS Table 7 rows 24/25/26) and `notificationPlayStatusChangedNative` (T9: emits PLAYBACK_STATUS_CHANGED 0x01 + BATT_STATUS_CHANGED 0x06 each on edge + PLAYBACK_POS_CHANGED 0x05 at 1 s cadence while playing per ICS Table 7 rows 23/27/28; Y1MediaBridge fires `playstatechanged` on real play edges, battery-bucket transitions, and a 1 s tick while playing to drive T9), plus PDU 0x17/0x18 ack-responses for Inform PDU spec compliance, plus PDU 0x40/0x41 explicit reject via T_continuation per ICS Table 7 rows 31/32), **U1** (NOP the `UI_SET_EVBIT(EV_REP)` ioctl at 0x74e8 inside `avrcp_input_init` so the AVRCP `/dev/input/event4` virtual keyboard never claims EV_REP and the kernel never enables `input_enable_softrepeat()` for it; eliminates the ~25 Hz `KEY_xxx REPEAT` cascade on dropped PASSTHROUGH RELEASE that drove the haptic-loop symptom on strict CTs), and **LOAD #1 program-header extension** (FileSiz/MemSiz 0xac54 → 0xb2c8 to map the trampoline blob as R+E). The trampoline blob is built dynamically by `_trampolines.py` using a tiny Thumb-2 assembler (`_thumb2asm.py`). Per AVRCP 1.3 §5.4.1 (GetPlayStatus) / §5.4.2 (RegisterNotification, including TRACK_CHANGED Table 5.30) / §5.2.7 (InformDisplayableCharacterSet) / §5.2.8 (InformBatteryStatusOfCT) / §4.6.1 (PASS THROUGH, +AV/C Panel Subunit Spec) — and ESR07 §2.2 for the 8-byte TRACK_CHANGED Identifier sentinel clarification we apply. See [`AVRCP13-COMPLIANCE.md`](../../docs/AVRCP13-COMPLIANCE.md) §0 for the citation discipline + spec PDFs in [`docs/spec/`](../../docs/spec/) and [`../../docs/INVESTIGATION.md`](../../docs/INVESTIGATION.md) "Hardware test history per CT" for the per-CT empirical observations. Pairs with P1 in `patch_mtkbt.py`. Wired by `apply.bash --avrcp`. |
| **`patch_mtkbt_odex.py`** | `MtkBt.odex` | F1 (`getPreferVersion()` returns 14), F2 (`disable()` resets `sPlayServiceInterface`), and two cardinality NOPs in `BTAvrcpMusicAdapter.handleKeyMessage` (`if-eqz` at 0x3c530 / sswitch_1a3 so `notificationTrackChangedNative` fires on every Y1MediaBridge track-change broadcast; `if-eqz` at 0x3c4fe / sswitch_18a for event 0x01 so `notificationPlayStatusChangedNative` fires on every play/pause broadcast — pairs with T5/T9 in `patch_libextavrcp_jni.py`). Recomputes DEX adler32. Wired by `apply.bash --avrcp`. |
| **`patch_y1_apk.py`** | `com.innioasis.y1*.apk` | Smali patches A/B/C for Artist→Album navigation; **Patch E** splitting `PlayControllerReceiver`'s short-press `KEY_PLAY → playOrPause()` branch into four discrete arms per AVRCP 1.3 §4.6.1 + AV/C Panel Subunit Spec [ref 2] + ICS Table 8 (op_id status for Cat 1 TGs, `docs/spec/AVRCP.ICS.p17.pdf` §1.5): `KEY_PLAY` (85, `KEYCODE_MEDIA_PLAY_PAUSE`) → `playOrPause()` (toggle, legacy MediaButton); `KEYCODE_MEDIA_PLAY` (126, from PASSTHROUGH 0x44 via `KEY_PLAYCD`) → `play(true)` (discrete PLAY, ICS Table 8 item 19 mandatory); `KEYCODE_MEDIA_PAUSE` (127, from PASSTHROUGH 0x46 via `KEY_PAUSECD`) → `pause(0x12, true)` (discrete PAUSE, item 21 optional); **`KEYCODE_MEDIA_STOP`** (86, from PASSTHROUGH 0x45 via `KEY_STOPCD`) → **`stop()V`** (discrete STOP, ICS Table 8 item 20 mandatory). Honors AV/C Panel Subunit Spec op_id 0x44's "transition to PLAYING from any state" semantic (concrete frame in AVRCP 1.3 §19.3 Appendix D); a CT-side discrete PLAY toggling a TG that was already playing — the failure mode if PLAY routed to `playOrPause()` — would invert the CT's intent. **Patch H** in `BaseActivity.dispatchKeyEvent` (`smali/com/innioasis/y1/base/BaseActivity.smali`): the stock implementation always returns TRUE so every KeyEvent reaching the foreground activity is consumed, but the activity only acts on KeyMap-listed scroll-wheel keycodes — discrete media keycodes that the activity does not recognise (`KEYCODE_MEDIA_PLAY` 0x7e, `KEYCODE_MEDIA_PAUSE` 0x7f, `KEYCODE_MEDIA_STOP` 0x56) fall through every if-eq check and reach `return v0`(TRUE) without any action, silently swallowing the event before it can reach `PhoneFallbackEventHandler` → `AudioService` → `PlayControllerReceiver`. Patch H inserts an early `return false` for those three keycodes immediately after `getKeyCode()`, restoring spec-correct routing for AVRCP PASSTHROUGH 0x44/0x45/0x46 end-to-end. Uses androguard + apktool. |

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
- [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) — **AVRCP metadata proxy architecture**: data-path diagram, trampoline chain (T1/T2/extended_T2/T4/T5/T_charset/T_battery/T_continuation/T6/T8/T9 + R1/U1), response builder calling conventions, ELF program-header surgery, code-cave inventory. Read this first if extending the trampoline chain or adding new PDU handlers.
- [`../../docs/PATCHES.md`](../../docs/PATCHES.md) — per-patch byte-level reference (offsets, before/after bytes, rationale)
- [`../../docs/INVESTIGATION.md`](../../docs/INVESTIGATION.md) — chronological investigation history (gdbserver capture work, dead-end paths, hypothesis evolution)
- [`../../CHANGELOG.md`](../../CHANGELOG.md) — top-level changelog

# Investigation — Final Status

This document grew organically over the 2026-05-02 / 2026-05-03 sessions. **Read this top section first** — sections below preserve the original investigation narrative including hypotheses that were later refuted, so reading top-down without this summary is misleading.

## Final state (after all traces complete)

The shipped patch set:
- `mtkbt.patched` (11 patches: **B1-B3, C1-C3, A1, D1, E3, E4, E8**) — MD5 `d47c904063e7d201f626cf2cc3ebd50b`
- `libextavrcp_jni.so.patched` (4 patches: **C2a/b, C3a/b**)
- `libextavrcp.so.patched` (1 patch: **C4**)
- `MtkBt.odex.patched` (2 patches: **F1, F2**)

Patches **E5, E7a, E7b were tested and removed** — they patched live code that was never exercised at runtime for our peer state, so they had no observable effect.

**E8 added 2026-05-02 and tested same-day as inert.** NOPing `bge #0x30688` at `0x3065e` in fn `0x3060c` (op_code=4 dispatcher slot 0) had no observable effect on cardinality:0. Inspection of the test logcat showed only msg_ids 505 and 506 received — **no GetCapabilities (`op_code=4`) ever arrives at any of the three dispatchers** (`0x3060c`, `0x30708`, `0x3096c`). The gate is upstream of the dispatcher table itself — somewhere in mtkbt's AVCTP receive path between L2CAP and the dispatcher. E8 left in place as a verified-correct patch even though inert.

**G1/G2 attempt 1 (2026-05-02) — reverted (SIGSEGV at NULL).** 12-byte thunk forwarded r2 (fmt) as both tag and fmt for android_log_print. mtkbt SIGSEGV at addr 0 immediately at startup — at least one xlog callsite passes NULL in r2; bionic's `__android_log_print` at API 17 doesn't NULL-check the tag, so `strlen(NULL)` faulted.

**G1 attempt 2 (2026-05-03) — reverted (BT does not turn on).** 20-byte thunk added a `cbz r2, .L_null` guard. NULL guard didn't help. BT framework log shows `bt_sendmsg(cmd=100, ...)` returns ENOENT — mtkbt's abstract socket never came up. Either mtkbt crashed on a non-NULL but invalid pointer (small int, stack pointer), or the redirected log volume flooded logd and slowed mtkbt's init past the framework timeout.

**Conclusion: blanket xlog→logcat redirect at the consolidated wrapper is too fragile.** The wrapper at `0x675c0` is hit 2988 times across mtkbt's lifecycle including very early init when bionic's logd may not be ready, and the calling-convention assumption (r2 = valid fmt pointer) doesn't hold for every callsite. Future diagnostic instrumentation, if attempted, must be surgical: explicit `bl __android_log_print` calls at a small number of high-value sites (dispatcher entries, AVCTP RX handler at fn `0x6d9ba`, the silent-drop site at `0x0513a4`) with hardcoded tag/fmt string args. That requires finding free space in mtkbt for tag/fmt strings and either a trampoline or in-place call-site rewrite — significantly more complex than what was attempted.

## Verified true (with corrections from earlier in this doc)

- **mtkbt IS the AVRCP processor** on this device. (Earlier in this doc I hypothesized the BT chip firmware was the processor — that was wrong. The chip firmware blob is the WMT common subsystem, contains zero AVRCP code.)
- **None of mtkbt's documented AVRCP/AVCTP functions are dead code** in the sense earlier in this doc claimed. (Earlier I concluded they were dead because no caller mechanism I searched found references — that conclusion was wrong.) `0x29e98` is reached via PIC-style callback registration through `register_callback` at `0x2fecc`, called from `0x28a5e` with the fn ptr computed by `ldr r1, [pc, #0x17c]; add r1, pc` (literal `0x1439`). `0x3096c` is reached via `R_ARM_RELATIVE` relocation installing it into a 3-slot fn-ptr table at vaddr `0xf94b0..0xf94bc` (slot 2). Same for the other 0-caller functions: they are reached, just through callback-registration mechanisms my earlier scans missed. The AV/C parser at `0x6d04a` is the **only** function still confirmed dead — multiple independent scans found zero references via every mechanism (literal pools, `R_ARM_RELATIVE`, ADR/ADD-PC, MOVW+MOVT, `ABS32`, no callers).
- **The `mtkbt` SDP layer patches all land on the wire.** sdptool confirms AVRCP 1.4 + AVCTP 1.3 + SupportedFeatures 0x0033 served by mtkbt to peers.
- **The Java layer (`MtkBt.apk`) is correctly initialized for AVRCP 1.4** post-F1/F2. `getSupportVersion()` returns 0xe (1.4) when `sPlayServiceInterface == true`. `checkCapability()` builds the 1.4-aware EventList `[1, 2, 9, 10, 11]` (PLAYBACK_STATUS_CHANGED, TRACK_CHANGED, NOW_PLAYING_CONTENT_CHANGED, AVAILABLE_PLAYERS_CHANGED, ADDRESSED_PLAYER_CHANGED). `BTAvrcpMusicAdapter.registerNotification(eventId)` would correctly handle events 1/2/9 if invoked, log `[BT][AVRCP] mRegBit set %d Reg:%b cardinality:%d`, and update the cardinality bitset.
- **`Y1MediaBridge.apk` is correctly implemented** as a dual-interface (IBTAvrcpMusic + IMediaPlaybackService) Binder bridge. F1/F2 + the bridge + the SDP-layer patches form a complete & correct user-space chain.

## Where the cardinality:0 gate is

Logcat across multiple full connection cycles shows neither:
- `[BT][AVRCP](test1) registerNotificationInd eventId:%d` (the JNI→Java entry log) nor
- `[BT][AVRCP] mRegBit set %d Reg:%b cardinality:%d` (the cardinality update log) nor
- `[BT][AVRCP] MusicAdapter blocks support register event:%d` (the rejection log)

So no inbound REGISTER_NOTIFICATION events reach Java. Combined with the existing observation that no `Recv AVRCP indication` msg_ids beyond 501/505/506/512 (ACTIVATE_CNF / connect_ind / CONNECT_CNF / DISCONNECT_CNF) reach the JNI receive loop, **the gate is unambiguously inside mtkbt's native AVRCP layer, between AVCTP RX and the JNI dispatch socket**.

**Refined 2026-05-02 (post-E8 test):** Logcat over the E8 test cycle confirms only msg_ids 505 and 506 ever arrive. **No `op_code=4` (GetCapabilities) message ever reaches any of the three dispatchers** (`0x3060c`, `0x30708`, `0x3096c`) — verified because E8 NOP'd the gate at `0x3065e` in fn `0x3060c` and the patch had zero observable effect, AND the post-dispatcher init path at `0x2fd34` is never logged either. **The gate is upstream of the dispatcher table itself.** The most plausible upstream points are:

- mtkbt's AVCTP receive handler at fn `0x6d9ba` — silently drops the inbound L2CAP frame before dispatch.
- The silent-drop site at `0x0513a4` (`[AVRCP][WRN] AVRCP receive too many data. Throw it!`) — sized check that may reject GetCapabilities under unknown conditions.
- The L2CAP→AVCTP demux logic upstream of `0x6d9ba` — wrong PSM routing, missing peer-state guard, etc.
- The `bws:0 tg_feature:0 ct_featuer:0` in the CONNECT_CNF log line suggests mtkbt's per-connection feature negotiation is failing on the daemon side — peers may be classified as "no AVRCP capability" before any GetCapabilities even gets a chance to arrive.

Which one is the real gate cannot be determined statically without observing runtime decisions, and runtime visibility into mtkbt is the chronic blind spot — it logs only via `__xlog_buf_printf` (separate buffer, invisible without root or daemon-side tooling).

**Strengthened 2026-05-03 (post Trace #7):** the four `libbluetooth*.so` libs were inspected end-to-end and confirmed HCI/transport-only — zero AVRCP/AVCTP code anywhere outside `mtkbt`. The gate has no other place it could live. See "Trace #7 — Findings" for full details.

**New observation 2026-05-03 (test.log, peer `38:42:0B:38:A3:3E`):** `MSG_ID_BT_AVRCP_CONNECT_CNF conn_id:1  result:4096`. The `result` field on a successful AVRCP connect should be `0`. **`4096 = 0x1000` is non-zero**, suggesting mtkbt is reporting the connection as accepted-but-degraded (encryption pending, version downgrade flag, or feature-mismatch indicator). This pairs with the `bws:0 tg_feature:0 ct_featuer:0` line — both fields read straight off the message mtkbt sends over the JNI socket. The non-zero `result` is consistent with the peer never escalating to GetCapabilities and is now the most concrete static-investigation target. Strings present in mtkbt that would name the relevant branch under root + xlog visibility: `AVRCP register activeVersion:%d`, `[AVRCP] AVRCP activate version:%d`, `[AVRCP] avctpCB state:%d retryCount:%d retryFlag:%d`. This had not been called out in prior log analyses.

## Remaining diagnostic options

All require capabilities we don't have:
- **HCI snoop / btsnoop** — needs root.
- **Capture daemon-side `__xlog_buf_printf` traces** — Mediatek's separate log buffer, requires special tooling.
- **Runtime instrumentation patches that redirect xlog → logcat** — attempted twice (G1/G2 with NULL guard) and broke Bluetooth both times. The wrapper at `0x675c0` is hit ~3000 times across mtkbt's lifecycle, including very early init when bionic's logd may not be ready, and the calling-convention assumption (r2 = valid fmt pointer) doesn't hold uniformly. Path now considered closed within current constraints.
- **Surgical instrumentation at specific high-value sites** (dispatcher entries, AVCTP RX handler, silent-drop site) with hardcoded tag/fmt strings via a trampoline. Doable in principle but each site is its own potential crash vector and requires finding free space in the binary for tag/fmt strings.

## Single concrete patch candidate identified but not shipped — UPDATE: shipped

Trace #1g exposed a clean-looking patch site in fn `0x3060c` (slot 0 of the dispatcher table): NOP the `bge` at `0x3065e` to force the 1.3/1.4 init path regardless of `[conn+0x149]`'s sign. Single-byte change `13 da → 00 bf`.

**Originally not shipped** because (a) we can't tell whether fn `0x3060c` is selected at runtime for our peers vs. fn `0x30708` or fn `0x3096c`, and (b) for "correctly classified" peers (high bit set in `[conn+0x149]`) the gate doesn't fire and the patch is inert.

**Reversed 2026-05-02 — now shipped as E8.** Re-examination of the brute-force "patch all three" plan showed the other two dispatcher candidates do not have analogous clean patch sites (see Final state above), so the cost calculus changed: E8 is the only viable single-instruction probe of the three, it's a one-byte change to a code path that's either the runtime gate (fix) or unexercised for our peers (no-op), and shipping it is strictly more informative than not. If E8 does not change cardinality:0, runtime selection is not fn `0x3060c` and the only remaining static analysis option is Option B — redirecting `__xlog_buf_printf` to `__android_log_print` to make mtkbt's daemon-side decisions visible in logcat.

---

## Original narrative (preserved for audit trail)

What follows is the original investigation order. Some sections contain hypotheses that were later refuted — those refutations are in subsequent sections. Read the Final Status above for the corrected picture.

## Planned Traces

### 1. mtkbt format-string xref scan  (highest value, pure static analysis)

Every `[AVCTP]`/`[AVRCP]` format string in `mtkbt` is referenced from somewhere via PC-relative addressing (`ldr rN, [pc, #imm]; add rN, pc`). Compute literal-pool entries that resolve to each string's address and find every callsite.

Likely to reveal:
- The function containing `[AVCTP] cmdFrame->ctype:%d cmdFrame->opcode:%d` — the AV/C command dispatcher entry; the choke point we've been missing.
- The function containing `[AVCTP] AVCTP_ConnectRsp not in incoming state:%d` — mtkbt's AVCTP state machine.
- The function containing `[AVRCP][WRN] AVRCP receive too many data. Throw it!` — the silent-drop point.

Output: a map of every `[AVCTP]`/`[AVRCP]` log point in the binary, with surrounding function context.

### 2. ACTIVATE_REQ (msg=500) handler in mtkbt

When the JNI sends `msg=500, payload[6]=0x0e (tg_feature), payload[7]=ct_feature`, mtkbt receives this on the abstract `bt.ext.adp.avrcp` socket and dispatches to a handler. The handler stores the TG feature globals on mtkbt's side. Find that handler. Verify whether the stored globals persist across `connect_ind`/`CONNECT_RSP` or get cleared per-connection.

### 3. CONNECT_RSP (msg=507) handler in mtkbt

Same path. JNI sends accept-flag-only via msg=507 (bytes [6][7]=0). Find mtkbt's response handler. If it has a code path like "if features == 0 then mark connection as 1.0" — that's the gate.

### 4. AVCTP PSM-registration path

`[AVCTP] register psm 0x%x status:%d` is a log string in mtkbt. Find the registration function — verify it actually registers L2CAP PSM 0x17 for inbound and what callback it installs. If the callback is missing or wrong-pointer, no AVCTP frames ever get parsed.

### 5. Decompile `MtkBt.apk` (Java side)

Only two methods are patched in `MtkBt.odex` (F1 `getPreferVersion`, F2 `disable() reset`). The full `BluetoothAvrcpService` Java class includes the connect-event listener, the play-service interface, and any feature-gate logic on the Java side. May reveal additional version checks not yet touched.

### 6. Inspect `Y1MediaBridge.apk` and verify it plays nicely with the patches

The mediabridge service is what supplies metadata to the AVRCP service. Confirm it implements the right callbacks and doesn't unintentionally suppress events that would otherwise propagate to a registered controller. (Source available — first-party app.)

### 7. Inspect `libbluetoothdrv.so`

mtkbt links against this. It almost certainly contains the actual L2CAP send/receive primitives. The `[AVCTP] register psm` call from mtkbt resolves into this library. If the bug lives there, mtkbt is innocent and we've been chasing the wrong binary.

**Findings (2026-05-03):** see "Trace #7 — Findings" below. All four `libbluetooth*` libs are HCI/transport-only — zero AVRCP/AVCTP code. The hypothesis was wrong; mtkbt is not innocent.

### 8. Verify `/system/etc/bluetooth/` config end-state on device

`audio.conf`, `auto_pairing.conf`, `blacklist.conf` are touched by the bash flasher but the on-device final state has never been read back. Confirm the patches landed and there's no `Disable=` or similar override.

## Trace #1 — Findings (2026-05-02)

Format-string xref scan complete. All 26 `[AVCTP]`/`[AVRCP]` log strings located, every callsite mapped via `ldr+add r,pc` literal-pool resolution.

**Surprising finding:** six of eight key documented functions in mtkbt have zero static references — not direct `bl/blx` targets, not branch targets, not stored as 4-byte literals anywhere, and not computed via ADR / ADD-PC / movw-movt arithmetic visible to static scan.

| Function | Direct callers |
|---|---:|
| `0x028c98` connect handler (state=1) | 12 |
| `0x029910` REGISTER_NOTIFICATION dispatcher | 22 |
| `0x0290bc` state=3 setter | 6 |
| `0x029294` state=5 setter | 5 |
| `0x06cf30` AVCTP_ConnectRsp | 2 |
| `0x038a44` SDP init function | 1 (tail call) |
| `0x0513a4` AVRCP silent-drop | 1 |
| `0x00fa94` AVRCP avctpCB | 1 |
| `0x029e1c` callback dispatcher TBH | **0** |
| `0x02fd02` AVRCP 1.3/1.4 initializer | **0** |
| `0x030708` op_code dispatcher (E5 site) | **0** |
| `0x06d040` AV/C command parser | **0** |
| `0x06d25c` AVCTP register PSM | **0** |
| `0x06d9ba` AVCTP RX handler | **0** |

**Implications:**

- The AV/C command parser at `0x06d04a` (which we patched the surrounding logic of via E5) appears to be **unreachable code** from anywhere in mtkbt's static call graph. The `[AVCTP] cmdFrame->ctype:%d cmdFrame->opcode:%d` format string exists, the function exists, but no path leads to it.
- Same for the operation dispatcher containing the E5 patch site — also zero callers.
- `mtkbt` has no AVRCP/AVCTP exports in dynsym, so `libbluetoothdrv.so` can't resolve these by name at load time.
- `libbluetoothdrv.so` itself is only 9,280 bytes and contains zero AVRCP/AVCTP strings — it's a thin shim, not the processor.

**Working hypothesis:** the AVRCP/AVCTP code visible in mtkbt is **dead code** (leftover from a prior build that had the daemon do the processing). The actual AVRCP processing is happening either inside the Bluetooth chip firmware or via a path we haven't traced yet. This would explain:

- Why no AVRCP commands ever reach the JNI dispatch socket — mtkbt isn't the dispatcher.
- Why every patch we've made to mtkbt's command path (E5, E7) had no behavioral effect — those code paths are never executed.
- Why `tg_feature:0` persists in CONNECT_CNF — mtkbt's view, but the actual TG state lives elsewhere.

This makes patches like B1-B3, C1-C3 (SDP descriptors, which mtkbt *is* responsible for serving) genuinely effective on the wire (sdptool confirms), while the runtime command-path patches are necessarily inert.

## Trace #1b — Walk back from the 12 callers of `0x028c98` (executed 2026-05-02)

Find the actual entry point of mtkbt's connection logic. The 12 callers tell us where "new connection" events come from. Tracing the call chain back finds either an internal entry (in which case the dispatcher chain *does* exist somewhere in mtkbt that we haven't traced) or a PLT call into libbluetoothdrv.so (in which case the connection event originates from outside mtkbt and our search expands to firmware/IPC).

**Findings:**
- 12 callers in 12 distinct containing functions.
- Walking back 4 levels: 11 distinct top-level entry points (functions with 0 callers themselves) all eventually call `state=1`.
- Critically: `fn@0x029e98` (the "callback dispatcher TBH" identified in earlier analysis) appears at depth 2 in the walk — it's a top-level entry (0 direct callers) whose descendants include the state=1 setter. So 0x029e98 IS in the live call graph, reached from outside mtkbt as a callback.
- The deepest entry found is `fn@0x06adee` at depth 4, which has 0 callers but 3 call sites going down.
- **None of the "AV/C parser" / "op dispatcher" / "AVCTP RX handler" / "AVCTP register PSM" appear anywhere in this call tree.** They are not on the path from any top-level entry to the state=1 setter.

## Trace #1c — Scan for runtime writes to BSS function-pointer slots (executed 2026-05-02)

If the 0-caller functions are reached via callbacks, the registration site MUST write the function pointer somewhere. Scan for `add rN, pc, #imm; str rN, [rA, #imm]` patterns where the computed PC-relative target equals any of the 0-caller function addresses. Captures runtime callback registration sites missed by the literal-pool search.

**Findings:**
- Zero `add rN, pc, #imm; str` patterns matching any of the 0-caller function addresses.
- Full scan of `.data` (385 function pointers) and `.data.rel.ro.local` (1282 function pointers): **none point to** the AV/C parser, op dispatcher, AVCTP RX handler, AVCTP register PSM, or AVCTP_ConnectRsp containing fn.
- Full RX-segment scan for any 4-byte literal pointing to any of those addresses: zero hits.

## Trace #1 — interpretation

Three independent signals show that several of mtkbt's documented AVCTP/AVRCP functions have no static back-reference to live code:

1. **No direct or indirect callers** for the AV/C parser, op_code dispatcher (E5 patch site), AVCTP register PSM, AVCTP RX handler, or AVCTP_ConnectRsp containing fn.
2. **No stored function pointers** to any of these addresses in `.data`, `.data.rel.ro.local`, or any literal pool in the RX segment.
3. **Not on the live call graph** that drives the connection state setters reached at runtime (state=1/3/5 sites all have real callers; the "command path" code does not).

### Initial interpretation (REVISED — see below)

I initially concluded these were **dead code** and that the BT chip firmware was the actual AVRCP TG processor, with mtkbt only managing connection lifecycle. **That conclusion is wrong**, as confirmed by inspecting the actual chip firmware on disk.

### Why the firmware-does-AVRCP claim is wrong

The Y1 BT chip is **MT6627** (combo: BT + Wi-Fi + FM + GPS, on MT6572 SoC). The firmware blob is `/etc/firmware/mt6572_82_patch_e1_0_hdr.bin`, 39,868 bytes, build dated `20130523`. Inspecting its strings reveals it is the **WMT (Wireless/MediaTek) common subsystem firmware** — sleep states, coredump, queue management, GPS desense, Wi-Fi power on/off. It contains **zero** AVRCP/AVCTP/L2CAP-level strings and no profile-stack code. Confirmed by `strings` over the blob: only chip-level housekeeping content.

The actual stack architecture:

```
[mtkbt + libextavrcp_jni.so + MtkBt.apk]   ← Bluetooth profile stack, USERSPACE
        |   AVRCP / AVCTP / L2CAP / HCI parser, all in userspace
        v
[/dev/stpbt]
        |   HCI transport
        v
[mtk_stp_bt.ko]                            ← kernel module
        |
        v
[MT6627 chip]                              ← only handles radio + HCI commands
```

So mtkbt **is** the AVRCP processor. There's nowhere else the AVRCP frame parsing can live. Which means the "0-caller" functions in mtkbt **must** be reached at runtime through some mechanism static analysis missed.

### What this implies for the open question

- **What we got right**: SDP-layer patches (B1-B3, C1-C3, E3, E4, A1, D1) are genuinely effective — sdptool confirms the bytes land on the wire, and mtkbt is what serves SDP. These remain in the script.
- **What we got wrong**: removing E5 and E7 was the right operational call (they had no observable effect), but the *reason* I gave was incorrect. The real reason is most likely that **my static analysis missed the indirect-call mechanism that wires up mtkbt's AVRCP dispatcher functions to its live code path**. Trace #1c looked for a specific pattern (`add rN, pc, #imm; str rN, [rA, #imm]`) and found nothing, but there are other plausible mechanisms: function-pointer tables in `.rodata` indexed by op_code, vtable-style indirect dispatch through a struct field initialized at runtime by code I didn't trace, or a TBB/TBH-driven jump table whose target table is built dynamically.
- **The gate is still in mtkbt**, somewhere we haven't found. It's not in firmware.

## Trace #1d / #1e — Findings (executed 2026-05-02)

### What was missed in earlier traces

mtkbt is a **PIE executable** (ET_DYN with `e_entry=0xb558`, ARM mode). The dynamic loader applies relocations to its `.data.rel.ro` section at startup. Previous static-only function-pointer searches (literal pools, `add+str` patterns, `movw+movt` pairs) **completely missed** this because:

- `.rel.dyn` has 3982 entries: 374 ABS32 + 4 GLOB_DAT + 3604 RELATIVE.
- For PIE binaries with load_base=0 (mtkbt's case), R_ARM_RELATIVE entries effectively store `addend` at `r_offset` at load time — and the addend lives in the file as a raw 4-byte word at `r_offset` itself, indistinguishable from data until the loader runs.
- 2392 of those RELATIVE addends point into the RX segment (i.e., function pointers), forming function-pointer tables in `.data.rel.ro`.

### Concrete finding: the op-code dispatcher IS reachable

A 3-slot function-pointer table sits at vaddr `0xf94b0..0xf94bc`:

| vaddr | Thumb fn ptr | Function |
|---|---|---|
| `0xf94b0` | `0x3060c` | (unknown) |
| `0xf94b4` | `0x30708` | op-code dispatcher A (its own push prologue) |
| `0xf94b8` | `0x3096c` | op-code dispatcher B (the E5 patch site fn entry) |

All three are populated at load time by R_ARM_RELATIVE relocations. **`0x3096c` is the op_code=4 dispatcher (the function E5 patches inside).** It's a real runtime target, not dead code. My previous "dead code" verdict for E5 was based on incomplete static analysis — the relocation-driven mechanism wasn't searched.

A larger cluster at vaddr `0xf94c0..0xf954c` holds ~75 more Thumb function pointers — likely an op-code-indexed dispatch table for a different protocol layer.

### Status of the other "0-caller" functions

Even after the relocation scan, **zero** R_ARM_RELATIVE relocations install pointers to: AV/C parser (`0x6d040`/`0x6d04a`), AVCTP RX handler (`0x6d9ba`), AVCTP register PSM (`0x6d25c`), AVCTP_ConnectRsp containing fn (`0x6cf30`), callback dispatcher TBH (`0x29e1c`/`0x29e98`), or AVRCP 1.3/1.4 init (`0x02fd02`/`0x02fd34`). They're absent from every reference mechanism we know how to scan: direct branches, literal pools, ADR/ADD-PC arithmetic, MOVW+MOVT pair, R_ARM_ABS32, R_ARM_RELATIVE.

There's a contradiction with Trace #1b: the call-tree walk back from `0x028c98` (state=1 setter) showed `fn@0x029e98` (callback dispatcher TBH body) appearing at depth 2 as a top-level entry whose descendants include the state=1 setter. So `0x29e98` IS in the live call graph somehow, even though no relocation mechanism we've checked installs a pointer to it.

### Implications for E5

Reverting E5 may have been premature on the *operational* side — the function it patches IS reachable at runtime. But E5 still made no observable behavioral difference on three different controllers, which suggests one of:

1. The E5 patch site (the BNE inside the version-comparison logic) doesn't get exercised because mtkbt's runtime version classification for our peers takes a different branch before reaching the BNE.
2. The function `0x3096c` is reached only for specific op_codes that our peers don't send, so the patched code path never executes.
3. Our peers DO reach `0x3096c` at the right moment but with version data that bypasses the patch's effect.

We can't distinguish these without runtime visibility — and the chip-firmware-does-AVRCP theory is now ruled out, so we know mtkbt IS the processor; we just don't see what *it* does.

### Updated open question

The remaining cardinality:0 gate is somewhere inside mtkbt's userspace AVRCP/AVCTP code path. The previous walls all still apply (no root, no btsnoop, daemon-side logs gated to `__xlog_buf_printf`). Concrete next steps that *might* break the impasse:

- **Trace #1f**: Find the code that LOADS pointers from the table at vaddr `0xf94b0..0xf94bc`. The literal `0xf94b4` is stored at file_off `0x7cc0` — find the LDR that reads it, find the surrounding function, and follow upward to the caller chain. That chain is the actual op-code dispatch entry into mtkbt's AVRCP processing.
- **Trace #1g**: Scan ALL `blx rN` instructions in mtkbt where `rN` was loaded from `[rA + offset]` for some memory location, and resolve which load addresses correspond to the function-pointer tables we've identified. This builds an indirect call graph.
- **Trace #1h**: For the AV/C parser specifically — it parses cmdFrame bytes that originate from inbound AVCTP frames. Find the function that *receives* AVCTP frames (likely a state machine in the L2CAP receive path) and trace forward to where it dispatches by `cmdFrame[3]` (opcode byte). That's the AV/C demux. Even if 0x6d04a is dead code, *something* parses incoming AV/C frames.

These all extend Trace #1 — pure static analysis, no flash cycles.

## Trace #1f — Findings (executed 2026-05-02)

### `0x29e98` IS reachable — confirmed

Traced the callback registration mechanism for the field `[conn+0x5cc]` (the per-connection callback fn ptr documented in earlier analysis as being read at `0x02fd74` and `blx`'d to dispatch the AVRCP layer).

Chain found:

```
register_callback (0x2fecc):
  takes (conn_ptr, fn_ptr, sub_arg) and stores fn_ptr at [conn+0x5cc].

Caller (1 site only): 0x28a5e
  Sets up r1 (the fn_ptr argument) via PIC-style PC-relative computation:
    0x028a56:  ldr r1, [pc, #0x17c]    ; r1 = literal 0x1439  ← offset, not address
    0x028a5c:  add r1, pc               ; r1 = 0x1439 + 0x28a60 = 0x29e99
    0x028a5e:  bl 0x2fecc               ; register_callback(r0=conn, r1=0x29e99, r2=...)
```

The literal `0x1439` is **not** a function address — it's a PC-relative offset. The function address is computed at runtime by `add rN, pc`. Disassembly at the resolved target `0x29e98` matches the documented "callback dispatcher TBH" character-for-character (`push.w {...,lr}; tbh [pc, r3, lsl #1]`). So:

- `0x29e98` is reachable.
- The earlier analysis of its role is correct.
- The function `0x3096c` (E5 patch site) is also genuinely reachable — it lives in the live call chain that this dispatcher reaches via TBH.

### Why earlier traces missed this

Trace #1c looked for the wrong shape. The pattern in the binary is:

```
ldr rN, [pc, #imm]    ; load PC-rel offset literal
add rN, pc             ; compute fn_ptr = literal + PC + 4
bl <register_func>     ; pass fn_ptr as argument
```

…not the `add+str` pattern I was scanning for. Also, the literal value (e.g. `0x1439`) is a small offset, not a Thumb-LSB-set function address, so the filter `(v & 1) and v < 0xf3000` excluded it.

### Implication

The "dead code" framing has been wrong twice over: first I attributed the un-trackable references to chip firmware (refuted by inspecting the firmware blob), then to my own static-analysis blind spot (now refuted by finding the actual mechanism). The remaining "0-caller" functions in the AVCTP/AVRCP layer (`0x6d04a` AV/C parser, `0x6d25c` AVCTP register PSM, `0x6d9ba` AVCTP RX handler, `0x6cf30` AVCTP_ConnectRsp containing fn) are very likely registered through the same PIC-style mechanism via different `register_*` functions I haven't enumerated yet. They're not dead.

### Why E5 still didn't help operationally

E5's patch site (`0x309ec`: `BNE 0x30aca` → `B 0x30aca`) is inside `0x3096c`, which IS reachable. Three remaining possibilities for the lack of behavioral effect:

1. `0x3096c` is reached, but its TBH dispatch only routes specific op-codes through the branch we patched; for all other op-codes the BNE site is never reached.
2. The patch correctly forces the branch to `0x30aca`, but `0x30aca`'s downstream logic doesn't actually fire AVRCP 1.4 features for our peer state.
3. Something further upstream (the AV/C parser? the AVCTP RX handler?) is gating whether `0x3096c` ever sees a GetCapabilities op-code from our peer in the first place.

Distinguishing these requires runtime visibility we don't have. But the gate is somewhere in this code path, not in firmware or dead code.

### Suggested next step (if continuing)

Run the trace #1f mechanism (find PIC-style `ldr+add-pc; bl <reg_fn>` patterns and resolve the resulting fn pointers) against ALL register-callback-style functions in mtkbt — not just `0x2fecc`. That gives a comprehensive map of which "0-caller" functions are actually wired up, and where. From there we can compare to the call chain that processes inbound AVCTP frames and identify the true gate site for cardinality:0.

## Trace #1f (full) — Comprehensive PIC fn-ptr enumeration (executed 2026-05-02)

Scanned all 14,417 `add rN, pc` Thumb-1 sites in mtkbt's `.text` and resolved 13,825 PIC-style address constructions. **245 of those resolve to addresses that are plausible function entries** (have a `push` prologue at the resolved address).

**Classification of the 245 fn-ptr constructions by what immediately follows:**
- **63** are `bl <register_func>` — fn ptr passed as arg to a registration function
- **155** are `str rN, [rA, #imm]` — fn ptr stored directly into a struct field
- **7** are direct `blx rN` (rare, indirect tail-call)
- **20** are "other" patterns

### Findings vs our 0-caller key functions

Out of the 245 constructions, exactly **4 target our key 0-caller functions** — and **all 4 target the AVRCP callback dispatcher (`0x29e1c` / `0x29e98`)**:

| Site | Stored to / passed to | Fn ptr |
|---|---|---|
| `0x275e0` | `str r3, [r0, #0x20]` | `0x29e1d` (pre-entry) |
| `0x28352` | `str r0, [r2, #0x34]` | `0x29e1d` (pre-entry) |
| `0x28a5c` | `bl 0x2fecc` (= `register_callback(conn, fn, ...)` writing `[conn+0x5cc]`) | `0x29e99` (body) |
| `0x28dce` | `str r3, [r5, #0x44]` | `0x29e1d` (pre-entry) |

The other "0-caller" functions show **zero PIC constructions, zero R_ARM_RELATIVE, zero literal pool entries, zero direct callers**:

- `0x6d04a` AV/C parser → confirmed dead code (never reachable by any mechanism scanned).
- `0x6d25c` AVCTP register PSM, `0x6d9ba` AVCTP RX handler, `0x6cf30` AVCTP_ConnectRsp containing fn → likely also dead code (alternate implementations).
- `0x02fd34` AVRCP 1.3/1.4 init body → reached via internal `b.w` tail-call from inside the live function `0x3096c` at offset `0x030aca` (per earlier analysis). Not registered, just a sub-path within a live function.

### The three op-code=4 dispatchers

A 3-slot function-pointer table at vaddr `0xf94b0..0xf94bc` holds:

| Slot | Vaddr | Fn ptr | Function |
|---|---|---|---|
| 0 | `0xf94b0` | `0x3060c` | dispatcher A — checks `[conn+0x5d0]` against `0xa0`, `0x82`, etc. |
| 1 | `0xf94b4` | `0x30708` | dispatcher B — checks `[conn+0x5d0]` against `0x82`, `0x81`, `0x20` |
| 2 | `0xf94b8` | `0x3096c` | dispatcher C (E5 site) — checks `[conn+0x149]&0x7f` against `0x20`, `0x10` |

All three are **op-code=4 (GetCapabilities) dispatchers** for different sub-contexts. They each read different combinations of `[conn+0x149]` (version) and `[conn+0x5d0]` (state code) and dispatch differently:
- `0x3060c`: 3 reads of `[+0x149]`; cmps against `#0xa0`, `#0x82`
- `0x30708`: 2 reads of `[+0x149]`; cmps `[+0x5d0]` against `#0x82`, `#0x81`, `#0x20`
- `0x3096c`: 1 read of `[+0x149]`; **classic version-dispatch (cmp `#0x10` / `#0x20`)**

**E5 patched only the `0x3096c` branch.** If runtime selection picks `0x3060c` or `0x30708` for our peers (driven by some other state), the patch never fires.

### Why we can't proceed via static analysis alone

To know which of the three dispatchers gets invoked for our peers, we'd need to know:
1. The runtime value of `[conn+0x5d0]` (state code) when GetCapabilities arrives.
2. The runtime value of `[conn+0x149]` (version field).
3. Which slot the upstream code reads from the 3-slot table — i.e., which struct field at `+0x20`/`+0x34`/`+0x44`/`+0x5cc` is consulted.

These are runtime state. Without HCI snoop, daemon log access (xlog buffer), or device-side debugging, we can't observe them. The static call graph branches at this node and we can't predict which branch fires.

## Trace #1g (full) — Indirect-call resolution complete (2026-05-02)

### The 7 callback-invoker functions

Mapped all 14 readers of `[conn+0x5cc]` (the callback fn ptr slot holding `0x29e98`). 10 are non-PC-relative (genuine struct-field reads); they live in **7 distinct functions** that invoke the AVRCP callback dispatcher:

| Function | `[+0x5cc]` reads | Notes |
|---|---|---|
| `0x2fd36` (= AVRCP 1.3/1.4 init body) | 1 (at `0x2fd74`) | previously-documented site |
| `0x2fd84` | 1 | adjacent helper |
| `0x3060c` (op-dispatcher slot 0) | 1 (at `0x306dc`) | one of the 3-slot table dispatchers |
| `0x30708` (op-dispatcher slot 1) | 2 (at `0x308e2`, `0x3090a`) | another 3-slot dispatcher |
| `0x3096c` (op-dispatcher slot 2 = E5 site) | 1 (at `0x30b88`) | the third 3-slot dispatcher |
| `0x34e1a` | 1 | unrelated function |
| `0x34e64` | 3 | unrelated function |

**All three op-code=4 dispatchers (`0x3060c`, `0x30708`, `0x3096c`) reach the callback** — they're not mutually exclusive paths. So the question of "which one fires" is really "which one runs the path that *does* invoke the callback for this connection". Each has different gating logic before the `[+0x5cc]` read.

### Concrete patch candidate found in fn `0x3060c`

The cleanest gate site is in fn `0x3060c`:

```
0x030658:  ldrsb.w r0, [r4, #0x149]      ; SIGNED load
0x03065c:  cmp r0, #0
0x03065e:  bge #0x30688                   ; ★ if [+0x149] >= 0 (high bit clear), bypass 1.4
0x030660:  ...
0x030684:  b.w #0x2fd34                   ; tail-call AVRCP 1.3/1.4 init
```

**Single-byte patch (E8 candidate):** `0x3065e: 13 da → 00 bf` (NOP the BGE).

**Caveat:** every immediate write to `[conn+0x5d9]` (which feeds `[+0x149]`) sets the high bit (`0x90`, `0xa0`, `0xc0`, `0xd0`, ...), so for normal peers `[+0x149]` is negative as a signed byte and the BGE is NOT taken — the gate doesn't fire. The patch only matters if our peers' `[+0x149]` somehow ends up with high bit clear (uninitialized, or written via an untraced code path). We can't determine this statically.

## Trace #4 — Java decompilation of MtkBt.apk (executed 2026-05-02)

### Tooling and access

`MtkBt.dex` (extracted from MtkBt.odex at offset 0x28) contains ODEX-optimized opcodes (e.g., `invoke-virtual-quick`, `iget-quick`, `vtable@N`) that pure DEX parsers reject. Disassembly required:

```
java -jar baksmali-2.5.2.jar disassemble --allow-odex-opcodes -a 17 MtkBt.dex
```

(Plain androguard fails with `InvalidInstruction: opcode '0xf7' is unused`; baksmali with the `--allow-odex-opcodes` flag and Android 4.2 API level (17) handles them.)

### Key class structure

In `com.mediatek.bluetooth.avrcp`:
- `BluetoothAvrcpService` — top-level service. Has all `*Native()` JNI methods plus matching event handlers (`connectInd`, `connectCnf`, `activateCnf`, `registerNotificationInd`, etc.).
- `BTAvrcpMusicAdapter` — bridge to the music play service. Owns the cardinality bitset (`field@0x90` = `mRegBit`) and the EventList (`field@0x78`). Handles `registerNotification(B, I)Z` per-event.
- `BTAvrcpProfile.getPreferVersion()B` — F1 patch site, returns `0xe` after patch.
- `IBTAvrcpMusic$Stub` and `IBTAvrcpMusicCallback$Stub` — IPC interfaces to/from Y1MediaBridge.

### `BTAvrcpMusicAdapter.getSupportVersion()B`

```
getSupportVersion():
    if (sPlayServiceInterface) return 0x0e   ; AVRCP 1.4
    else                       return 0x0d   ; AVRCP 1.3
```

Confirms F2's importance: `disable()` resetting `sPlayServiceInterface = false` is required so that re-activation doesn't see stale state.

### `BTAvrcpMusicAdapter.checkCapability()V`

```
v2 = getSupportVersion()    ; v2 = 0xe (1.4) or 0xd (1.3)
if (field@0xf4 == 1):
    log "version: <v2>"     ; second-call: just log and return
    return
log "init capability version: <v2>"   ; ★ matches our logcat: "version:14"
field@0xf4 = 1                          ; mark initialized

if (v2 == 0xe):
    field@0x78 = new byte[5]            ; 1.4 EventList
else:
    field@0x78 = new byte[2]            ; 1.3 EventList

field@0x78[0] = 1   ; PLAYBACK_STATUS_CHANGED
field@0x78[1] = 2   ; TRACK_CHANGED
if (v2 == 0xe):
    field@0x78[2] = 9   ; NOW_PLAYING_CONTENT_CHANGED  (1.4)
    field@0x78[3] = 0xa ; AVAILABLE_PLAYERS_CHANGED   (1.4)
    field@0x78[4] = 0xb ; ADDRESSED_PLAYER_CHANGED    (1.4)

field@0x90 = new BitSet(16)    ; cardinality bitset (mRegBit)
field@0x90.clear()
```

Logcat confirms `init capability version:14` so the 1.4 path runs and EventList is populated correctly.

### `BTAvrcpMusicAdapter.registerNotification(B, I)Z`

This is the cardinality update site:

```
switch (eventId):
    case 1, 2, 9:    handle (delegate to BluetoothAvrcpService notification* method) → bReg = true
    case 3, 4, 5, 8: log "[BT][AVRCP] MusicAdapter blocks support register event:%d", bReg = false
    case 6, 7:       delegate to BluetoothProfileManager (vtable@15)
    case 10, 11, 12: fall through, bReg unchanged (= false)
    case 13:         log "blocks", bReg = false

if (bReg):
    synchronized (field@0x90):
        field@0x90.set(eventId)              ; ★ THE cardinality update
        log "[BT][AVRCP] mRegBit set %d Reg:%b cardinality:%d"
return bReg
```

### `BluetoothAvrcpService.registerNotificationInd(B, I)V`

Calls `BTAvrcpMusicAdapter.registerNotification(eventId, interval)` (via `field@0x24` = music adapter, vtable@75) for any eventId not in the special set `{0xa, 0xb, 0xc}`. Logs `[BT][AVRCP](test1) registerNotificationInd eventId:%d interval:%d` on entry.

### Definitive verdict

The user's logcat over multiple sessions shows:
- **Neither** `[BT][AVRCP](test1) registerNotificationInd eventId:%d` (the registration entry log)
- **Nor** `[BT][AVRCP] mRegBit set %d Reg:%b cardinality:%d` (the cardinality update log)
- **Nor** `[BT][AVRCP] MusicAdapter blocks support register event:%d` (the rejection log)

Therefore `registerNotificationInd` **never fires** — i.e., the JNI never receives a "REGISTER_NOTIFICATION arrived" event from mtkbt. Combined with our prior observation that no inbound AVRCP `Recv AVRCP indication` msg_ids beyond 501/505/506/512 are seen, this **definitively locates the cardinality:0 gate inside `mtkbt`'s native AVRCP layer**, between the AVCTP receive path and the JNI dispatch socket.

### Java layer ruled out

The Java layer:
- Initializes correctly (1.4 EventList ready).
- Handles incoming subscriptions correctly (events 1/2/9 succeed; 3/4/5/8/13 explicitly blocked; the others no-op).
- Has no version gate or capability check that would suppress events when they DO arrive.

No additional Java/smali patches will help. F1 + F2 are necessary AND sufficient on the Java side. The gate is unambiguously below.

### The honest end of the static investigation

After Trace #1f the architectural picture is finally complete and consistent:

- **`mtkbt` IS the AVRCP processor** (not chip firmware). ✓ confirmed by inspecting firmware blob.
- **The documented dispatchers (`0x29e98`, `0x02fd34`, `0x3096c`) are all reachable at runtime** — via PIC-style callback registration that earlier traces missed. ✓ confirmed.
- **`0x6d04a` "AV/C parser" is dead code** — multiple independent searches confirm no caller mechanism reaches it. ✓ confirmed.
- **The cardinality:0 gate is in the runtime decision tree of `[conn+0x5d0]` × `[conn+0x149]` × dispatcher-table selection**, somewhere in the `0x29e98` → `0x3060c`/`0x30708`/`0x3096c` family of paths.
- **Static analysis cannot determine which decision point fires for our peers without observing runtime values.** Every structural and addressable element has been mapped.

The remaining diagnostic options (HCI snoop / chip firmware modification / runtime instrumentation patches that emit observable side effects) are all out of scope per the constraints established at session start.

The repo (B1-B3, C1-C3, A1, D1, E3, E4, plus C2a/b, C3a/b, C4, F1, F2 across the four binaries) represents the complete set of demonstrably-effective patches reachable through static analysis. Y1MediaBridge is correctly implemented and ready to fire the moment the runtime gate releases.

## Trace #7 — Findings (2026-05-03): MT6572 BT lib stack is HCI-only

The four `libbluetooth*` shared objects in `/system/lib` were inspected end-to-end (sizes, dynsyms, full `strings`):

| Library | Size | MD5 | Role |
|---|---:|---|---|
| `libbluetoothdrv.so` | 9,280 | `32f1af87e46acaf1efa3f083340495cb` | Thin shim. Exports `mtk_bt_enable/disable/write/read/op` plus 8 fn-ptr objects in `.bss`. `mtk_bt_enable` does `dlopen("libbluetooth_mtk.so")` + dlsym on `bt_send_data`, `bt_receive_data`, `bt_read_nvram`, `bt_get_combo_id`, `bt_restore`, `read_comm_port`, `write_comm_port`. `mtk_bt_op` handles two opcodes only: `BT_COLD_OP_GET_ADDR` and `BT_HOT_OP_SET_FWASSERT`. |
| `libbluetooth_mtk.so` | 13,452 | — | Real driver. Exports `BT_InitDevice`, `BT_DeinitDevice`, `BT_SendHciCommand`, `BT_ReadExpectedEvent`, `GORM_Init`, `bt_send/receive_data`, `bt_read_nvram`, `bt_get_combo_id`, `bt_restore`, `read/write_comm_port`. Strings reveal it as UART transport + GORM/HCC chip-bringup commands (`Set_Local_BD_Addr`, `Set_Sleep_Timeout`, `Set_TX_Power_Offset`, `RESET`, `Set_Radio`) + NVRAM BD-address management + chip combo-id detection. Contains `bt_init_script_6572`. |
| `libbluetoothem_mtk.so` | 5,156 | — | Engineer Mode test surface (`EM_BT_read/write/init/deinit`). |
| `libbluetooth_relayer.so` | 9,252 | — | EM↔BT relayer (`bt_rx_monitor`, `bt_tx_monitor`, `RELAYER_start/exit`). |

**Combined `strings` search across all four libraries returned ZERO hits** for `avrcp`, `avctp`, `profile`, `capability`, `notif`, `metadata`, `cardinal`. They are exclusively HCI/transport — UART connection to the MT6627 chip, BD-address management from NVRAM, and chip-bringup HCC commands. Nothing above HCI.

**Implication.** The cardinality:0 gate cannot live in any userland library other than `mtkbt`. `mtkbt` does not call back through any of these libraries for AVCTP/AVRCP processing — it uses them only for HCI transport via `bt_send_data`/`bt_receive_data`. AVCTP framing, L2CAP demux, and AVRCP command dispatch all happen inside `mtkbt`'s own code segment. This narrows the search space conclusively to `mtkbt`.

This trace was deferred during 2026-05-02 work as low-priority ("almost certainly a thin shim"). Confirmed 2026-05-03; the deferral was correct but the verification was cheap and worth doing before considering root.

## Out of Scope (eliminated)

- HCI snoop / btsnoop — no root, eliminated in earlier passes.
- mtkbt instrumentation patches (insert log calls at choke points) — possible but very high effort, low marginal value over #1 + #2.
- boot.img init scripts — won't reveal anything about the AVRCP path.

---

# Conclusion (2026-05-04) — byte-patch path exhausted, proxy work needed

After the original investigation in this document concluded the gate was upstream of the op_code=4 dispatcher, post-root work in May 2026 added the diagnostic infrastructure to actually see what mtkbt and peers were doing on the wire (`@btlog` tap, `dual-capture`, `btlog-parse`, `probe-postroot` — all in `tools/` and `src/btlog-dump/`) and ran a series of byte-patch experiments to test increasingly informed hypotheses about the SDP-record shape required by working AVRCP CTs.

**The byte-patch hypothesis is conclusively dead.** Five distinct (version, features) combinations were tested:

| Configuration | SDP wire | AVCTP RX behaviour | Cardinality | PASSTHROUGH play/pause |
|---|---|---|---|---|
| Stock 1.0 + features `0x01` | `09 01 00 09 00 01` | Sonos doesn't bother sending AVRCP COMMANDs at all (no AVCTP_EVENT:4) | 0 | **WORKS** |
| `--avrcp` standard 1.4 + features `0x33` | `09 01 04 09 00 33` | Sonos sends one COMMAND, mtkbt drops silently, Sonos gives up | 0 | **broken** |
| Pixel-shape 1.5 + features `0xd1` (Browsing+MultiPlayer) | `09 01 05 09 00 d1` | Sonos tries to open AVCTP browse PSM `0x1B`, mtkbt has no listener (`+@l2cap: cannot find psm:0x1b!`), Sonos gives up | 0 | broken |
| Pixel-1.3 mimic 1.3 + features `0x01` | `09 01 03 09 00 01` | Same dropped-COMMAND failure as 1.4 | 0 | broken |
| Features-only at 1.4 + features `0x01` | `09 01 04 09 00 01` | Same | 0 | broken |

**Reference: Pixel 4 ↔ Sonos works at every AVRCP version 1.3-1.6** (per user-supplied `sdptool browse F0:5C:77:E4:30:62` outputs at each Developer-Options-forced version, captured 2026-05-04). The Pixel at 1.3 advertises features `0x0001` — *the same value Y1 stock advertises* — and Sonos receives full title/artist/album metadata + responds correctly to `PASS_THROUGH` play/pause. The difference is not the SDP advertisement. It is mtkbt's command-handling layer.

**mtkbt is internally an AVRCP 1.0 implementation.** Compile-time string `[AVRCP] AVRCP V10 compiled` + runtime log `AVRCP register activeVersion:10` are accurate. The opcode dispatchers identified earlier in this document (`0x3060c`, `0x30708`, `0x3096c` at op_code=4 = `GetCapabilities`) exist in the binary, but no inbound packet from any peer ever reaches them, regardless of how we shape the SDP record. The earlier "the gate is upstream of the dispatcher table" framing was correct; the missing piece was that there is no upstream gate that byte-patches can flip — mtkbt's AVCTP RX simply does not classify AVRCP COMMAND PDUs as anything its 1.0 dispatcher recognises, and silently drops them.

The previously-listed primary lead, `MSG_ID_BT_AVRCP_CONNECT_CNF result:4096`, was also disproven during this work: the same `0x1000` value is emitted at `MSG_ID_BT_AVRCP_ACTIVATE_CNF` time 3 ms after the JNI sends `ACTIVATE_REQ`, before any peer is involved. `0x1000` is mtkbt's standard "request acknowledged" status code, not a peer-feedback or "feature degraded" indicator.

## Repo state after the conclusion (commits 2690d05 → 7077b5a → bd36160 → this one)

- `--avrcp` is now a known-broken opt-in. It runs if explicitly requested (useful for the proxy work below) and prints a startup warning. **Excluded from `--all`.**
- `--bluetooth` no longer sets `persist.bluetooth.avrcpversion=avrcp14`. The remaining audio.conf / `auto_pairing.conf` / `blacklist.conf` / `ro.bluetooth.class` / `ro.bluetooth.profiles.*.enabled` properties are pairing-essential and stay.
- The recommended baseline is `--all` (without `--avrcp`): pairing works, A2DP audio works, AVRCP 1.0 PASSTHROUGH (play/pause/skip) works, no metadata over BT.
- Diagnostic infrastructure remains in-tree: `src/btlog-dump/` (no-libc ARM ELF that taps mtkbt's `@btlog` socket), `tools/btlog-parse.py` (frame decoder), `tools/dual-capture.sh` (btlog + logcat correlated capture), `tools/probe-postroot.sh` + `tools/probe-postroot-device.sh` (one-shot post-root sanity probe).
- Failed-experiment scripts (Browsing-bit, Pixel-shape, Pixel-1.3 mimic, features-only) have been removed from `tools/`. Their results are summarised in the table above and in `CHANGELOG.md`.

## Path forward — user-space AVRCP proxy

Three architecture sketches were considered when the byte-patch path was first ruled out (see commit messages around `bd36160`). The smallest viable one is sketched below.

**Approach: trampoline mtkbt's silent-drop site to forward unhandled AVRCP COMMANDs raw to the JNI; respond from Java.**

The work is roughly four phases.

### Phase 1 — Identify the silent-drop site (gdbserver, ~1-2 days)

Push an API-17 ARM AOSP-prebuilt `gdbserver` to `/data/local/tmp/`, attach to the live `mtkbt` PID. PIE base is `0x400c1000` (per `tools/probe-postroot.sh` §1; verify on each session — the base is per-process not per-firmware). Set breakpoints on the candidate drop sites identified in the appendix below:

- `0x6d9ba` (live `0x40128d9a`) — AVCTP RX handler
- `0x6cf30` (live `0x40128f30`) — AVCTP_ConnectRsp
- `0x0513a4` (live `0x401123a4`) — `[AVRCP][WRN] AVRCP receive too many data. Throw it!` log site
- `0x29e98` (live `0x400d2e98`) — TBH callback dispatcher

Trigger the failure scenario (Y1 ↔ Sonos with `--avrcp` on so peer engages enough to send a COMMAND). Whichever breakpoint fires when the single `AVCTP_EVENT:4` arrives is the candidate drop site. Dump the inbound packet bytes from r0/r1/stack at that point and confirm they're a real AVRCP COMMAND PDU (op_code 0x4 = GetCapabilities is the most likely first command).

`tools/probe-postroot.sh` §11 confirmed SELinux is absent on this firmware and §12 confirmed `/proc/sys/kernel/yama/ptrace_scope` doesn't exist either, so ptrace attach is unblocked.

### Phase 2 — Patch a trampoline (~3-5 days)

At the identified drop site, replace the silent-drop branch with a `bl <trampoline>`. The trampoline (in a code-cave or appended to mtkbt's `.text`) marshals the inbound packet into a new IPC message — e.g., msg_id 999 — and writes it to the existing `bt.ext.adp.avrcp` abstract socket that already carries msg_ids JNI↔mtkbt. The IPC framing and the existing send wrapper at vaddr `0x511c0` are documented in the appendix below.

Verification: `tools/btlog-parse.py` should now show the AVRCP COMMAND bytes flowing through the new msg_id; logcat should show the JNI receiving msg_id 999 (or whatever ID we pick).

### Phase 3 — Java AVRCP COMMAND parser/responder (~1-2 weeks)

Extend `Y1MediaBridge` (or add a sibling Java component) to:
1. Receive the new msg_id from the JNI via the existing Binder path.
2. Parse the AVCTP+AVRCP frame: AV/C control header, op_code, PDU ID, transId, params.
3. Build the appropriate AVRCP RSP for at minimum:
   - `GetCapabilities` (PDU `0x10`)
   - `RegisterNotification` (PDU `0x31`) for `EVENT_TRACK_CHANGED` (`0x05`) and `EVENT_PLAYBACK_STATUS_CHANGED` (`0x01`)
   - `GetPlayStatus` (PDU `0x30`)
   - `GetElementAttributes` (PDU `0x20`)
4. Use the existing `IBTAvrcpMusic`/`IBTAvrcpMusicCallback` plumbing for the actual track/state data — Y1MediaBridge already sources this from the music player via broadcast intents and RCC.

`PASS_THROUGH` (op_code `0x7C`) commands should pass through to the existing 1.0 path so play/pause keeps working — don't intercept those.

### Phase 4 — Outbound RSP path (~3-5 days)

Patch a second trampoline (or extend the first) that takes a Java-built AVRCP RSP frame, marshals it into an outbound msg_id, and routes it through mtkbt's existing AVCTP TX path so it reaches the peer's AVCTP channel. The IPC dispatcher map in the appendix below (msg_ids 500-611, second TBH at vaddr `0x518ac`) names the candidate slots.

### Verification target

`tools/dual-capture.sh` against Sonos should show:
- `cardinality:N` non-zero in `MMI_AVRCP: ACTION_REG_NOTIFY for notifyChange ... cardinality:N`
- `MMI_AVRCP: registerNotificationInd eventId:` firing for events 1/2/9
- Sonos app showing title/artist/album for the currently-playing track
- Y1MediaBridge log lines `notifyAvrcpCallbacks code=N targets=>=1` (currently always logs `targets=0` because MtkBt never registers a callback — the proxy work fixes this by routing peer-side `RegisterNotification` through Java)
- Physical play/pause from Sonos still working (PASSTHROUGH path unbroken)

### Known prerequisites for the next agent

- Read this entire document top-down — the failure modes earlier in the doc (G1/G2 SIGSEGV at NULL, blanket xlog redirect being too fragile, etc.) are real and re-tripping them wastes days.
- Re-verify `tools/probe-postroot.sh` outputs against the device before assuming PIE base / PSM list / SELinux state. The probe is idempotent and cheap.
- The diagnostic tooling (`@btlog` tap, dual-capture, parser) was developed against firmware 3.0.2. If `KNOWN_FIRMWARES` gains a new entry, re-verify the framing against that firmware before trusting parsed output.
- `--avrcp` MUST be enabled to test the proxy work (otherwise the Y1MediaBridge bridge isn't installed and there's no Java endpoint for the proxy to deliver to). The startup warning is informational; ignore it for the duration of the proxy work.

### Estimated total

2-4 weeks of focused work for someone with ARM Thumb-2 binary RE + Android Bluetooth experience. The diagnostic infrastructure is in place; the gating risk is finding a viable drop site in mtkbt that we can hook without destabilising AVCTP. If no clean site exists (e.g., the drop happens inline rather than at a callable choke point), the alternative is the larger Option 2 — disable mtkbt's AVRCP entirely and bind PSM 0x17 from Java — which is a multi-month rewrite.

---

# Appendix — Reference detail (originally maintained as the working-notes brief, archived 2026-05-04)

This appendix preserves the granular detail from a working-notes brief that was maintained externally to the repo during the 2026-05-02 → 2026-05-04 investigation. The narrative above (top of doc) is the canonical history; the conclusion above is the canonical end-state. **This appendix is reference data**: byte-level patch tables, MD5s, function offsets, ILM layouts, msg_id maps, log tag conventions, and the post-root traces (#8–#11) that complement the original Traces #1–#7. Future work should consult both halves of this document. The brief itself is no longer maintained.

## Device Context

| Item | Value |
|---|---|
| SoC | MT6572 |
| Android | 4.2.2 (JDQ39) |
| Bluetooth | 4.2 (host stack) |
| Stock player | Proprietary Innioasis app — logcat prefix `DebugY1` |
| BT stack | `MtkBt.apk` → `libextavrcp_jni.so` → `libextavrcp.so` → `mtkbt` daemon via Unix socket |
| BT chip | MT6627 (combo: BT + Wi-Fi + FM + GPS), HCI-only — chip firmware is the WMT common subsystem and contains zero AVRCP code |
| System access | Full system-partition write via MTKClient + loop-mount. Flash cycle 5–10 min. |
| ADB root | **Hardware-verified 2026-05-04 via setuid `/system/xbin/su`** (v1.8.0+). Stock `/sbin/adbd` untouched. |

## Architecture

```
[Car Stereo CT] <--SDP/AVRCP--> [mtkbt daemon] <--socket bt.ext.adp.avrcp--> [libextavrcp.so]
                                       |                                           ^
                                       | HCI / UART                     [libextavrcp_jni.so]
                                       v                                           ^
                                  [/dev/stpbt]                            [MtkBt.apk Java layer]
                                       |
                                       v
                              [mtk_stp_bt.ko (kernel)]
                                       |
                                       v
                                [MT6627 chip — HCI/radio only]
```

The socket `bt.ext.adp.avrcp` lives in `ANDROID_SOCKET_NAMESPACE_ABSTRACT` (namespace=0). Abstract sockets have no filesystem file and are auto-released on FD close; no stale socket is possible across BT toggle cycles.

**Trace #7 confirmed** all four `libbluetooth*.so` libs (`libbluetoothdrv.so`, `libbluetooth_mtk.so`, `libbluetoothem_mtk.so`, `libbluetooth_relayer.so`) are HCI/transport-only. Combined `strings` search returned zero hits for `avrcp/avctp/profile/capability/notif/metadata/cardinal`. The cardinality:0 gate cannot live anywhere except inside `mtkbt`.

Additionally, mtkbt exposes an undocumented `SOCK_STREAM` listener at the abstract socket `@btlog` (created by `socket_local_server("btlog", ABSTRACT, SOCK_STREAM)` at vaddr `0x6b4d4`). Connecting to it as root yields a stream of mtkbt's `__xlog_buf_printf` output **plus** decoded HCI command/event traffic — the diagnostic capability used by `tools/dual-capture.sh` and Trace #9. See `src/btlog-dump/README.md` for the framing format.

## The mtkbt Fix — Eleven Patches (the `--avrcp` patch set; now known broken end-to-end per the conclusion above)

The patches below ship under the `--avrcp` flag of `apply.bash`. All eleven land on the wire (`sdptool browse` confirms AVRCP 1.4 + AVCTP 1.3 + SupportedFeatures 0x0033) and the Java layer initialises correctly for AVRCP 1.4. Despite that, the patch set as a whole is a **net regression** vs. stock 1.0 — it claims a version mtkbt's command-handling layer cannot deliver, and peers (Sonos, car) refuse to engage AVRCP COMMANDs as a result. See "Conclusion (2026-05-04)" above. The detail below is preserved because individual patches are still load-bearing if/when the user-space proxy work activates AVRCP 1.3+ command handling — the SDP record needs to be there.

### Descriptor Table Structure

The mtkbt descriptor table at file offset `0x0f9774` has **three** service record groups, each a contiguous run of 5–6 entries (`attrID LE16`, `len LE16`, `ptr LE32`, `zeros LE32`):

| Group | Role | ProtocolDescList ptr | AdditionalProtocol ptr | ProfileDescList ptr | SupportedFeatures (stock) |
|---|---|---|---|---|---|
| 1 | TG (record A) | `0x0eba5c` (shared) | `0x0eba12` | `0x0eba6e` | `0x0021` (Cat1+GroupNav) |
| 2 | TG (record B, **last wins**) | `0x0eba5c` (shared) | — | `0x0eba4f` | `0x0001` (Cat1 only) |
| 3 | CT | `0x0eba26` | — | `0x0eba42` | `0x000f` (Cat1-4) |

`AttrID=0x0311` (SupportedFeatures) **IS** registered in all three groups with non-zero values.

### B1-B3 — AVCTP Version

Stock mtkbt advertises AVCTP 1.0 in all three AVCTP-bearing SDP blobs. AVRCP 1.4 requires AVCTP 1.3.

| Patch | Offset | Blob | Before | After | Effect |
|---|---|---|---|---|---|
| **B1** | `0x0eba6d` | Groups 1&2 TG ProtocolDescList | `0x00` | `0x03` | AVCTP 1.0 → 1.3 (TG control channel) |
| **B2** | `0x0eba37` | Group 3 CT ProtocolDescList | `0x00` | `0x03` | AVCTP 1.0 → 1.3 (CT record) |
| **B3** | `0x0eba25` | Group 1 AdditionalProtocol | `0x00` | `0x03` | AVCTP 1.0 → 1.3 (browsing channel descriptor) |

### C1-C3 — AVRCP Profile Version

Last-wins SDP semantics across all three ProfileDescList entries; all three patched to 1.4.

| Patch | Offset | Record | Before | After | Effect |
|---|---|---|---|---|---|
| **C1** | `0x0eba4b` | [23] ProfileDescList | `0x00` | `0x04` | AVRCP 1.0 → 1.4 |
| **C2** | `0x0eba58` | [18] ProfileDescList (served) | `0x00` | `0x04` | AVRCP 1.0 → 1.4 |
| **C3** | `0x0eba77` | [13] ProfileDescList | `0x03` | `0x04` | AVRCP 1.3 → 1.4 |

### A1 — Runtime SDP MOVW (belt-and-suspenders)

```asm
0x38BFC:  40 f2 01 37   MOVW r7, #0x0301      ; byte-swapped 1.3
0x38C02:  a3 f8 48 70   STRH.W r7, [r3, #72]  ; writes {01 03} to runtime struct
```

| Offset | Before | After | Effect |
|---|---|---|---|
| `0x38BFC` | `40 f2 01 37` | `40 f2 01 47` | MOVW r7: #0x0301 → #0x0401 |

### D1 — Runtime Registration Guard NOP at `0x38C6C`

The SDP init function (`0x38AB0`–`0x38C74`) builds the AVRCP TG SDP struct in r3, then gates the final registration step behind `CMP r0, r5` / `BNE 0x38C76` where r5 = `0x111F`. r0 is never `0x111F`, so the three writes that complete registration are always skipped. NOP'ing the BNE links the constructed SDP struct into mtkbt's live registry.

| Offset | Before | After | Effect |
|---|---|---|---|
| `0x38C6C` | `03 d1` | `00 bf` | `BNE 0x38C76 → NOP` — always fall through to registration writes |

### E3/E4 — TG SupportedFeatures

Wire-confirmed via `sdptool browse` after D1 was live: `AttrID=0x0311` IS served inside the AVRCP TG record (UUID 0x110c), but the served value is `0x0001` (Cat1 only — Group 2 wins the merge). 1.4 controllers see ProfileVersion=1.4 with a feature bitmask consistent with 1.0 (or 1.3). E3/E4 raise it to `0x0033`.

| Patch | Offset | Group | Before | After | Notes |
|---|---|---|---|---|---|
| **E3** | `0x0eba5b` | Group 2 TG (served) | `0x01` | `0x33` | `0x0001 → 0x0033` — Cat1 + Cat2 + PAS + GroupNav |
| **E4** | `0x0eba4e` | Group 1 TG (defense-in-depth) | `0x21` | `0x33` | `0x0021 → 0x0033` |

The Browsing bit (6) was deliberately omitted at brief-writing time because `AdditionalProtocolDescriptorList` (0x000d) is in Group 1 only and isn't on the wire after the merge — claiming Browsing without serving the descriptor would re-introduce the same inconsistency. The Browsing-bit experiment (Trace #11 Thread A) and the Pixel-shape experiment (which set features `0xd1` including Browsing + Multi-Player) confirmed this concern: Sonos engages browse, mtkbt rejects PSM `0x1B` (`+@l2cap: cannot find psm:0x1b!`), Sonos gives up on AVRCP altogether.

### E8 — op_code=4 dispatcher slot-0 sign gate

```asm
0x030658:  ldrsb.w r0, [r4, #0x149]     ; SIGNED load
0x03065c:  cmp r0, #0
0x03065e:  bge #0x30688                  ; ★ if [+0x149] >= 0 (high bit clear), bypass 1.4 init
0x030684:  b.w #0x2fd34                  ; tail-call AVRCP 1.3/1.4 init
```

| Offset | Before | After | Effect |
|---|---|---|---|
| `0x3065e` | `13 da` | `00 bf` | `BGE 0x30688 → NOP` — force every classification through the 1.3/1.4 init path |

**Tested 2026-05-02 and observed inert** — cardinality:0 persists, no `op_code=4` GetCapabilities messages reach the dispatchers. The gate is upstream of the dispatcher table entirely (and ultimately, per the 2026-05-04 conclusion, in mtkbt's compiled-1.0 command handling rather than at the dispatcher table). Kept as a verified-correct probe.

### Patches that have been tried and removed/reverted

| Patch | Site | Outcome | Status |
|---|---|---|---|
| **E1** `0x29be4` `BNE.W → NOP` | Inside `0x299fc` (REGISTER_NOTIFICATION dispatcher) | State gate is **legitimate** — only fires when state ∉ {3,5}. State=3 is set by an *incoming* REGISTER_NOTIFICATION, so no response should be sent without one. Bypass caused unsolicited responses → car cycle-1 disconnect. | **Reverted 2026-05-01** |
| **E2** `0x0309ec` `BNE → NOP` | Inside `0x3096c` op_code=4 dispatcher | Branch routes 1.3/1.4 cars to the *correct* count=4 path (`0x02fd34` → 5-slot init + AVAILABLE_PLAYERS). NOPing it bypassed mandatory init. | **Reverted 2026-05-01** |
| **E5** `0x309ed` `BNE → B` | Force 1.3/1.4 init in op_code=4 dispatcher slot 2 | Empirically inert across all three peers — likely never reached at runtime for our peer state. Initial "dead code" reasoning was wrong (Trace #1f proved the function IS reachable via PIC-style callback registration), but the *operational call to remove* was correct. | **Removed 2026-05-02** |
| **E7a/E7b** `0x033dec`/`0x034100` `0x90 → 0x94` | No-SDP-CT fallback bytes | Empirically inert (Sonos *does* advertise CT 0x110e, so the fallback path doesn't fire for our peers). | **Removed 2026-05-02** |
| **G1** `0x675c0` 12-byte Thumb thunk | xlog→logcat redirect (no NULL guard) | mtkbt SIGSEGV at addr 0 immediately at startup — at least one xlog callsite passes NULL in r2; bionic's `__android_log_print` at API 17 doesn't NULL-check the tag. | **Reverted 2026-05-02** |
| **G2** `0xb408` ARM PLT thunk | Same redirect, ARM-mode entry path | Same SIGSEGV. | **Reverted 2026-05-02** |
| **G1 attempt 2** `0x675c0` 20-byte Thumb thunk with `cbz r2, .L_null` guard | xlog→logcat redirect (NULL guard) | NULL guard helped but BT framework still couldn't enable: `bt_sendmsg(cmd=100, ...)` returned ENOENT — mtkbt's abstract socket never came up. Either crashed on a non-NULL but invalid pointer, or redirected log volume flooded logd past framework's init timeout. | **Reverted 2026-05-03** |
| **Browsing bit / Pixel-shape / Pixel-1.3 / features-only** experiments | various E3/E4/B/C/A1/F1 byte tweaks | All disproven 2026-05-04 — see "Conclusion (2026-05-04)" test matrix above. Tooling deleted. | **Removed 2026-05-04** |

**Conclusion (G1/G2):** blanket `__xlog_buf_printf → __android_log_print` redirect at the consolidated wrapper at `0x675c0` is too fragile. The wrapper is hit ~3000 times across mtkbt's lifecycle including very early init. Future diagnostic instrumentation must be surgical — explicit `bl __android_log_print` calls at a small number of high-value sites with hardcoded tag/fmt strings via a trampoline. (The `@btlog` passive tap from Trace #9 supersedes this need entirely for read-only observation; instrumentation is only needed to *change* mtkbt's behaviour, e.g. for the Phase-2 trampoline of the user-space proxy work.)

## adbd Root Patches (H1/H2/H3) — Closed 2026-05-03 (failed on hardware), superseded by setuid `/system/xbin/su`

> **Status: closed.** Both attempted revisions caused "device offline" on hardware. `--root` flag removed from `apply.bash` in v1.7.0 then reintroduced in v1.8.0 against `/system/xbin/su` instead. The standalone `patch_adbd.py` and `patch_bootimg.py` scripts (kept in the tree until v2.0.0) were removed in v2.1.0; the analysis below is preserved for whoever picks up the root pass with a different mechanism.

The OEM adbd has stripped the standard AOSP `should_drop_privileges()` gating. `strings adbd` returns ZERO references to `ro.secure`. The drop_privileges block at vaddr `0x94b8` runs unconditionally on every adbd startup.

```asm
0x94b8:  movs   r0, #0xb           ; arg0 = count = 11               ← H1
0x94ba:  add    r1, sp, #0x24      ; arg1 = gid_array on stack
0x94bc:  blx    #0x17038           ; setgroups(11, gids)
0x94c0:  cmp    r0, #0
0x94c2:  bne.w  #0x97ea            ; on failure → exit(1)
0x94c6:  mov.w  r0, #0x7d0         ; arg0 = AID_SHELL = 2000          ← H2
0x94ca:  blx    #0x1701c           ; setgid(2000)
0x94ce:  cmp    r0, #0
0x94d0:  bne.w  #0x97ea
0x94d4:  mov.w  r0, #0x7d0         ; arg0 = AID_SHELL = 2000          ← H3
0x94d8:  blx    #0x19418           ; setuid(2000) wrapper → bl 0x27b30; eventually mov r7,#0xd5; svc 0
0x94dc:  mov    r3, r0
0x94de:  cmp    r0, #0
0x94e0:  bne.w  #0x97ea
```

**Final approach (arg-zero, 2026-05-03 revision):** change only the *argument loads* so the syscalls execute with arguments of 0. All bionic bookkeeping (capability bounding-set, thread-credential sync) runs normally; the process ends up at uid=0/gid=0 with no supplementary groups.

| Patch | File offset | Before | After | Effect |
|---|---|---|---|---|
| **H1** | `0x14b8` | `0b 20` | `00 20` | `movs r0, #0xb` → `movs r0, #0` (setgroups count 11 → 0) |
| **H2** | `0x14c6` | `4f f4 fa 60` | `4f f0 00 00` | `mov.w r0, #0x7d0` → `mov.w r0, #0` (setgid arg 2000 → 0) |
| **H3** | `0x14d4` | `4f f4 fa 60` | `4f f0 00 00` | `mov.w r0, #0x7d0` → `mov.w r0, #0` (setuid arg 2000 → 0) |

| Item | Value |
|---|---|
| Stock adbd MD5 | `9e7091f1699f89dc905dee3d9d5b23d8` (223,132 bytes) |
| Patched adbd MD5 (arg-zero) | `9eeb6b3bef1bef19b132936cc3b0b230` (same size) |
| Patched adbd MD5 (NOP-the-blx, earlier failed revision) | `ccebb66b25200f7e154ec23eb79ea9b4` |

Confirmed `blx` targets:
- `0x17038` → ARM-mode `mov r7, #0xce ; svc 0` (setgroups32 EABI #206)
- `0x1701c` → ARM-mode `mov r7, #0xd6 ; svc 0` (setgid32 EABI #214)
- `0x19418` → ARM wrapper that does `bl 0x27b30` *before* reaching `mov r7, #0xd5 ; svc 0` at `0x31a70` (setuid32 EABI #213) — the `bl 0x27b30` is the load-bearing bookkeeping (likely capability bounding-set / thread-credential sync) that the original NOP-the-blx revision skipped.

**Why default.prop edits alone don't work.** Empirical confirmation 2026-05-03: `adb shell id` returned `uid=2000(shell)` despite `ro.secure=0`/`ro.debuggable=1`/`ro.adb.secure=0` correctly set per `getprop`. `adb root` is also actively harmful on the un-patched binary — adbd accepts the request (ro.debuggable=1 passes the permission check), sets `service.adb.root=1`, exits to be respawned, hits the same unconditional drop_privileges path again, and the self-restart triggers a USB rebind that the stock MTK adbd handles poorly (host loses the device until reboot).

**Why arg-zero, not NOP-the-blx (history).** An earlier revision NOPed the three `blx` calls outright. **On hardware**, however, `adb shell` and `adb root` both returned "device offline" — adbd starts and the USB endpoint enumerates, but the ADB protocol handshake never completes. The bionic setuid wrapper at `0x19418` does `bl 0x27b30` *before* reaching the actual syscall stub, doing capability bounding-set / thread-credential bookkeeping that downstream adbd code depends on. NOPing the call entirely skips that bookkeeping → process is uid 0 nominally but has inconsistent credentials/capabilities → the USB ADB protocol layer never fully initializes. The arg-zero revision keeps every syscall and every bionic wrapper intact; `setuid(0)` when EUID is already 0 is a no-op that runs all the same bookkeeping. Same for `setgid(0)`. `setgroups(0, _)` clears supplementary groups, which is the desired end state anyway. **Even so, arg-zero ALSO failed on hardware** ("device offline"); root cause never fully diagnosed because losing ADB makes diagnosis circular.

`patch_bootimg.py` extracted `/sbin/adbd` from the boot.img ramdisk cpio in-place, applied H1/H2/H3 via `patch_adbd.patch_bytes()`, and wrote it back. Same file size (223,132 bytes) so cpio record offsets are unchanged.

## Root via setuid `/system/xbin/su` — v1.8.0 (verified on hardware 2026-05-04)

> **Status: hardware-verified 2026-05-04.** First flash + `adb shell` → `su` → `id` returned `uid=0(root) gid=0(root)`. Replaces the failed H1/H2/H3 adbd byte-patch path; got us out of the "patched adbd is broken / can't even diagnose because we just broke ADB" trap.
>
> Verification log:
>
> ```
> $ adb devices
> List of devices attached
> 0123456789ABCDEF	device
>
> $ adb shell
> shell@android:/ $ id
> uid=2000(shell) gid=2000(shell) groups=1003(graphics),1004(input),...
> shell@android:/ $ su
> shell@android:/ # id
> uid=0(root) gid=0(root) groups=1003(graphics),1004(input),...
> ```
>
> `su` resolved without explicit path → `/system/xbin/su` is on `$PATH`. Prompt flipped `$`→`#`. No password prompt, no manager APK gating. The 892-byte direct-syscall escalator works exactly as designed.

### Strategy

Sidestep adbd entirely. Stock `/sbin/adbd` is left untouched and continues to drop privileges to uid 2000 (shell) at boot — ADB protocol handshake comes up cleanly, identical to stock behavior. Root is then obtained per-session by exec'ing a setuid-root binary at `/system/xbin/su`.

The binary is built from `src/su/su.c` (~80 lines of C) + `src/su/start.S` (~10 lines of ARM Thumb-2 assembly), entirely in-tree:

- **No libc dependency** — direct ARM-EABI syscall implementation. `setgid(0)` → `setuid(0)` → `execve("/system/bin/sh", …)`. Three invocation forms: bare `su` (interactive root shell), `su -c "<cmd>"` (one-off), `su <prog> [args…]` (exec-passthrough).
- **No supply chain beyond GCC + this source.** No SuperSU/Magisk/phh-style binary imported, no manager APK, no whitelist.
- **Build via `cd src/su && make`.** Output: 892-byte statically-linked ARMv7 ELF, soft-float, EABI v5, no `NEEDED` entries. Output MD5 (current): `a87dc616085e1a0e905692a628e747e7`.

The bash patcher's `--root` flag does:

```
sudo install -m 06755 -o root -g root src/su/build/su /mnt/y1-devel/xbin/su
```

against the mounted system.img. No boot.img extraction, no ramdisk repack, no `/sbin/adbd` byte-patches.

### Trade-offs

- **Anyone who can exec `/system/xbin/su` becomes root.** No permission-prompt UI, no whitelist. Acceptable for a single-user research device. Not appropriate for a consumer ROM.
- The binary is intentionally tiny + direct so every byte is auditable. Statically linked means a future bionic mismatch can't brick the escalator.

### Why this should work where H1/H2/H3 didn't

The H1/H2/H3 failure mode was: patched `/sbin/adbd` got into a state where ADB protocol initialization failed, and once you've shipped a broken adbd you can't diagnose what broke it (you've lost ADB). The `su` install touches NOTHING in the boot path — adbd, init, ramdisk, even `default.prop` are all stock. If `/system/xbin/su` somehow doesn't work post-flash, ADB still works fine; we can pull `/system/xbin/su` and check what's wrong (perms? mode bits? signing? wrong arch?) without losing visibility.

### Watch-items on the root install itself

- **SELinux / `/system` enforcement.** The current `su` works because Android 4.2.2 + this OEM build apparently allows setuid binaries on `/system` to escalate. If a future firmware update hardens this, the manager-APK-paired SuperSU/Magisk fallback would become necessary.
- **Cross-firmware portability.** `su` is verified on v3.0.2 only. If the `KNOWN_FIRMWARES` manifest gains other firmware versions (e.g. a hypothetical 3.0.3), re-verify `--root` against each.
- **Kernel-level fallback** (CVE-based exploits against the 3.4-era kernel) and **MTK-specific accessory binaries** (`mtk_mtkbt_root` etc.) remain available if the setuid path is ever closed.

## mtkbt AVRCP State Machine Analysis

### Key Globals

| Symbol | Offset | Role |
|---|---|---|
| `[conn+0xe99]` | per-conn | State byte for AVRCP notification state machine (values 0–9) |
| `[conn+0x149]` | per-conn | Negotiated AVRCP version from remote SDP (0x10=1.0, 0x13=1.3, 0x14=1.4) |
| `[conn+0x5cc]` | per-conn | Callback fn ptr (set to `0x29e98` via `register_callback` at `0x2fecc` from `0x28a5e`) |
| `[conn+0x5d0]` | per-conn | State code consulted by op_code=4 dispatchers (vals: 0x82, 0x81, 0x20, 0xa0, …) |
| `[global+0x25800+0x1b8]` | BSS | Callback dispatch count; drives TBH dispatch in `0x29e98` |

### State Machine Values

| Value | Meaning | Set at |
|---|---|---|
| 0 | init | initial |
| 1 | new connection | `0x028d72` (connect handler) |
| 3 | pending REGISTER_NOTIFICATION response | `0x029200` (incoming REGISTER_NOTIFICATION received) |
| 5 | active registration | `0x0293d6` |

### Three op_code=4 dispatchers (3-slot fn-ptr table at vaddr `0xf94b0..0xf94bc`)

Confirmed reachable via R_ARM_RELATIVE relocations populated at load time.

| Slot | Vaddr | Fn ptr | Function character |
|---|---|---|---|
| 0 | `0xf94b0` | `0x3060c` | 3 reads of `[+0x149]` (signed); cmps `[+0x5d0]` against 0xa0, 0x82. **E8 patch site (BGE→NOP at `0x3065e`).** |
| 1 | `0xf94b4` | `0x30708` | 2 reads of `[+0x149]` (unsigned with `& 0x7f`); cmps `[+0x5d0]` against 0x82, 0x81, 0x20 |
| 2 | `0xf94b8` | `0x3096c` | 1 read of `[+0x149]`; classic version-dispatch (cmp `#0x10` / `#0x20`). Old E5 patch site. |

All three reach the AVRCP callback via `[conn+0x5cc]` (mapped to fn `0x29e98`) — they're not mutually exclusive paths, but each has different upstream gating logic. Post-E8 testing definitively showed **none of the three are reached for our peers**: only msg_ids 505 and 506 ever arrive, never `op_code=4`. This is now understood (per the 2026-05-04 conclusion) as mtkbt's AVCTP RX silently dropping unrecognized AVRCP COMMANDs at a layer upstream of the dispatcher table, because mtkbt's compiled command set is 1.0-only.

### Callback registration mechanism (Trace #1f)

```asm
0x028a56:  ldr r1, [pc, #0x17c]    ; r1 = literal 0x1439 (PC-rel offset)
0x028a5c:  add r1, pc               ; r1 = 0x1439 + 0x28a60 = 0x29e99
0x028a5e:  bl 0x2fecc               ; register_callback(conn, 0x29e99, ...)

register_callback (0x2fecc):
  takes (conn_ptr, fn_ptr, sub_arg) and stores fn_ptr at [conn+0x5cc].
```

The literal `0x1439` is **not** a function address — it's a PC-relative offset. Earlier static-analysis searches missed this pattern. The earlier documented analysis of `0x29e98` (callback dispatcher TBH) elsewhere in this document is correct; the function is reachable, just registered through a PIC-style mechanism.

The remaining "0-caller" functions (`0x6d04a` AV/C parser, `0x6d25c` AVCTP register PSM, `0x6d9ba` AVCTP RX handler, `0x6cf30` AVCTP_ConnectRsp) show **zero PIC constructions, zero R_ARM_RELATIVE, zero literal pool entries, zero direct callers**. Likely registered through similar mechanisms via different `register_*` functions not yet enumerated.

## Post-D1 Analysis — Why `tg_feature:0` Persists in CONNECT_CNF

### CONNECT_CNF handler dissection (`libextavrcp_jni.so`)

The receive loop (`FUN_0x5f0c`) dispatches on `msg_id` using a TBH at `0x60B8`. Resolved jump table:

| msg_id | Dec | TBH index | Handler vaddr |
|---|---|---|---|
| 505 | CONNECT_CNF | 4 | **`0x62EA`** |
| 506 | connect_ind | 5 | `0x619C` |

**CONNECT_CNF handler at `0x62EA`:**
1. Reads `result` from ILM+0x02, `conn_id` from ILM+0x01 → log
2. Reads `bws` from ILM+0x0c, **`tg_feature` from ILM+0x0e**, `ct_feature` from ILM+0x10 → log
3. Loads global flag; if flag=1: sends browse connect req; else: exits to function epilogue

`tg_feature` is read and logged, then discarded. Whether 0 or non-zero, behavior is identical. `cardinality` is not set here.

### connect_ind handler and CONNECT_RSP payload

`connect_ind` handler (`0x619C`) calls `btmtk_avrcp_send_connect_ind_rsp` (PLT `0x3618`) at `0x62A8`:
```asm
0x62a0:  ldrb.w r1, [sp, #0x170]   ; r1 = conn_id
0x62a6:  movs r2, #1                ; r2 = 1 (accept)
0x62a8:  blx #0x3618                ; btmtk_avrcp_send_connect_ind_rsp(conn_ptr, conn_id, 1)
```

CONNECT_RSP payload (msg_id=507):
```
byte[0..3]  0x00000000
byte[4]     conn_id
byte[5]     0x01     (accept flag, hardcoded)
byte[6]     0x00     (no tg_feature_code sent to mtkbt)
byte[7]     0x00
```

`g_tg_feature` (set to 0x0e by C2b) is **not included in the CONNECT_RSP payload**. mtkbt's CONNECT_CNF tg_feature field is populated from mtkbt's own internal SDP registration state — D1 enables that registration, but mtkbt reports tg_feature=0 regardless.

### Java layer audit (Trace #4 cross-reference)

(See the Trace #4 section earlier in this document for the full decompilation. Summary preserved here for reference:)

- `BTAvrcpMusicAdapter.getSupportVersion()B` returns `0x0e` if `sPlayServiceInterface` is true, else `0x0d`. Confirms F2's importance: `disable()` must reset the flag so re-activation doesn't see stale state.
- `BTAvrcpMusicAdapter.checkCapability()V` builds the 1.4 EventList `[1, 2, 9, 10, 11]` (PLAYBACK_STATUS_CHANGED, TRACK_CHANGED, NOW_PLAYING_CONTENT_CHANGED, AVAILABLE_PLAYERS_CHANGED, ADDRESSED_PLAYER_CHANGED) when v=0xe.
- `BTAvrcpMusicAdapter.registerNotification(B, I)Z` (the cardinality update site): events 1/2/9 → handle (`bReg=true`); 3/4/5/8/13 → blocked (`bReg=false`); 10/11/12 → fall through. If `bReg`: `field@0x90.set(eventId)` and log `[BT][AVRCP] mRegBit set %d Reg:%b cardinality:%d`.

**Definitive verdict (Trace #4):** logcat across multiple sessions shows neither `[BT][AVRCP](test1) registerNotificationInd eventId:%d` nor the cardinality update log. **`registerNotificationInd` never fires** — the JNI never receives a "REGISTER_NOTIFICATION arrived" event from mtkbt. Java layer is definitively ruled out.

### Where the cardinality:0 gate is

The gate is unambiguously inside mtkbt's native AVRCP layer, between AVCTP RX and the JNI dispatch socket. Per the 2026-05-04 conclusion, this is because mtkbt's compiled command set is 1.0-only — AVRCP 1.3+ COMMANDs from peers reach the AVCTP layer but are not classified by mtkbt as anything its 1.0 dispatcher recognises, and are silently dropped. Candidate drop sites identified for the user-space proxy work:

- mtkbt's AVCTP receive handler at fn `0x6d9ba` (live `0x40128d9a` per probe v3 PIE base `0x400c1000`) — silently drops the inbound L2CAP frame before dispatch.
- The silent-drop site at `0x0513a4` (live `0x401123a4`) — `[AVRCP][WRN] AVRCP receive too many data. Throw it!`.
- The L2CAP→AVCTP demux logic upstream of `0x6d9ba` — wrong PSM routing, missing peer-state guard, etc.
- `0x6cf30` (live `0x40128f30`) — AVCTP_ConnectRsp.

These are the gdbserver targets for Phase 1 of the proxy work (see "Path forward" section above).

## All Patches — Complete Status

### `mtkbt` binary (11 patches in the `--avrcp` set)

| ID | Offset | Patch | Status |
|---|---|---|---|
| **B1** | `0x0eba6d` | `0x00 → 0x03` (AVCTP 1.0→1.3, Groups 1&2 TG ProtocolDescList) | Live (in `--avrcp`) |
| **B2** | `0x0eba37` | `0x00 → 0x03` (AVCTP 1.0→1.3, Group 3 CT ProtocolDescList) | Live |
| **B3** | `0x0eba25` | `0x00 → 0x03` (AVCTP 1.0→1.3, Group 1 AdditionalProtocol) | Live |
| **C1** | `0x0eba4b` | `0x00 → 0x04` (AVRCP 1.0→1.4, record [23] ProfileDescList) | Live |
| **C2** | `0x0eba58` | `0x00 → 0x04` (AVRCP 1.0→1.4, record [18] ProfileDescList, served) | Live |
| **C3** | `0x0eba77` | `0x03 → 0x04` (AVRCP 1.3→1.4, record [13] ProfileDescList) | Live |
| **A1** | `0x38BFC` | `40 f2 01 37 → 40 f2 01 47` (MOVW r7: runtime SDP struct) | Live |
| **D1** | `0x38C6C` | `03 d1 → 00 bf` (BNE→NOP: registration guard bypass) | Live |
| **E3** | `0x0eba5b` | `0x01 → 0x33` (Group 2 TG SupportedFeatures: 0x0001 → 0x0033, served) | Live, confirmed by sdptool XML |
| **E4** | `0x0eba4e` | `0x21 → 0x33` (Group 1 TG SupportedFeatures: 0x0021 → 0x0033, defense-in-depth) | Live |
| **E8** | `0x3065e` | `13 da → 00 bf` (BGE→NOP in op_code=4 slot-0 dispatcher, force 1.3/1.4 init) | Live, observed inert |
| ~~E5~~ | `0x309ed` | BNE→B in op_code=4 slot-2 | **Removed 2026-05-02** (inert) |
| ~~E7a~~ | `0x033dec` | `0x90 → 0x94` no-SDP fallback | **Removed 2026-05-02** (inert) |
| ~~E7b~~ | `0x034100` | `0x90 → 0x94` no-SDP fallback (second site) | **Removed 2026-05-02** (inert) |
| ~~E1, E2~~ | various | State-gate / version-check NOPs | **Reverted 2026-05-01** (incorrect) |
| ~~G1, G2~~ | `0x675c0` / `0xb408` | xlog→logcat redirect (with and without NULL guard) | **Reverted 2026-05-02 / 2026-05-03** (broke BT) |

### `MtkBt.odex` (2 patches)

| ID | Offset | Patch | Status |
|---|---|---|---|
| **F1** | `0x3e0ea` | `0a → 0e` (`getPreferVersion()` returns 14, AVRCP 1.4) | Live (in `--avrcp`) |
| **F2** | `0x03f21a` | `BluetoothAvrcpService.disable()` resets `sPlayServiceInterface = false` | Live |

### `libextavrcp_jni.so` (4 patches)

| ID | Offset | Patch | Status |
|---|---|---|---|
| **C2a** | `0x3764` | `1d 46 → 23 25` (sdpfeature=0x23 hardcoded, bypass bitmask) | Live, confirmed by logcat (sdpfeature:35) |
| **C2b** | `0x37a8` | `01 20 → 0e 24` (`g_tg_feature=0x0e` hardcoded) | Live |
| **C3a** | `0x5e56` | `0d 2c → 0e 2c` (GetCapabilities event cap cmp threshold 13→14) | Live, never observed firing — mtkbt doesn't dispatch GetCapabilities to JNI |
| **C3b** | `0x5e5c` | `0d 24 → 0e 24` (GetCapabilities event cap movs value 13→14) | Live, same as C3a |

### `libextavrcp.so` (1 patch)

| ID | Offset | Patch | Status |
|---|---|---|---|
| **C4** | `0x002e3b` | `03 01 → 04 01` (version constant in .text) | Live (in `--avrcp`) |

### `/sbin/adbd` (3 patches in `boot.img` ramdisk) — closed, see "adbd Root Patches" section above

| ID | Offset | Patch | Status |
|---|---|---|---|
| ~~**H1**~~ | `0x14b8` | `0b 20 → 00 20` (setgroups count 11 → 0) | Reverted — caused "device offline" on hardware |
| ~~**H2**~~ | `0x14c6` | `4f f4 fa 60 → 4f f0 00 00` (setgid arg 2000 → 0) | Reverted — same |
| ~~**H3**~~ | `0x14d4` | `4f f4 fa 60 → 4f f0 00 00` (setuid arg 2000 → 0) | Reverted — same |
| ~~**H1/H2/H3 (NOP-the-blx, earlier revision)**~~ | `0x14bc`/`0x14ca`/`0x14d8` | `blx setgroups/setgid/setuid → movs r0,#0; nop` | Reverted — also caused "device offline" |

## Binary Reference Data

### `mtkbt`

| Property | Value |
|---|---|
| Stock MD5 | `3af1d4ad8f955038186696950430ffda` |
| Patched MD5 (full `--avrcp`, 11 patches: B1-B3, C1-C3, A1, D1, E3, E4, E8) | `d47c904063e7d201f626cf2cc3ebd50b` |
| File size | 1,029,140 bytes |
| Format | ELF32 LE ARM, **ET_DYN** (PIE), base `0x00000000` (live PIE base on v3.0.2: `0x400c1000` per probe v3) |
| ISA | ARM Thumb-2 throughout |

**ELF segment map:**

| Region | File offset | Vaddr | Flags |
|---|---|---|---|
| RX (code + rodata + SDP blob) | `0x00000000` | `0x00000000` | R-X |
| `.data.rel.ro.local` | `0x000f3d40` | `0x000f4d40` | RW- |
| `.data` (descriptor table) | `0x000f9000` | `0x000fa000` | RW- (vaddr+0x1000) |
| BSS | — | `0x000fbe60`–`0x001be63d` | RW- (no file bytes; size 0xc27dd) |

### `MtkBt.odex`

| Property | Value |
|---|---|
| Stock MD5 | `11566bc23001e78de64b5db355238175` |
| Patched MD5 | `acc578ada5e41e27475340f4df6afa59` |
| Format | ODEX `dey\n036\0`, embedded DEX `dex\n035\0` at offset `0x28` |

### `libextavrcp_jni.so`

| Property | Value |
|---|---|
| Stock MD5 | `fd2ce74db9389980b55bccf3d8f15660` |
| Patched MD5 | `6c348ed9b2da4bb9cc364c16d20e3527` |
| Format | ELF32 LE ARM, ET_DYN, base `0x00000000` |
| Global `g_tg_feature` | `0xD29C` |
| Global `g_ct_feature` | `0xD004` |
| CONNECT_CNF handler | `0x62EA` (msg_id=505, TBH index=4) |
| connect_ind handler | `0x619C` (msg_id=506, TBH index=5) |
| `getCapabilitiesRspNative` | `0x5DE8` (FUN_005de8; C3a at 0x5e56, C3b at 0x5e5c) |
| `activateConfig_3req` | `0x375C` (C2a at 0x3764, C2b at 0x37a8) |

**ILM layout in CONNECT_CNF receive loop stack frame:**

| ILM offset | sp offset | Field | Observed value (peer 38:42:0B:38:A3:3E) |
|---|---|---|---|
| +0x00 | sp+0x170 | conn_id (byte) | 1 |
| +0x02 | sp+0x172 | result (u16) | **4096 (0x1000)** ← phantom lead per Trace #10; mtkbt's standard ACK status code |
| +0x0c | sp+0x17c | bws (u16) | 0 |
| +0x0e | sp+0x17e | tg_feature (u16) | 0 (cosmetic in JNI handler) |
| +0x10 | sp+0x180 | ct_feature (u16) | 0 |

### `libextavrcp.so`

| Property | Value |
|---|---|
| Stock MD5 | `6442b137d3074e5ac9a654de83a4941a` |
| Patched MD5 | `943d406bfbb7669fd62cf1c450d34c42` |
| File size | 17,552 bytes |
| `btmtk_avrcp_send_activate_req` | `0x19CC` |
| `AVRCP_SendMessage` | `0x18EC` |

### `/sbin/adbd` (boot.img ramdisk, historical)

| Property | Value |
|---|---|
| Stock MD5 | `9e7091f1699f89dc905dee3d9d5b23d8` |
| Patched MD5 (arg-zero) | `9eeb6b3bef1bef19b132936cc3b0b230` |
| Patched MD5 (NOP-the-blx, superseded — caused "device offline") | `ccebb66b25200f7e154ec23eb79ea9b4` |
| File size | 223,132 bytes (unchanged after patching) |
| Format | ELF32 LE ARM, EXEC (statically linked, stripped) |
| RX segment | file_off `0x0`, vaddr `0x8000`, size `0x34594` |
| Privilege-drop block | vaddr `0x94b8` (file_off `0x14b8`) |

## Eliminated Paths — Do Not Pursue

| Path | Why eliminated |
|---|---|
| Patching record [13] blob alone | Not the served ProfileDescList — record [18] overrides via last-wins. |
| Old patches #2/#3 as "read-back only" | Both target live ProfileDescList minor-version bytes — superseded by C1/C2 at 1.4. |
| Patching 0xeba1d / 0xeba4e (legacy claim) | Unrelated bytes; 0x0311 IS registered in all three groups. |
| Descriptor table flags / ptr patches (0x0f97b2) | `flags` = element size, not control bit. |
| FUN_00022cec MOVW cluster (0x00012d7c, 0x00012d84) | Not on any SDP path. |
| `ldrb.w` intercept at 0x0000ead4 | FUN_000108d0 ignores its r1 parameter. |
| Version sink at FUN_000afd60 (0x000afd6a) | Downstream of SDP record construction. |
| Code caves in RX segment | All null blocks are live SDP/string data. |
| Code caves in `.data` | RW- segment — non-executable; causes BT crash. |
| BSS caves | No file bytes; loader zeroes before execution. |
| **E1** `0x29be4` BNE.W→NOP | State gate is intentional; bypass caused unsolicited responses → car disconnect. **Reverted 2026-05-01.** |
| **E2** `0x0309ec` BNE→NOP | Branch routes 1.3/1.4 cars to *correct* count=4 path; NOP'ing it bypassed init. **Reverted 2026-05-01.** |
| **E5/E7a/E7b** | Empirically inert across all three peers; Trace #1f confirmed the patched functions ARE reachable via PIC callback registration, but the patched code paths are not exercised at runtime for our peer state. **Removed 2026-05-02.** |
| **G1/G2** xlog→logcat redirect | Crashed mtkbt at NULL fmt; even with NULL guard, BT framework couldn't enable. **Reverted 2026-05-03.** Path closed within current constraints. |
| `__xlog_buf_printf` capture without root | Special MTK tooling required. **Superseded by `@btlog` passive tap (Trace #9, requires root).** |
| Property-only adbd root via `default.prop` | OEM adbd has stripped the standard `should_drop_privileges()` gating; `ro.secure=0` is inert (confirmed empirically 2026-05-03 — `adb shell id` returned `uid=2000(shell)` with all properties correctly set). |
| H1/H2/H3 binary patches in `/sbin/adbd` (NOP-the-blx and arg-zero revisions) | **Tried 2026-05-03; both caused "device offline" on hardware.** Static analysis found no `getuid()` gate, no uid==2000 compare; the failure mode is something we can't see without on-device visibility (which we lose the moment we ship a broken adbd). `--root` flag removed from the bash in v1.7.0; **superseded 2026-05-03 (v1.8.0) by the setuid `/system/xbin/su` install** which leaves `/sbin/adbd` untouched. |
| `AttrID 0x0311` SupportedFeatures via SDP response | Initial claim "not registered" was incorrect — IS registered in all three groups. E3/E4 patches the served value. |
| IBTAvrcpMusic / binder dispatch | Not the gate (Trace #4 ruled out the Java layer). |
| HCI snoop (`persist.bt.virtualsniff`) | Breaks BT init. **Superseded by `@btlog` passive tap (Trace #9).** |
| Chip firmware (`mt6572_82_patch_e1_0_hdr.bin`) | WMT common subsystem only — sleep/coredump/queue/GPS/Wi-Fi power. Zero AVRCP code. |
| `libbluetooth*.so` libs (Trace #7) | All four libs are HCI/transport-only — UART link to MT6627, GORM/HCC chip-bringup, NVRAM BD-address management. Zero hits for `avrcp/avctp/profile/capability/notif/metadata/cardinal`. mtkbt is the AVRCP processor. |
| `0x6d04a` AV/C parser as patch site | Confirmed dead code via multiple independent searches (no callers via any mechanism). |
| Java-side patches beyond F1/F2 (Trace #4) | Java initializes correctly for AVRCP 1.4; no version gate or capability check would suppress events when they DO arrive. |
| **Browsing-bit experiment** (E3/E4 `0x33 → 0x73`) | Landed on the wire (sdptool confirmed `0x0073`); peer behaviour identical to baseline. **Disproven 2026-05-04.** Tooling deleted. |
| **Pixel-shape experiment** (B/C bumped to AVCTP 1.4 + AVRCP 1.5; E3/E4 `0x33 → 0xd1` Cat1+PAS+Browsing+MultiPlayer) | Landed on the wire; peer (Sonos) tried to open AVCTP browse PSM `0x1B`; mtkbt has no L2CAP listener for that PSM (`+@l2cap: cannot find psm:0x1b!`); peer gave up. **Disproven 2026-05-04.** Tooling deleted. |
| **Pixel-1.3 mimicry experiment** (B/C dropped to AVCTP 1.2 + AVRCP 1.3; E3/E4 → 0x01; A1/F1 reverted) | Landed on the wire; peer (Sonos) sent one AVRCP COMMAND (AVCTP_EVENT:4 with transId:0); mtkbt dropped silently; peer gave up. **Disproven 2026-05-04.** Tooling deleted. |
| **Features-only experiment** (E3/E4 `0x33 → 0x01` keeping AVRCP 1.4) | Same dropped-COMMAND failure as Pixel-1.3 mimic. **Disproven 2026-05-04.** Tooling deleted. |
| **Y1MediaBridge actively interfering** | Bridge-disable test 2026-05-04 confirmed bridge is innocent: same failure mode with bridge present (`mbPlayServiceInterface=true`) or disabled (`mbPlayServiceInterface=false`). The 1.4-version push comes from F1 (in odex) + B/C/E patches, not from the bridge. Bridge implements `IBTAvrcpMusic` correctly via raw `onTransact` dispatch — but MtkBt's `BTAvrcpMusicAdapter` never calls `registerCallback` against it because no peer-side AVRCP COMMAND ever reaches MtkBt to trigger the call. Bridge stays idle as a downstream consequence of the upstream silence. |

## Post-Flash Verification Checklist

For the (now-deprecated) `--avrcp` patch set, when verifying that the flash landed correctly:

- `sdptool browse <Y1_BT_ADDR>` → `AV Remote (0x110e) Version: 0x0104`
- `sdptool browse` → `AVCTP uint16: 0x0103`
- `sdptool browse` → `SupportedFeatures = 0x0033`
- D1 flashed — mtkbt no longer crashes, CONNECT_CNF received
- `tg_feature:0` confirmed cosmetic — JNI CONNECT_CNF handler does not gate on it
- mtkbt patched MD5 `d47c904063e7d201f626cf2cc3ebd50b` confirmed device-side
- `libextavrcp_jni.so` patched MD5 `6c348ed9b2da4bb9cc364c16d20e3527` confirmed
- `libextavrcp.so` patched MD5 `943d406bfbb7669fd62cf1c450d34c42` confirmed
- `MtkBt.odex` patched MD5 `acc578ada5e41e27475340f4df6afa59` confirmed
- `/system/xbin/su` setuid escalator — hardware-verified 2026-05-04. `adb shell` → `su` → `id` returns `uid=0(root) gid=0(root)`; prompt `$`→`#`.
- ~~logcat → `cardinality > 0` in ACTION_REG_NOTIFY lines~~ — **STILL 0 across all peers**. This is the bug that the user-space proxy work is intended to fix.
- ~~`getCapabilitiesRspNative` log~~ — never fires; confirms mtkbt is not dispatching inbound AVRCP commands to the JNI for any tested peer.

## Log Tags

| Tag | Layer |
|---|---|
| `DebugY1` | Innioasis Y1 stock player |
| `Y1MediaBridge` | Y1MediaBridge bridge service |
| `MMI_AVRCP` | MtkBt.apk AVRCP middleware |
| `JNI_AVRCP` | `libextavrcp_jni.so` JNI bridge |
| `EXT_AVRCP` | `libextavrcp_jni.so` / `libextavrcp.so` |
| `BWS_AVRCP` | AVRCP 1.4 browse layer |
| `EXTADP_AVRCP` | Adapter layer |

## Trace #8 (2026-05-04, post-root) — `MSG_ID_BT_AVRCP_CONNECT_CNF` emit-path map in mtkbt

Pure static analysis on stock mtkbt MD5 `3af1d4ad…`, driven by the post-root pivot to "find where `result=0x1000` is set" before reaching for gdbserver. The `result:4096` lead was disproven by Trace #10; this trace's emit-chain map is preserved because it documents the IPC dispatcher structure that the user-space proxy work's Phase 4 (outbound RSP path) will need.

**Emit chain identified end-to-end:**

| Layer | Vaddr | Role |
|---|---|---|
| msg_id 505 send | `0x000511c0` | Common ILM send wrapper (`b.w 0x67bc0`); shared by every adp message. |
| CONNECT_CNF builder stub | `0x000512a8` | The **only** site in the binary that issues msg_id 505. Allocates 24-byte buf via allocator at `0x6a29c`, lays out: `buf+4`=conn_id (arg1 byte), `buf+5`=flag (arg4 byte), `buf+6`=**result u16** (arg2), `buf+8..15`=memcpy(arg3, 8). The JNI's ILM offsets are buf+4-relative — JNI's `ILM+0x02` ⇔ buf+6. |
| Stub caller (sole) | `0x000515c4` | `bl 0x512a8`. Picks args from a dispatcher event struct in `r4`: `arg2 = ldrh r2, [r4, #2]` ⇒ **event[2:4] = result u16 in CONNECT_CNF**. |
| Event-code dispatcher | `0x000514a4` | `ldrb r3, [r4, #0]; cmp r3, #102; tbh [pc, r3, lsl#1]` — generic AVRCP-adapter event-router. **Case 3 = CONNECT_CNF** (TBH entry value 0x77 → handler at `0x000515b6`). |
| Event constructor (CONNECT_CNF) | `0x0000f7b0` | The only function found that does `movs r1, #3; strb.w r1, [sp]` then `blx r2` where `r2 = ctx[4]` (= dispatcher fn ptr). Builds the event on its own stack and dispatches via `ctx->callback`. |

**Where the 0x1000 enters the system (sibling path):** Same code-region neighbour `0x0000f83c` calls helper `0x00010404` with `r1 = 0x1000` (bytes verified: `4f f4 80 51` at `0xf8a6`). Helper `0x10404` lays out an event on a 1872-byte stack frame: `strh.w r1, [sp, #6]` (=event[2:4] = 0x1000) and `strb.w r5, [sp, #4]` where `r5 = #8` — so it dispatches **event_code=8**, not 3. The dispatcher's case-8 handler at `0x00051622` reads `event[8..12]` but **does not** read `event[2:4]`. So this 0x1000-injection path does not directly reach CONNECT_CNF's result field — it produces a different msg_id with a `0x1000` status payload.

**Second TBH dispatcher** at `0x000518ac` (msg_ids 500-611, JNI→mtkbt direction):
- 500: ACTIVATE_REQ
- 502: DEACTIVATE_REQ
- 504: connect-related
- 507: CONNECT_RSP
- 508/513: disconnect-related
- 511, 515, 517, 520, 522, 524…560+: various AVRCP COMMAND-class messages
- The full TBH map is in the binary; consult via Trace #8's tooling (`objdump -d` + Python xref pass) when needed.

**Negative results (so the next person doesn't redo them):**

- No site in the binary directly stores `0x1000` to `[rN, #2]` of any struct (zero hits across all `mov*/strh*` pair scans in `.text`).
- 28 sites store `0x1000` to `[rN, #0xe]` (= ILM+0x0e = `tg_feature`) — concentrated in the `0x13xxx`–`0x15xxx` range. Fits the bit-12 = "feature degraded" hypothesis but doesn't directly set CONNECT_CNF result.
- Dispatcher `0x000514a4` has zero direct callers and zero R_ARM_RELATIVE relocs and zero word-aligned hits in `.data`/`.data.rel.ro` — registered via the same PIC `add Rn, pc` callback-registration pattern documented for `0x29e98` (Trace #1f).
- The second msg_id-505 hit at `0x00071ffa` is a **false positive** — 505 there is the source line number passed to `__xlog_buf_printf` (signature `xlog(level, line_no, fmt, …)`), not an ILM msg_id.

**Tooling:** linear `objdump -d` of the whole binary into `/tmp/mtkbt.dis` (~290k lines) plus a small Python pass that parses `mn`/`rest`/`addr` and resolves PC-relative xrefs by walking back from `add Rn, pc` to the prior `ldr Rn, [pc, #N]`. Confirmed correct against known-good xrefs to `[AVRCP] avctpCB AVCTP_EVENT:%d` (`0xc8c7e`) and `bt.ext.adp.avrcp` (`0xda7f9`).

## Trace #9 (2026-05-04, post-root) — `@btlog` passive tap unlocks `__xlog_buf_printf` + HCI snoop in one stream

The post-root probe (`tools/probe-postroot.sh` + `…-device.sh`) found that `mtkbt` runs `socket_local_server("btlog", ABSTRACT, SOCK_STREAM)` at vaddr `0x6b4d4` and that the abstract socket `@btlog` (inode 1497, mtkbt fd 13) is a `SOCK_STREAM` listener with `SO_ACCEPTCON` set. Built `src/btlog-dump/` — a 1016-byte no-libc ARM ELF using the same direct-syscall style as `src/su/` — that opens an `AF_UNIX/SOCK_STREAM` socket, `connect()`s to the abstract `@btlog` address, and pipes `read()` to stdout. **Connect requires no handshake; mtkbt starts pushing the moment a client attaches.**

First capture confirms the stream contains both layers we needed:

- **HCI command/event traffic** — fully decoded: `HCC_INQUIRY`, `HCC_CREATE_CONNECTION`, `HCC_WRITE_SCAN_ENABLE`, `HCC_AUTH_REQ`, `HCC_READ_REMOTE_FEATURES`, `HCC_READ_REMOTE_VERSION`, `HCC_READ_REMOTE_EXT_FEATURES`, `HCE_COMMAND_COMPLETE`, `HCE_READ_REMOTE_FEATURES_COMPLETE`, `HCE_READ_REMOTE_VERSION_COMPLETE`, `[BT]GetByte:`/`[BT]PutByte:` byte-level transport.
- **`__xlog_buf_printf` output** — every `[AVRCP]…`, `[AVCTP]…`, `[L2CAP]…`, `[ME]…`, `[BT]…`, `SdpUuidCmp:…`, `ConnManager: event=…` log line that's invisible to logcat.

**Framing format (preliminary, by inspection):**

| Bytes | Field |
|---|---|
| 1 | Start marker `0x55` ('U') |
| 1 | Always `0x00` (separator/flag?) |
| 1 | Frame length |
| 2 | Sequence ID (alphabetic, increments — `bl`, `bm`, `bn`, …) |
| 1 | Severity / category (`0x12` for xlog text, `0xb4` for HCI snoop) |
| 1 | `0x00` pad |
| body[0..1]   | Often constant `00 e5` |
| body[2..6]   | Timestamp (`u32` LE; monotonic per process lifetime, **separate domains per severity**) |
| body[6..10]  | Zero/flag bytes |
| body[10..12] | `u16` LE — typically the format-string base length |
| body[12..]   | Variable-length sub-header (often NUL padding for arg alignment), then format string + substituted args, NUL-terminated |

Severities seen: `0x12` (xlog text) and `0x07` / `0x08` / `0xb4` (HCI snoop / module-specific).

See `src/btlog-dump/README.md` for the maintained version of this format documentation.

**What this tooling collapsed from the prior plan:**

- HCI snoop / btsnoop: DONE via `@btlog`. No need to push `hcidump` or fight with `persist.bt.virtualsniff`.
- `__xlog_buf_printf` capture: DONE via `@btlog`. Same stream.
- Surgical `__android_log_print` instrumentation: no longer needed for read-only observation. The xlog tag IS the log; we just had no way to read it before.

## Trace #10 (2026-05-04, post-root) — first dual capture (Sonos Roam) kills the `result:4096` lead

Captured `tools/dual-capture.sh` against Sonos Roam at `/work/logs/dual-sonos-attempt1/` — 1.5 MB `btlog.bin`, 159-line `logcat.txt`. The smoking-gun line landed cleanly:

```
05-03 23:29:43.371   710   710 I JNI_AVRCP: [BT][AVRCP]+_activate_1req index:0 version:14 sdpfeature:35
05-03 23:29:43.371   710   710 I EXTADP_AVRCP: msg=500, ptr=0xBEA64D30, size=8        ← JNI sends ACTIVATE_REQ
05-03 23:29:43.373   710  2451 I JNI_AVRCP: [BT][AVRCP] Recv AVRCP indication : 501   ← JNI receives ACTIVATE_CNF
05-03 23:29:43.374   710  2451 V EXT_AVRCP: [BT][AVRCP] activate_cnf index:0 result:4096   ★

… 22 seconds later, peer initiates connect …

05-03 23:30:06.084   710  2451 I JNI_AVRCP: [BT][AVRCP] Recv AVRCP indication : 506   ← CONNECT_IND from mtkbt
05-03 23:30:06.085   710  2451 I EXTADP_AVRCP: msg=507, ptr=0x523D3A98, size=8        ← JNI sends CONNECT_RSP
05-03 23:30:06.139   710  2451 I JNI_AVRCP: [BT][AVRCP] MSG_ID_BT_AVRCP_CONNECT_CNF conn_id:1  result:4096   ★
05-03 23:30:06.139   710  2451 I JNI_AVRCP: [BT][AVRCP] MSG_ID_BT_AVRCP_CONNECT_CNF bws:0 tg_feature:0 ct_featuer:0
```

**`result:4096` appears 3 ms after the JNI sends ACTIVATE_REQ — purely local mtkbt processing, before any peer is involved.** The same `result:4096` then re-appears at CONNECT_CNF time. **`0x1000` is mtkbt's standard "request acknowledged" status code, set on every CNF mtkbt emits to the JNI — not a "feature degraded" or peer-feedback indicator at all.**

This kills the previously-listed primary lead. The Trace #8 emit-chain map is still useful (the IPC dispatcher structure is needed for the proxy work) but no longer aimed at "find where 0x1000 is set" — that question is answered.

**What the dual capture actually shows about the peer:**

- Sonos Roam (`38:42:0B:38:A3:3E`) initiates the connection 22 s after the JNI activate completes — likely after Sonos's own scan/discover cycle.
- L2CAP/AVCTP come up cleanly: 3× `l2cap conn_rsp result:0`, 7× `handleconfigrsp result:0` on `psm:0x19`, then `[AVCTP] chid:66` (channel ID varies between captures — was `0x67` in Trace #9).
- AVRCP profile-level connect succeeds end-to-end: `connect_ind` (msg 506) → `CONNECT_RSP` (msg 507) → `CONNECT_CNF` (msg 505).
- After the connect, **only one `AVCTP_EVENT:4` (RECV_DATA-class event) fires from the peer**, accompanied by `[AVRCP] transId:0`, then **silence** — no further AVCTP RX activity, no `GetCapabilities`, no `RegisterNotification`. Sonos is not following up the basic AVRCP-profile connect with the AVRCP COMMAND PDUs a 1.4 controller should send.
- The Y1 stays in this connected-but-silent state indefinitely until A2DP drops, at which point mtkbt cleans up via `AVRCP: disconnect because a2dp is lost`.
- Java-side `cardinality:0` in `ACTION_REG_NOTIFY` lines is exactly what we'd expect from this state — `mRegBit` is empty because no peer has issued REGISTER_NOTIFICATION.

## Trace #11 (2026-05-04, post-root) — Browsing-bit experiment failed, real-world reference peer comparisons settle the gate-location question

Three independent threads, one conclusion.

### Thread A: Browsing-bit experiment

Hypothesis: served `SupportedFeatures = 0x0033` omits Browsing bit (`0x40`); some 1.4 controllers may decline AVRCP COMMANDs against a TG that doesn't claim Browsing.

Built a non-destructive bash wrapper that swaps `src/patches/patch_mtkbt.py` for an alternate that overrides E3/E4 `after` bytes from `0x33` → `0x73`, runs the standard `--avrcp --bluetooth` flow, then restores the original on EXIT. Flashed and re-captured against Sonos.

Direct evidence the experiment landed on the wire: btlog `SdpUuidCmp:uuid1, len=2, (11  e,  9  1,  4  9,  0 73)` — the served bytes for AVRCP TG (`0x110e`) are now `Version=0x0104` + `SupportedFeatures=0x0073`. Compare to Trace #9's `0x0033`.

**Result: peer behaviour identical to baseline.** 14× `cardinality:0` lines, none non-zero. Same single `[AVRCP] avctpCB AVCTP_EVENT:4` → `[AVRCP] transId:0` → silence. Same `MSG_ID_BT_AVRCP_CONNECT_CNF result:4096 bws:0 tg_feature:0 ct_featuer:0`. L2CAP/AVCTP config exchange clean.

**Hypothesis #1 dead.** Tooling deleted on cleanup.

### Thread B: hypothesis-#3 static (`[AVRCP] transId:0`)

Two callers of the `[AVRCP] transId:%d` log function (`0x11374` static / `0x400d2374` live). Both read transId directly from inbound packet bytes (`event[1]` in caller `0x1457c..0x1458a`; `event[5]` in caller `0x51a20`). The `transId:0` we observe is **the actual transId byte the peer sent on the wire** — not mtkbt mangling the value. transId is a 4-bit AVCTP-header field; `0` is a perfectly valid value for the first packet on a fresh AVCTP channel. **Hypothesis #3 dead.**

Bonus from the same pass: `@btlog`'s `[BT]GetByte:`/`[BT]PutByte:` lines around the AVCTP_EVENT:4 timestamp give a per-byte HCI trace. Decoded, mtkbt sends an outbound L2CAP CONFIG_REQ; Sonos sends back its own CONFIG_REQ for our `cid 0x42` carrying MTU=1024 — standard AVCTP control-channel config. Then AVCTP_EVENT:4 fires once and AVRCP-layer activity stops.

### Thread C: real-world reference-peer comparisons (the decisive evidence)

User-supplied empirical data:

| Test | AVRCP works? | Implication |
|---|---|---|
| Pixel 4 (TG) ↔ Sonos Roam (CT), Sonos app shows now-playing metadata | ✅ | Sonos *is* a real working 1.4 controller |
| Y1 (TG) ↔ Sonos Roam (CT), our captures | ❌ | Y1's TG is broken |
| Y1 (TG) ↔ car head unit (CT) | ❌ — no metadata, **play/pause broken** | Y1's TG is broken end-to-end on the actual goal device (cars are the project's primary AVRCP target per the README's history) |

**The play/pause break is the load-bearing finding.** Play/pause flows car→Y1 as AVRCP `PASS_THROUGH` commands. Functional break in the CT→TG command path — not just notification-cosmetic. Same root cause as cardinality:0.

### Combined verdict

**The gate is on the Y1 side**, not on any peer. Sonos's "single AVCTP_EVENT:4 then silence" pattern is Sonos sending its first AVRCP command (likely `GetCapabilities`), getting nothing usable back from Y1, and giving up.

**Pixel 4 SDP record across all four AVRCP versions (Pixel Developer-Options-forced, captured 2026-05-04):**

| Attribute | Pixel-1.3 | Pixel-1.4 | Pixel-1.5 | Pixel-1.6 |
|---|---|---|---|---|
| 0x0004 AVCTP version | `0x0102` (1.2) | `0x0103` (1.3) | `0x0104` (1.4) | `0x0104` (1.4) |
| 0x0009 AVRCP version | `0x0103` | `0x0104` | `0x0105` | `0x0106` |
| 0x000d AdditionalProtocolDescList | **MISSING** | PSM `0x001b` AVCTP 1.3 | PSM `0x001b` AVCTP 1.4 | PSM `0x001b` AVCTP 1.4 + OBEX (Cover Art) |
| 0x0311 SupportedFeatures | **`0x0001`** (Cat1 only!) | `0x00d1` | `0x00d1` | `0x01d1` (extra bit 8) |

User-confirmed: at every Pixel-AVRCP-version setting (1.3 / 1.4 / 1.5 / 1.6), Sonos receives full title/artist/album metadata + responds correctly to play/pause from Pixel. Cover art doesn't transfer (Sonos-side limitation).

This is what makes the 2026-05-04 conclusion definitive: at AVRCP 1.3, the bare-minimum SDP record (Cat1 features, no AdditionalProtocolDescriptorList, AVCTP 1.2) is sufficient for Sonos to engage AVRCP COMMAND traffic — *if the implementation actually responds to those commands*. Y1 stock advertises features `0x0001` exactly like Pixel-1.3 but at AVRCP 1.0; Sonos doesn't bother sending COMMANDs because AVRCP 1.0 is too primitive. Y1 patched to 1.3+ advertises a richer record but mtkbt drops the COMMANDs Sonos then sends. **mtkbt is a 1.0-class implementation regardless of SDP advertisement.**

## Trace #12 (2026-05-05, post-root) — full silent-drop chain mapped end-to-end via gdbserver

This trace settled the silent-drop architecture conclusively. Five gdb capture iterations narrowed the problem from "somewhere in mtkbt" to a 2-byte patch site, then exposed the next gate one binary upstack.

### Setup

Built `tools/install-gdbserver.sh` (fetches a sha256-pinned ARM 32-bit static gdbserver from `aosp-mirror/platform_prebuilt`, commit `f5033a8c`, sha256 `1c3db6a3...`, 186112 bytes — last touched upstream 2010) and `tools/attach-mtkbt-gdb.sh` (pushes gdbserver, attaches to live mtkbt PID, computes PIE base, generates a `commands`-driven gdb command file with breakpoints at the critical sites and silent printf+continue blocks). Watch-items learned the hard way:

- mtkbt is all Thumb-2. Plain even-addressed BPs make gdb plant 4-byte ARM BKPTs that corrupt Thumb instructions → mtkbt SIGSEGV at NULL on the first BP hit. Fix: `set arm fallback-mode thumb` + `set arm force-mode thumb` in the gdb file (NOT `addr | 1` — that breaks gdb's trap-time PC lookup).
- After mtkbt SIGSEGV mid-debug, gdbserver wedges with the dead PID's ptrace slot. Fix: clean up stale gdbserver via `/proc` walk before each attach, drop the adb forward.
- mtkbt respawns automatically on crash; BT off→on resets cleanly.

### What `--avrcp` (V1+V2+S1, then `--avrcp-min` in the historical iter1) shows

With AVRCP 1.3 + AVCTP 1.2 + a `0x0100` ServiceName attribute on the served SDP record, Sonos sends a real **AV/C VENDOR_DEPENDENT GetCapabilities** (op_code 0x00, vendor BT-SIG `0x001958`, PDU 0x10, capability_id 0x02 = EVENTS_SUPPORTED). Confirmed by gdb breakpoint dumps of the inbound L2CAP frame bytes. This contradicts the earlier 2026-05-04 reading of Trace #10's capture, which assumed the inbound was a malformed/dropped command — it was actually a 14-byte real GetCapabilities all along.

### The full mtkbt RX chain (PASSTHROUGH vs VENDOR_DEPENDENT)

Both frame types follow the same path through:

1. **AVCTP RX inner TBH** at file `0x6da7a` — keyed on `[r5,#0]` (event subtype 0..8); subtype 3 routes to the AV/C-bearing path.
2. **Classifier** at `0x6db7c` — `ldrb r0, [r5,#5]; cmp r0, #1; bhi 0x6dc3a`. For both PASSTHROUGH and VENDOR_DEPENDENT, `[r5,#5]=0` so AV/C parse path taken.
3. **AV/C parse** at `0x6dba0+` — extracts ctype/subunit_type/subunit_id/op_code from frame bytes 0..2, stores at `conn+160..163`.
4. **event_code=4 setter** at `0x6dc36`.
5. **Dispatch** at `0x6de64` via `[r4+244]` callback (= fn at file `0xfb04`, set up via `register_callback` fn at `0x6ce78` from caller at `0xeaec` with PSM=0x17 and a callback-fn-ptr literal).
6. Inside fn `0xfb04`'s default arm, → `bl 0x145b0` (the AV/C-event handler in fn `0x147dc`'s case 4 = TBH index 3).
7. fn `0x145b0` stores frame bytes at `conn+2956..` and `conn+2400+9`; calls `bl 0x144bc`.
8. **fn `0x144bc` op_code dispatch at `0x144e8`** — `ldrb r3, [r6,#3]` reads op_code from `conn+163`:
   - `r3 == 0x7c` (PASSTHROUGH) → `b.n 0x14528` → `bl 0x10404` → emits **msg_id 519** to JNI.
   - `r3 < 0x30` or `r3 != 0x7c` (VENDOR_DEPENDENT op_code 0x00, also UNIT_INFO 0x30, SUBUNIT_INFO 0x31, etc.) → `bcc 0x1454a` or `bne 0x1454a` → `bl 0x11374` → log only, **silent drop**.

The captured `r2` at fn `0x144bc` entry differs (3 for PASS, 9 for VENDOR), but that's downstream of the gate at `0x144e8`. The actual gate is the op_code branch.

### P1 patch (mtkbt, file offset `0x144e8`)

Two-byte rewrite of `cmp r3, #0x30` → `b.n 0x14528`:

| | Bytes (LE) | Encoding |
|---|---|---|
| stock | `30 2b` | `cmp r3, #0x30` (0x2b30) |
| patched | `1e e0` | `b.n 0x14528` (0xe01e, +0x3c from PC at 0x144ec) |

Forces all AV/C frames through the bl `0x10404` → msg 519 emit path regardless of op_code. Hardware-verified 2026-05-05: **VENDOR_DEPENDENT GetCapabilities now reaches JNI as `MSG_ID_BT_AVRCP_CMD_FRAME_IND size:9 rawkey:0 data_len:9`** with the AV/C-body bytes intact.

Ships as the fourth patch in `src/patches/patch_mtkbt.py`. Stock mtkbt md5 `3af1d4ad8f955038186696950430ffda` → output `a37d56c91beb00b021c55f7324f2cc09`.

### What's NOT yet solved — the JNI's "unknow indication" path

The JNI receive function in `libextavrcp_jni.so` is `_Z17saveRegEventSeqIdhh` at file `0x5ee4`. It dispatches msg 519 on **frame size**:

- `cmp.w lr, #3` at `0x6452` — size 3 → PASSTHROUGH path; calls `btmtk_avrcp_send_pass_through_rsp`
- `cmp.w lr, #8` at `0x6524` — size 8 → branch with a BT-SIG vendor check (`cmp r1, #0x5819` at `0x656a`); on match, jumps to `0x65a4` (VENDOR_DEPENDENT handling)
- otherwise → `0x65bc` → "unknow indication" + dump first 16 bytes + default reject (msg_id 520 CMD_FRAME_RSP with NOT_IMPLEMENTED)

P1 produces size=9 frames (the 14-byte AV/C frame minus 3-byte AV/C header minus 2 leading bytes — the trampoline path strips slightly differently from the size=8 path). **Size=9 falls into "unknow indication"**, and the inbound is auto-rejected before reaching Java's `BTAvrcpMusicAdapter`.

The candidate next patch is at file `0x6526` of `libextavrcp_jni.so`: `cmp.w lr, #8` → `cmp.w lr, #9` (single byte 0x08 → 0x09). That'd route size-9 frames into the size-8 branch and onward to the BT-SIG vendor check at `0x656a`. Risk: the size-8 branch's downstream reads (sp+381, sp+382, sp+385) assume a specific stack layout that size-9 frames may not satisfy, AND the path eventually calls `btmtk_avrcp_send_pass_through_rsp` which is the wrong response builder for a VENDOR_DEPENDENT command. May need additional patches to skip the pass_through_rsp call and/or to invoke Java's `BTAvrcpMusicAdapter.checkCapability()` via JNI.

A clean patch will require static-analyzing what `0x65a4+` actually does (whether it reaches Java or just logs+returns) before committing to a byte rewrite.

**2026-05-05 follow-up.** The single-byte J1 (cmp 8 → 9) was tried and rolled back — it routed size-9 frames through the PASSTHROUGH dispatch, generating fake `key=1 isPress=0` events and never reaching Java. Path forward (now in `patch_libextavrcp_jni.py`) is **trampoline T1**: redirect `bne.n 0x65bc` at file 0x6538 to a code-cave at file 0x7308 (overwriting the unused JNI debug method `testparmnum`). The trampoline checks the PDU byte at sp+382, and on `0x10` (GetCapabilities) calls `btmtk_avrcp_send_get_capabilities_rsp` directly via PLT 0x35dc, then exits.

**Iter5 capture (2026-05-05) — T1 confirmed working.** `/work/logs/dual-sonos-avrcp-min-iter5/` shows: 1 size:9 inbound (GetCapabilities) → 1 outbound msg=522 (size 30, the response) → 4 size:13 inbound (Sonos's first-ever follow-up VENDOR_DEPENDENT commands, 2-second retry pattern indicating RegisterNotification with no INTERIM ACK). For comparison, iter4 (J1) had the same size:9 inbound but msg=520 NOT_IMPLEMENTED instead of msg=522, and zero size:13 follow-ups — Sonos gave up. T1 is the first patch that gets Sonos past the GetCapabilities gate.

**T2 added 2026-05-05.** Trampoline T2 at file 0x72d0 (overwriting unused `classInitNative` debug method) handles inbound RegisterNotification(EVENT_TRACK_CHANGED). T1's fall-through arm (originally `b.w 0x65bc`) now bridges to T2 stage 2 at 0x72d4. T2 verifies the PDU is 0x31 and event_id is 0x02, then calls `btmtk_avrcp_send_reg_notievent_track_changed_rsp` (PLT 0x3384) with INTERIM (reasonCode 0x0F) and track_id = 0xFFFFFFFFFFFFFFFF ("no track"). Other registered events (0x01, 0x09, 0x0a, 0x0b) fall through to the original "unknow indication". See `docs/PROXY-BUILD.md` for the full plan including T3/T4 follow-ups.

**Iter6 capture (2026-05-05) — T2 confirmed working.** `/work/logs/dual-sonos-avrcp-min-iter6/` shows: 1× size:9 → 1× msg=522 (T1 GetCapabilities response, same as iter5); 5× size:13 inbound (RegisterNotification for the 5 advertised events); **2× msg=544 size=40 outbound** firing in the same millisecond as inbound size:13 with event_id=0x02 (T2's TRACK_CHANGED INTERIM response — first-ever AVRCP 1.3-shape metadata response built by mtkbt for this device); Sonos accepted and **immediately started sending size:45 GetElementAttributes** (PDU 0x20, 26 retries at 2-second intervals). The size:45 retries continue indefinitely because we don't have a T4 trampoline yet — Sonos is asking "give me the track metadata!" and getting no answer. Y1MediaBridge's `MediaBridgeService` is being connected (`PlayService onServiceConnected`) so track strings are plumbed and ready; the remaining work is T4 (call `btmtk_avrcp_send_get_element_attributes_rsp` with the strings). T4 is the last remaining patch in the metadata path.

**Iter7/iter8/iter9 — fix the unknow-indication path via ELF-extension T4 stub.** iter6 also surfaced a separate problem: unhandled inbound frames (size:13 events ≠ TRACK_CHANGED, size:45 GetElementAttributes) generated zero outbound responses. The b.w 0x65bc fall-through from T1/T2 was reaching the original "unknow indication" code, but that code requires `r0 = r5+8` (conn buffer; set at 0x6528 in original flow) AND `lr = halfword at sp+374` (= SIZE; loaded at 0x644e) — both of which the trampolines clobber. Iter7 restored r0 only (no msg=520 yet); iter8 added the lr restore (8 → 12 bytes at the 0xac54 stub). Iter9 hardware test: msg=520 NOT_IMPLEMENTED now flows for unhandled frames. **Major side effect**: AVRCP service stops restart-looping (iter6 had 30 PIDs cycling; iter9 has 2 stable), so PASSTHROUGH play/pause/skip now actually works on Sonos. First-ever transport-control delivery to a peer for this device.

**Iter10 — single-event advertised.** Iter9 surprise: Sonos aborts the entire RegisterNotification loop on its first NOT_IMPLEMENTED reply. Pre-iter9 the broken unknow path silently dropped the first reject, so Sonos timed out and accidentally tried event 0x02 (TRACK_CHANGED) anyway, which T2 acked. With proper msg=520 flowing, Sonos respects the rejection — meaning it never reaches event 0x02 unless we ack 0x01 (PLAYBACK_STATUS_CHANGED) too. Cheapest fix: advertise only event 0x02 in T1's GetCapabilities response (events count: 5 → 1; events_data: `01 02 09 0a 0b` → `02`). Sonos then registers only TRACK_CHANGED, T2 acks, Sonos proceeds to GetElementAttributes. **Iter10 confirmed**: Sonos sent 1265 size:13 + 1264 size:45 frames in a tight 70Hz loop — full path engaged but no real T4 yet to break the loop.

**Iter11 — first metadata on Sonos screen.** T4 implemented at vaddr 0xac54 in extended LOAD #1 (the 4276-byte page-padding region between the original LOAD #1 and LOAD #2). Single-attribute hardcoded "Y1 Test" Title response, 68 bytes. Argument layout for `btmtk_avrcp_send_get_element_attributes_rsp` (PLT 0x3570) inferred empirically:
- r0 = conn buffer (= r5+8)
- r1 = 0 (string-follows flag — JNI wrapper at 0x56dc dispatches on this)
- r2 = transId (jbyte at caller_sp+368; same convention as track_changed_rsp)
- r3 = 0 (placeholder; meaning unknown but works)
- sp[0]  = attribute_id LSB (1=Title, 2=Artist, 3=Album, 4=TrackNumber, …)
- sp[4]  = 0x6a (UTF-8 charset; JNI hardcodes this)
- sp[8]  = string length (in bytes)
- sp[12] = pointer to UTF-8 string data

**Iter11 hardware-verified 2026-05-05**: "Y1 Test" displayed on Sonos Now Playing screen. **First ever AVRCP metadata delivery from this device to a peer.** Loop continues at 70Hz because the TRACK_CHANGED INTERIM with track_id=0xFFFFFFFFFFFFFFFF tells Sonos to keep re-querying (no stable identity), but the metadata path itself works.

**Iter12 — multi-attribute T4 dispatch (loop, separate frames).** Extended T4 to 152 bytes with a dispatch loop: parse num_attributes from inbound at sp+394, walk requested attribute_ids at sp+395+, and for each one match against {0x01, 0x02, 0x03} — calling the response builder once per supported attribute with hardcoded strings ("Y1 Title", "Y1 Artist", "Y1 Album"). Unsupported attributes (0x04-0x07) silently skipped. **Hardware-verified iter12 2026-05-05**: ratio 3:1 of msg=540 to size:45 — three frames per query. Sonos accepted the first frame and displayed "Y1 Title" only; subsequent frames with same transId were ignored as duplicates. Output md5 `fa6191d6ce8170f5ef5c8142202c8ba5`.

**Iter13 — multi-attribute single-frame response (correct semantics, breakthrough).** After disassembling `btmtk_avrcp_send_get_element_attributes_rsp` at libextavrcp.so:0x2188, decoded the function's actual contract:
- `arg1 (r1)` = "with-string / reset" flag (0 = with string, append; !=0 = no-string finalize)
- `arg2 (r2)` = attribute INDEX in this response (0..N-1) — **NOT transId**
- `arg3 (r3)` = TOTAL number of attributes in this response
- `sp[0]` = attribute_id LSB
- `sp[4]` = 0x6a (UTF-8, JNI-hardcoded)
- `sp[8]` = string length
- `sp[12]` = string pointer

The function maintains an internal 644-byte static buffer that's reset when (`arg1!=0` OR `arg2==0`). It emits the IPC frame only when `(arg2+1)==arg3` AND `arg3!=0` (last attribute) — earlier calls accumulate. iter11/12 worked by accident because passing `arg3=0` triggered the legacy single-shot send path. iter13 makes 3 sequential calls with `arg2=0/1/2`, `arg3=3` → first two accumulate, third emits ONE frame containing all 3 attributes.

**transId** is NOT an argument — the function reads it from `conn[17]` automatically.

**iter13 output md5**: `56d9d8514f30a12aaf2303b7a7f6a067`. **Hardware-verified 2026-05-05**: ratio 1:1 of msg=540 to size:45 (672 each) — exactly one emit per inbound GetElementAttributes containing all three attributes. **Sonos displays Title + Artist + Album simultaneously.** First time the Y1 has ever delivered a multi-attribute AVRCP 1.3 metadata response. (`--avrcp-min` advertises AVRCP 1.3 over AVCTP 1.2; `GetElementAttributes` PDU 0x20 is the 1.3 metadata-transfer feature.)

The reverse-engineered argument layout is now empirically confirmed correct. The architectural work is done. Remaining work is pure data plumbing — replacing the hardcoded "Y1 Title"/"Y1 Artist"/"Y1 Album" strings with real metadata from Y1MediaBridge (iter14: file-based plumbing via `/data/local/tmp/y1-track-info`).

**Iter14 → 14b → 14c (data plumbing).** Y1MediaBridge writes `Title\0…Artist\0…Album\0…` (768 B fixed-layout) to a file; T4 opens, syscall-reads, and uses the strings instead of the hardcoded ones. Iter14 (`/data/local/tmp/y1-track-info`) regressed Y1MediaBridge — uid 10000 has no write permission to `/data/local/tmp/` (mode 0771 owner=shell), and the silent EACCES on `FileOutputStream` opening propagated past the IOException catch and killed the service. Iter14b moved the path to `/data/data/com.y1.mediabridge/files/y1-track-info`, with a `setExecutable(true,false)` chmod on the dir at startup so the BT process (uid bluetooth) could traverse and read. Iter14c added `__android_log_print` after `open()` to surface the fd/errno, which confirmed `T4` was firing successfully on every poll — but Sonos's display still showed first-track strings on track change. The actual diagnosis: **Sonos caches GetElementAttributes responses keyed by the TRACK_CHANGED INTERIM track_id**. Since T2 always sent `0xFF×8`, Sonos thought it was the same track forever, even though our T4 was happily delivering fresh strings.

**Iter15 — state-tracked CHANGED notifications.** Output md5 `92bcac1ab99d7fd0e263b712f9abb2d4`. Three architectural changes:

1. **File format**: y1-track-info grows to 776 B with the `mCurrentAudioId` (big-endian) at bytes 0..7 ahead of the 3 × 256 B Title/Artist/Album slots. Y1MediaBridge writes the track_id alongside the strings.
2. **State file**: a 16 B y1-trampoline-state file (mode 0666, pre-created by Y1MediaBridge at startup) lets the BT process remember (a) the last track_id we told Sonos about (bytes 0..7) and (b) the last RegisterNotification transId (byte 8). The `extended_T2` trampoline writes both fields on every RegisterNotification(TRACK_CHANGED); the `T4` trampoline reads them on every GetElementAttributes.
3. **Trampoline rewrite**: T2's logic moves out of the cramped 44-byte `classInitNative` slot into LOAD #1's page-padding region. T2 stub at 0x72d4 becomes a single `b.w extended_T2`. extended_T2 dispatches PDU/event-id internally and falls through to T4 for PDU 0x20 or to 0x65bc otherwise. T4 is rewritten cleanly (memset → open/read y1-track-info → open/read y1-trampoline-state → cmp track_id → conditionally emit `track_changed_rsp CHANGED` with state[8] as transId + write new state → 3× `get_element_attributes_rsp`). The whole blob is now built dynamically from a tiny Thumb-2 assembler in `src/patches/_thumb2asm.py` + `_iter15_trampolines.py`, rather than hand-encoded as a hex array. Total 572 bytes of trampoline + paths; LOAD #1 grows from 0xac54 → 0xae90 (still well under the 0xbc08 LOAD #2 boundary).

**Hardware-tested 2026-05-06: deadlocked Sonos.** Returning the file's real `track_id` in the INTERIM(TRACK_CHANGED) response flipped Sonos into "stable identity per track, only refresh on CHANGED" mode. Our `T4` only fires when Sonos polls `GetElementAttributes`; Sonos won't poll until it sees a `CHANGED`. After the first `RegisterNotification` (transId=0x00, track_id=0x147), Sonos went silent for 14+ minutes despite 10 track changes. Forensics confirm:

- `y1-trampoline-state` mtime 14 min before capture-end; bytes 0..7 = 0x147 (audioId 327)
- `y1-track-info` track_id at capture time = 0x151 (audioId 337) — 10 tracks ahead
- 0 inbound VENDOR_DEPENDENT commands across 60 s capture (vs 2,933 in iter14c)
- AVCTP control channel up; only PASSTHROUGH (PLAY/PAUSE) flowed
- Sonos display: "No Content" / "Unknown Content" / stale "Trouble Maker" cached from the previous iter14c session

Cause: AVRCP 1.4 §6.7.2 — peer behaviour depends critically on whether the TG advertises a stable track identity. With a real id we entered a CT/TG handshake that requires us to push asynchronous CHANGED edges, but our trampolines are reactive only.

**Iter16 — same architecture, INTERIM/CHANGED track_id pinned to 0xFF×8.** Output md5 `5d74443293f663bcd3765721bb690479`. The change-detection bookkeeping (file bytes 0..7 vs state bytes 0..7) is preserved; only the wire-level `track_id` field in the response is hardcoded to the `0xFFFFFFFFFFFFFFFF` "not bound to a particular media element" sentinel. Implementation: an 8-byte 0xFF constant labelled `sentinel_ffx8` is appended after the path strings; `extended_T2`'s INTERIM emit and `T4`'s CHANGED emit both `ADR.W r3, sentinel_ffx8` instead of computing a stack address. Trampoline blob grows 572 → 580 bytes; LOAD #1 ends at 0xae98.

**Hardware-tested 2026-05-06: iter16 protocol layer fully working.** Sonos engaged (115 inbound CMD_FRAME_INDs in 71 s, 67 RegisterNotification responses, 43 GetElementAttributes responses). Forensic dump of y1-track-info (audioId 360 = "The Kintsugi Kid (Ten Years)" / Fall Out Boy) and y1-trampoline-state (audioId 358 = "Bleed American" / Jimmy Eat World, transId=0x00) confirmed Y1MediaBridge writes the file correctly and the trampolines update state when fired. The remaining defect is **polling cadence**: Sonos polled aggressively for the iter16 capture window (UI was being viewed) but its idle poll rate is too slow for shuffle-heavy playback. State froze 2 audioIds behind reality, so display was stuck on "Bleed American" while the current track was "The Kintsugi Kid". The iter16 reactive trampolines can't push CHANGED without an inbound query — fundamentally a chicken-and-egg with Sonos's polling.

**Iter17a — proactive CHANGED via Java→JNI hook.** Output md5s libextavrcp_jni.so `37ad4394efe7686d367d08f20e6f623b`, MtkBt.odex `ca23da7a4d55365e5bcf9245a48eb675`. Adds asynchronous CHANGED emission triggered by Y1MediaBridge's existing track-change broadcast, independent of Sonos's polling rate.

  Y1MediaBridge sends `com.android.music.metachanged` → MtkBt's BluetoothAvrcpReceiver intercepts → updates internal state and calls `BTAvrcpMusicAdapter.passNotifyMsg(2, 0)` (Message what=34, arg1=2 = TRACK_CHANGED) → handleKeyMessage's sparse-switch lands at sswitch_1a3 → cardinality check `BitSet.get(2)` (Java-side bookkeeping; never populated because our JNI trampolines bypass the Java path → permanently 0) → if-eqz skips the native call.

  Patch A (`MtkBt.odex` @ 0x03c530): NOP the `if-eqz v5, :cond_184` (4 bytes `38 05 da ff` → `00 00 00 00`). The native call now fires on every track-change broadcast.

  Patch B (`libextavrcp_jni.so` @ 0x3bc0): replace `notificationTrackChangedNative`'s `stmdb` prologue with a 4-byte `b.w T5`. T5 lives in LOAD #1 padding alongside T4/extended_T2/sentinel_ffx8 and:
  1. Calls the JNI helper at 0x36c0 (same one the stock native used) to obtain the BluetoothAvrcpService per-conn struct → conn buffer at +8.
  2. Reads `y1-track-info` first 8 bytes (current track_id from Y1MediaBridge).
  3. Reads `y1-trampoline-state` 16 bytes (last-synced track_id at bytes 0..7, last RegisterNotification transId at byte 8).
  4. If the track moved since the last sync, calls `btmtk_avrcp_send_reg_notievent_track_changed_rsp` via PLT 0x3384 with `reason=CHANGED`, `transId=state[8]`, `track_id=&sentinel_ffx8` (same iter16 sentinel — keeps Sonos in poll-on-each-event mode), then writes the new track_id back to state[0..7].
  5. Returns jboolean(1).

  Trampoline blob grows 580 → 768 bytes; LOAD #1 ends at 0xaf54. The reactive T4 and extended_T2 are unchanged — iter17a layers proactive CHANGEDs on top, so we get both reactive (Sonos polls) and proactive (Y1 changes track) refresh paths.

**Iter17a hardware test (2026-05-06): proactive layer working, T4 multi-attribute regression discovered.** Capture under `/work/logs/dual-sonos-avrcp-min-iter17a/`. The proactive CHANGED path is firing — msg=544 outbound count reached 4172 over the test window vs ~30 in iter16 — confirming the Java cardinality NOP + `notificationTrackChangedNative` → T5 chain works end-to-end. But Sonos is rendering metadata field-by-field with visible flicker (Title appearing intermittently while Artist/Album swap in/out). Diagnosed from logcat: 1299 outbound msg=540 (`get_element_attributes_rsp`) for ~433 inbound `GetElementAttributes` queries — exactly 3:1 — meaning T4 is emitting *three separate msg=540 frames* per query instead of one frame containing all three attributes packed in. This is the iter12 bug that iter13 had originally fixed: T4's three calls to PLT 0x3570 had `arg2 = transId, arg3 = 0`, hitting the function's legacy `arg3 == 0 → EMIT each call` path. The dynamically-assembled T4 in `_iter15_trampolines.py` regressed it during iter15's rewrite. The reactive change-detection logic, the file I/O, the proactive CHANGED via T5 — all working. Just the response packing is wrong.

**Iter17b: T4 multi-attribute single-frame fix.** Restored iter13's calling convention in `_iter15_trampolines.py::_emit_t4`:
  - `r1 = 0` (with-string flag, accumulate)
  - `r2 = idx` (per-iteration: 0, 1, 2 — was `transId`)
  - `r3 = 3` (total attribute count — was `0`)

  The function only emits when `(arg2+1) == arg3 AND arg3 != 0`, so calls 1+2 accumulate into the internal 644-byte buffer and call 3 packs Title+Artist+Album into a single msg=540 outbound. Trampoline blob shrinks 768 → 760 B (the 4-byte `ldrb.w` to load transId becomes a 2-byte `movs r2, #imm`); LOAD #1 ends at 0xaf4c. Stock `fd2ce74db9389980b55bccf3d8f15660` → `91833d6f41021df23a8aa50999fcab9a`. The multi-attribute calling convention is documented in `docs/ARCHITECTURE.md` "Reverse-engineered semantics: btmtk_avrcp_send_get_element_attributes_rsp"; the iter17b commit message in this section's git history explains the diagnosis. Pending hardware verification.

For full architectural detail (ELF segment-extension trick, calling conventions, msg-id taxonomy, Thumb-2 encoding gotchas), see `docs/ARCHITECTURE.md`.

### Empirics + tooling for the next session

- Five gdbserver capture logs in `/work/logs/mtkbt-gdb-{getcap,passthrough,handler,narrow,drill}.log`
- Iter3 dual-capture under `--avrcp-min` post-P1 in `/work/logs/dual-sonos-avrcp-min-iter3/` — shows the first-ever `MSG_ID_BT_AVRCP_CMD_FRAME_IND` for a non-PASSTHROUGH frame plus JNI's "unknow indication" log + 9-byte hex dump.
- All gdb infrastructure (`tools/attach-mtkbt-gdb.sh`, `tools/install-gdbserver.sh`) committed and re-runnable.
- Stock libextavrcp_jni.so disassembly: `arm-linux-gnu-objdump -d -M force-thumb /work/v3.0.2/system.img.extracted/lib/libextavrcp_jni.so`. Has C++ symbols (unlike mtkbt). Function `_Z17saveRegEventSeqIdhh` is the receive loop; first 1700 bytes from `0x5ee4` cover the size-dispatch.

---

End of appendix. The brief at `/root/briefs/Innioasis_Y1_AVRCP_Unified_Brief.md` is now redundant with this document and may be deleted.

# Investigation — Final Status

This document grew organically over the 2026-05-02 session. **Read this top section first** — sections below preserve the original investigation narrative including hypotheses that were later refuted, so reading top-down without this summary is misleading.

## Final state (after all traces complete)

The shipped patch set:
- `mtkbt.patched` (12 patches: **B1-B3, C1-C3, A1, D1, E3, E4, E8, G1**) — MD5 `e2f9033eb50f10d2fc274726edb3ca75`
- `libextavrcp_jni.so.patched` (4 patches: **C2a/b, C3a/b**)
- `libextavrcp.so.patched` (1 patch: **C4**)
- `MtkBt.odex.patched` (2 patches: **F1, F2**)

Patches **E5, E7a, E7b were tested and removed** — they patched live code that was never exercised at runtime for our peer state, so they had no observable effect.

**E8 added 2026-05-02 and tested same-day as inert.** NOPing `bge #0x30688` at `0x3065e` in fn `0x3060c` (op_code=4 dispatcher slot 0) had no observable effect on cardinality:0. Inspection of the test logcat showed only msg_ids 505 and 506 received — **no GetCapabilities (`op_code=4`) ever arrives at any of the three dispatchers** (`0x3060c`, `0x30708`, `0x3096c`). The gate is upstream of the dispatcher table itself — somewhere in mtkbt's AVCTP receive path between L2CAP and the dispatcher. E8 left in place as a verified-correct patch even though inert.

**G1/G2 first attempt 2026-05-02 — reverted (mtkbt SIGSEGV at NULL).** Diagnostic instrumentation pair (G1 = Thumb wrapper hijack at `0x675c0`; G2 = ARM PLT hijack at `0xb408`) redirected `__xlog_buf_printf` → `__android_log_print`. The 12-byte thunk forwarded r2 (fmt) as both tag and fmt for android_log_print. mtkbt crashed at startup because at least one xlog callsite passes NULL in r2; bionic's `__android_log_print` at API 17 doesn't NULL-check the tag arg, so `strlen(NULL)` faulted at addr 0.

**G1 second attempt 2026-05-02 — re-shipped with NULL guard.** 20-byte Thumb thunk at `0x675c0`:

```
0x675c0:  cbz r2, .L_null     ; NULL fmt -> return, don't log
0x675c2:  movs r0, #4         ; LOG_INFO
0x675c4:  mov r1, r2          ; tag = fmt
0x675c6:  ldr.w pc, [pc, #4]  ; tail-jump via literal
0x675ca:  nop                  ; align literal at 0x675cc
0x675cc:  .word 0xaef8         ; PLT addr (ARM, bit 0 clear -> mode switch)
0x675d0:  .L_null: movs r0,#0; bx lr
```

G2 (PLT redirect at `0xb408`) was deliberately dropped this time. G1 alone covers the 2988 wrapper callsites where the `[AVRCP]/[AVCTP]` diagnostic surface lives; the 1091 direct PLT callers are lower-level kernel-side BT stack housekeeping (likely the source of the NULL-passing offenders that crashed the first attempt). If G1 also crashes (e.g., from non-NULL invalid pointers like small ints), the next iteration adds a low-memory range check (`cmp r2, #0xff; blo .L_null`).

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

The likely concrete location is in the runtime path through one of three op-code=4 dispatchers (`0x3060c`, `0x30708`, `0x3096c`) reached via the 3-slot fn-ptr table at vaddr `0xf94b0..0xf94bc`. Each has different version-check logic and reads `[conn+0x149]` (version) and `[conn+0x5d0]` (state code) differently. Which one fires for a given peer's GetCapabilities op-code depends on runtime state we cannot observe statically.

## Remaining diagnostic options

All require capabilities we don't have:
- **HCI snoop / btsnoop** — needs root.
- **Capture daemon-side `__xlog_buf_printf` traces** — Mediatek's separate log buffer, requires special tooling.
- **Runtime instrumentation patches** that emit observable side effects via existing logcat tags — possible in principle but high-effort and out of scope per the constraints established at session start.

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
- Critically: `fn@0x029e98` (the "callback dispatcher TBH" from the brief) appears at depth 2 in the walk — it's a top-level entry (0 direct callers) whose descendants include the state=1 setter. So 0x029e98 IS in the live call graph, reached from outside mtkbt as a callback.
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

Traced the callback registration mechanism for the field `[conn+0x5cc]` (the per-connection callback fn ptr the brief documented as being read at `0x02fd74` and `blx`'d to dispatch the AVRCP layer).

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

The literal `0x1439` is **not** a function address — it's a PC-relative offset. The function address is computed at runtime by `add rN, pc`. Disassembly at the resolved target `0x29e98` matches the brief's documented "callback dispatcher TBH" character-for-character (`push.w {...,lr}; tbh [pc, r3, lsl #1]`). So:

- `0x29e98` is reachable.
- The brief's analysis of its role is correct.
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
- `0x02fd34` AVRCP 1.3/1.4 init body → reached via internal `b.w` tail-call from inside the live function `0x3096c` at offset `0x030aca` (per the brief's analysis). Not registered, just a sub-path within a live function.

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
- `0x3096c`: 1 read of `[+0x149]`; **the brief's classic version-dispatch (cmp `#0x10` / `#0x20`)**

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
| `0x2fd36` (= AVRCP 1.3/1.4 init body) | 1 (at `0x2fd74`) | brief's documented site |
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
- **The brief's documented dispatchers (`0x29e98`, `0x02fd34`, `0x3096c`) are all reachable at runtime** — via PIC-style callback registration that earlier traces missed. ✓ confirmed.
- **`0x6d04a` "AV/C parser" is dead code** — multiple independent searches confirm no caller mechanism reaches it. ✓ confirmed.
- **The cardinality:0 gate is in the runtime decision tree of `[conn+0x5d0]` × `[conn+0x149]` × dispatcher-table selection**, somewhere in the `0x29e98` → `0x3060c`/`0x30708`/`0x3096c` family of paths.
- **Static analysis cannot determine which decision point fires for our peers without observing runtime values.** Every structural and addressable element has been mapped.

The remaining diagnostic options (HCI snoop / chip firmware modification / runtime instrumentation patches that emit observable side effects) are all out of scope per the constraints established at session start.

The repo (B1-B3, C1-C3, A1, D1, E3, E4, plus C2a/b, C3a/b, C4, F1, F2 across the four binaries) represents the complete set of demonstrably-effective patches reachable through static analysis. Y1MediaBridge is correctly implemented and ready to fire the moment the runtime gate releases.

## Out of Scope (eliminated)

- HCI snoop / btsnoop — no root, eliminated in earlier passes.
- mtkbt instrumentation patches (insert log calls at choke points) — possible but very high effort, low marginal value over #1 + #2.
- boot.img init scripts — won't reveal anything about the AVRCP path.

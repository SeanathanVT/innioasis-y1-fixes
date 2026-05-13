# Investigation — Final Status

This document grew organically over the 2026-05-02 / 2026-05-03 sessions. **Read this top section first** — sections below preserve the original investigation narrative including hypotheses that were later refuted, so reading top-down without this summary is misleading.

## Final state — what ships today

Wire target: AVRCP 1.3 / AVCTP 1.2 (V1+V2 SDP byte patches), implemented via a JNI-side trampoline chain in `libextavrcp_jni.so` that bypasses mtkbt's compiled-1.0 AVRCP command dispatcher. Full per-patch reference in [`PATCHES.md`](PATCHES.md); ICS Table 7 scorecard in [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md) §2.

Current shipped patches by binary:

| Binary | Patches |
|---|---|
| `mtkbt` | V1 (AVRCP 1.0→1.3 SDP byte on legacy served record), V2 (AVCTP 1.0→1.2 SDP byte), V3 (A2DP 1.0→1.3 SDP byte), V4 (AVDTP 1.0→1.3 SDP byte), V5 (AVDTP sig 0x0c TBH-table alias to sig 0x02 handler — best-effort workaround for GAVDP 1.3 ICS Acceptor row 9), V6 (internal `activeVersion` 10→14 — routes the SDP record builder to the AVRCP 1.3 served record so the wire-served record matches the F1-surfaced version), V7 (drop AVRCP 1.4 attr 0x000d Browse PSM advertisement on the AVRCP 1.3 record — swap entry slot to 0x0100 ServiceName), V8 (clear AVRCP 1.4 GroupNavigation bit 5 from SupportedFeatures byte stream so mask = 0x0001 strict 1.3), S1 (0x0311 SupportedFeatures → 0x0100 ServiceName attr-table swap on legacy record), P1 (force VENDOR_DEPENDENT through PASSTHROUGH-emit so the JNI sees the frame) |
| `libextavrcp_jni.so` | R1 (msg=519 redirect into trampoline-chain entry) + T1 / T2-stub / extended_T2 / T4 / T5 / T_charset / T_battery / T_continuation / T6 / T8 / T9 trampolines hosted in LOAD #1 page-padding extension; U1 (NOP `UI_SET_EVBIT(EV_REP)` to defang kernel auto-repeat on the AVRCP virtual keyboard) |
| `MtkBt.odex` | F1 (`getPreferVersion()`=14 unblocks 1.3+ Java dispatch), F2 (`disable()` resets `sPlayServiceInterface`), 2 cardinality NOPs (TRACK_CHANGED + PLAYBACK_STATUS_CHANGED switch arms in `BTAvrcpMusicAdapter.handleKeyMessage`) |
| `com.innioasis.y1*.apk` | A / B / C (Artist→Album navigation), E (discrete PASSTHROUGH PLAY/PAUSE/STOP/NEXT/PREV per AV/C Panel Subunit Spec), H / H′ / H″ (foreground-activity propagation of unhandled discrete media keys + framework-synthetic-repeat filter) |
| `libaudio.a2dp.default.so` | AH1 (skip `a2dp_stop` in `standby_l` so AudioFlinger silence-timeout leaves the AVDTP source stream alive across pauses) |
| `Y1MediaBridge.apk` | Installed as the metadata source / play-state-edge driver; provides `IBTAvrcpMusic` + `IMediaPlaybackService` Binders to MtkBt and writes `y1-track-info` / `y1-trampoline-state` for the trampoline chain to read |

Pre-v2.0.0 the project shipped a different set against the same binaries (B1-B3 / C1-C3 / A1 / D1 / E3 / E4 / E8 in `mtkbt`; C2a / b / C3a / b in `libextavrcp_jni.so`; C4 in `libextavrcp.so`; H1-H3 in `/sbin/adbd`) that advertised AVRCP 1.4 / AVCTP 1.3 / SupportedFeatures 0x0033 on the SDP wire but couldn't deliver on the claim — mtkbt's compiled-1.0 dispatcher NACK'd every metadata COMMAND a 1.4-class CT then sent. The `Conclusion (2026-05-04)` section below documents that pivot. The legacy patch IDs remain referenced throughout the audit trail (Traces #1 etc) but no longer exist in the shipped tree; `git log` is the authoritative byte-level archive.

## Static-analysis findings still load-bearing

- **mtkbt IS the AVRCP processor** on this device. (Earlier in this doc I hypothesized the BT chip firmware was the processor — that was wrong. The chip firmware blob is the WMT common subsystem, contains zero AVRCP code.)
- **mtkbt's documented AVRCP / AVCTP functions are NOT dead code.** Earlier scans (Trace #1 / 1b / 1c) missed the indirect-call mechanism — `0x29e98` is reached via PIC-style callback registration through `register_callback` at `0x2fecc`, called from `0x28a5e`; `0x3096c` is reached via `R_ARM_RELATIVE` relocation into a 3-slot fn-ptr table at vaddr `0xf94b0..0xf94bc`. Trace #1d / 1e / 1f / 1g resolved this. The AV/C parser at `0x6d04a` is the only function still confirmed dead.
- **`Y1MediaBridge.apk` is correctly implemented** as a dual-interface (IBTAvrcpMusic + IMediaPlaybackService) Binder bridge. The bridge + V1/V2/S1/P1 + F1/F2 + cardinality NOPs + the trampoline chain together form the user-space command-handling pipeline that mtkbt's compiled-1.0 native dispatcher cannot deliver on its own.

## The cardinality:0 gate question — resolved by the v2.0.0 pivot

Pre-v2.0.0 traces tried to find where in mtkbt's native AVRCP layer inbound REGISTER_NOTIFICATION events were being dropped (the "cardinality:0 gate") so that the legacy SDP-claim approach could deliver actual COMMANDs. Conclusion (2026-05-04, below) showed the gate could not be located within static-analysis budget. v2.0.0 pivoted to the user-space proxy approach: `P1` reroutes inbound VENDOR_DEPENDENT frames into the JNI emit path (msg=519), where the trampoline chain in `libextavrcp_jni.so` synthesises the AVRCP 1.3 responses directly — bypassing mtkbt's dispatcher entirely. Static-analysis traces on the gate (Trace #1 / 1b / 1c / 1d / 1e / 1f / 1g, plus #4 / #7) are preserved below as historical context but the question they investigated is no longer load-bearing for the shipped pipeline.

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

mtkbt links against this. It almost certainly contains the actual L2CAP send / receive primitives. The `[AVCTP] register psm` call from mtkbt resolves into this library. If the bug lives there, mtkbt is innocent and we've been chasing the wrong binary.

**Findings (2026-05-03):** see "Trace #7 — Findings" below. All four `libbluetooth*` libs are HCI / transport-only — zero AVRCP / AVCTP code. The hypothesis was wrong; mtkbt is not innocent.

### 8. Verify `/system/etc/bluetooth/` config end-state on device

`audio.conf`, `auto_pairing.conf`, `blacklist.conf` are touched by the bash flasher but the on-device final state has never been read back. Confirm the patches landed and there's no `Disable=` or similar override.

## Trace #1 — Findings (2026-05-02)

Format-string xref scan complete. All 26 `[AVCTP]`/`[AVRCP]` log strings located, every callsite mapped via `ldr+add r,pc` literal-pool resolution.

**Surprising finding:** six of eight key documented functions in mtkbt have zero static references — not direct `bl / blx` targets, not branch targets, not stored as 4-byte literals anywhere, and not computed via ADR / ADD-PC / movw-movt arithmetic visible to static scan.

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
- `mtkbt` has no AVRCP / AVCTP exports in dynsym, so `libbluetoothdrv.so` can't resolve these by name at load time.
- `libbluetoothdrv.so` itself is only 9,280 bytes and contains zero AVRCP / AVCTP strings — it's a thin shim, not the processor.

**Working hypothesis:** the AVRCP / AVCTP code visible in mtkbt is **dead code** (leftover from a prior build that had the daemon do the processing). The actual AVRCP processing is happening either inside the Bluetooth chip firmware or via a path we haven't traced yet. This would explain:

- Why no AVRCP commands ever reach the JNI dispatch socket — mtkbt isn't the dispatcher.
- Why every patch we've made to mtkbt's command path (E5, E7) had no behavioral effect — those code paths are never executed.
- Why `tg_feature:0` persists in CONNECT_CNF — mtkbt's view, but the actual TG state lives elsewhere.

This makes patches like B1-B3, C1-C3 (SDP descriptors, which mtkbt *is* responsible for serving) genuinely effective on the wire (sdptool confirms), while the runtime command-path patches are necessarily inert.

## Trace #1b — Walk back from the 12 callers of `0x028c98` (executed 2026-05-02)

Find the actual entry point of mtkbt's connection logic. The 12 callers tell us where "new connection" events come from. Tracing the call chain back finds either an internal entry (in which case the dispatcher chain *does* exist somewhere in mtkbt that we haven't traced) or a PLT call into libbluetoothdrv.so (in which case the connection event originates from outside mtkbt and our search expands to firmware / IPC).

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

Three independent signals show that several of mtkbt's documented AVCTP / AVRCP functions have no static back-reference to live code:

1. **No direct or indirect callers** for the AV/C parser, op_code dispatcher (E5 patch site), AVCTP register PSM, AVCTP RX handler, or AVCTP_ConnectRsp containing fn.
2. **No stored function pointers** to any of these addresses in `.data`, `.data.rel.ro.local`, or any literal pool in the RX segment.
3. **Not on the live call graph** that drives the connection state setters reached at runtime (state=1/3/5 sites all have real callers; the "command path" code does not).

### Initial interpretation (REVISED — see below)

I initially concluded these were **dead code** and that the BT chip firmware was the actual AVRCP TG processor, with mtkbt only managing connection lifecycle. **That conclusion is wrong**, as confirmed by inspecting the actual chip firmware on disk.

### Why the firmware-does-AVRCP claim is wrong

The Y1 BT chip is **MT6627** (combo: BT + Wi-Fi + FM + GPS, on MT6572 SoC). The firmware blob is `/etc/firmware/mt6572_82_patch_e1_0_hdr.bin`, 39,868 bytes, build dated `20130523`. Inspecting its strings reveals it is the **WMT (Wireless/MediaTek) common subsystem firmware** — sleep states, coredump, queue management, GPS desense, Wi-Fi power on/off. It contains **zero** AVRCP / AVCTP/L2CAP-level strings and no profile-stack code. Confirmed by `strings` over the blob: only chip-level housekeeping content.

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
- **What we got wrong**: removing E5 and E7 was the right operational call (they had no observable effect), but the *reason* I gave was incorrect. The real reason is most likely that **my static analysis missed the indirect-call mechanism that wires up mtkbt's AVRCP dispatcher functions to its live code path**. Trace #1c looked for a specific pattern (`add rN, pc, #imm; str rN, [rA, #imm]`) and found nothing, but there are other plausible mechanisms: function-pointer tables in `.rodata` indexed by op_code, vtable-style indirect dispatch through a struct field initialized at runtime by code I didn't trace, or a TBB / TBH-driven jump table whose target table is built dynamically.
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

The remaining cardinality:0 gate is somewhere inside mtkbt's userspace AVRCP / AVCTP code path. The previous walls all still apply (no root, no btsnoop, daemon-side logs gated to `__xlog_buf_printf`). Concrete next steps that *might* break the impasse:

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

The "dead code" framing has been wrong twice over: first I attributed the un-trackable references to chip firmware (refuted by inspecting the firmware blob), then to my own static-analysis blind spot (now refuted by finding the actual mechanism). The remaining "0-caller" functions in the AVCTP / AVRCP layer (`0x6d04a` AV/C parser, `0x6d25c` AVCTP register PSM, `0x6d9ba` AVCTP RX handler, `0x6cf30` AVCTP_ConnectRsp containing fn) are very likely registered through the same PIC-style mechanism via different `register_*` functions I haven't enumerated yet. They're not dead.

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
- `IBTAvrcpMusic$Stub` and `IBTAvrcpMusicCallback$Stub` — IPC interfaces to / from Y1MediaBridge.

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

No additional Java / smali patches will help. F1 + F2 are necessary AND sufficient on the Java side. The gate is unambiguously below.

### The honest end of the static investigation

After Trace #1f the architectural picture is finally complete and consistent:

- **`mtkbt` IS the AVRCP processor** (not chip firmware). ✓ confirmed by inspecting firmware blob.
- **The documented dispatchers (`0x29e98`, `0x02fd34`, `0x3096c`) are all reachable at runtime** — via PIC-style callback registration that earlier traces missed. ✓ confirmed.
- **`0x6d04a` "AV/C parser" is dead code** — multiple independent searches confirm no caller mechanism reaches it. ✓ confirmed.
- **The cardinality:0 gate is in the runtime decision tree of `[conn+0x5d0]` × `[conn+0x149]` × dispatcher-table selection**, somewhere in the `0x29e98` → `0x3060c`/`0x30708`/`0x3096c` family of paths.
- **Static analysis cannot determine which decision point fires for our peers without observing runtime values.** Every structural and addressable element has been mapped.

The remaining diagnostic options (HCI snoop / chip firmware modification / runtime instrumentation patches that emit observable side effects) are all out of scope per the constraints established at session start.

The repo (B1-B3, C1-C3, A1, D1, E3, E4, plus C2a / b, C3a / b, C4, F1, F2 across the four binaries) represents the complete set of demonstrably-effective patches reachable through static analysis. Y1MediaBridge is correctly implemented and ready to fire the moment the runtime gate releases.

## Trace #7 — Findings (2026-05-03): MT6572 BT lib stack is HCI-only

The four `libbluetooth*` shared objects in `/system/lib` were inspected end-to-end (sizes, dynsyms, full `strings`):

| Library | Size | MD5 | Role |
|---|---:|---|---|
| `libbluetoothdrv.so` | 9,280 | `32f1af87e46acaf1efa3f083340495cb` | Thin shim. Exports `mtk_bt_enable / disable / write / read / op` plus 8 fn-ptr objects in `.bss`. `mtk_bt_enable` does `dlopen("libbluetooth_mtk.so")` + dlsym on `bt_send_data`, `bt_receive_data`, `bt_read_nvram`, `bt_get_combo_id`, `bt_restore`, `read_comm_port`, `write_comm_port`. `mtk_bt_op` handles two opcodes only: `BT_COLD_OP_GET_ADDR` and `BT_HOT_OP_SET_FWASSERT`. |
| `libbluetooth_mtk.so` | 13,452 | — | Real driver. Exports `BT_InitDevice`, `BT_DeinitDevice`, `BT_SendHciCommand`, `BT_ReadExpectedEvent`, `GORM_Init`, `bt_send / receive_data`, `bt_read_nvram`, `bt_get_combo_id`, `bt_restore`, `read / write_comm_port`. Strings reveal it as UART transport + GORM / HCC chip-bringup commands (`Set_Local_BD_Addr`, `Set_Sleep_Timeout`, `Set_TX_Power_Offset`, `RESET`, `Set_Radio`) + NVRAM BD-address management + chip combo-id detection. Contains `bt_init_script_6572`. |
| `libbluetoothem_mtk.so` | 5,156 | — | Engineer Mode test surface (`EM_BT_read / write / init / deinit`). |
| `libbluetooth_relayer.so` | 9,252 | — | EM↔BT relayer (`bt_rx_monitor`, `bt_tx_monitor`, `RELAYER_start / exit`). |

**Combined `strings` search across all four libraries returned ZERO hits** for `avrcp`, `avctp`, `profile`, `capability`, `notif`, `metadata`, `cardinal`. They are exclusively HCI / transport — UART connection to the MT6627 chip, BD-address management from NVRAM, and chip-bringup HCC commands. Nothing above HCI.

**Implication.** The cardinality:0 gate cannot live in any userland library other than `mtkbt`. `mtkbt` does not call back through any of these libraries for AVCTP / AVRCP processing — it uses them only for HCI transport via `bt_send_data`/`bt_receive_data`. AVCTP framing, L2CAP demux, and AVRCP command dispatch all happen inside `mtkbt`'s own code segment. This narrows the search space conclusively to `mtkbt`.

This trace was deferred during 2026-05-02 work as low-priority ("almost certainly a thin shim"). Confirmed 2026-05-03; the deferral was correct but the verification was cheap and worth doing before considering root.

## Out of Scope (eliminated)

- HCI snoop / btsnoop — no root, eliminated in earlier passes.
- mtkbt instrumentation patches (insert log calls at choke points) — possible but very high effort, low marginal value over #1 + #2.
- boot.img init scripts — won't reveal anything about the AVRCP path.

---

# Conclusion (2026-05-04) — byte-patch path exhausted, proxy work needed

After the original investigation in this document concluded the gate was upstream of the op_code=4 dispatcher, post-root work in May 2026 added the diagnostic infrastructure to actually see what mtkbt and peers were doing on the wire (`@btlog` tap, `dual-capture`, `btlog-parse`, `probe-postroot` — all in `tools/` and `src/btlog-dump/`) and ran a series of byte-patch experiments to test increasingly informed hypotheses about the SDP-record shape required by working AVRCP CTs.

**The byte-patch hypothesis is conclusively dead.** Five distinct (version, features) combinations were tested:

| Configuration | SDP wire | AVCTP RX behaviour | Cardinality | PASSTHROUGH play / pause |
|---|---|---|---|---|
| Stock 1.0 + features `0x01` | `09 01 00 09 00 01` | Sonos doesn't bother sending AVRCP COMMANDs at all (no AVCTP_EVENT:4) | 0 | **WORKS** |
| `--avrcp` standard 1.4 + features `0x33` | `09 01 04 09 00 33` | Sonos sends one COMMAND, mtkbt drops silently, Sonos gives up | 0 | **broken** |
| Pixel-shape 1.5 + features `0xd1` (Browsing+MultiPlayer) | `09 01 05 09 00 d1` | Sonos tries to open AVCTP browse PSM `0x1B`, mtkbt has no listener (`+@l2cap: cannot find psm:0x1b!`), Sonos gives up | 0 | broken |
| Pixel-1.3 mimic 1.3 + features `0x01` | `09 01 03 09 00 01` | Same dropped-COMMAND failure as 1.4 | 0 | broken |
| Features-only at 1.4 + features `0x01` | `09 01 04 09 00 01` | Same | 0 | broken |

**Reference: Pixel 4 ↔ Sonos works at every AVRCP version 1.3-1.6** (per user-supplied `sdptool browse F0:5C:77:E4:30:62` outputs at each Developer-Options-forced version, captured 2026-05-04). The Pixel at 1.3 advertises features `0x0001` — *the same value Y1 stock advertises* — and Sonos receives full title / artist / album metadata + responds correctly to `PASS_THROUGH` play / pause. The difference is not the SDP advertisement. It is mtkbt's command-handling layer.

**mtkbt is internally an AVRCP 1.0 implementation.** Compile-time string `[AVRCP] AVRCP V10 compiled` + runtime log `AVRCP register activeVersion:10` are accurate. The opcode dispatchers identified earlier in this document (`0x3060c`, `0x30708`, `0x3096c` at op_code=4 = `GetCapabilities`) exist in the binary, but no inbound packet from any peer ever reaches them, regardless of how we shape the SDP record. The earlier "the gate is upstream of the dispatcher table" framing was correct; the missing piece was that there is no upstream gate that byte-patches can flip — mtkbt's AVCTP RX simply does not classify AVRCP COMMAND PDUs as anything its 1.0 dispatcher recognises, and silently drops them.

The previously-listed primary lead, `MSG_ID_BT_AVRCP_CONNECT_CNF result:4096`, was also disproven during this work: the same `0x1000` value is emitted at `MSG_ID_BT_AVRCP_ACTIVATE_CNF` time 3 ms after the JNI sends `ACTIVATE_REQ`, before any peer is involved. `0x1000` is mtkbt's standard "request acknowledged" status code, not a peer-feedback or "feature degraded" indicator.

## Repo state after the conclusion (commits 2690d05 → 7077b5a → bd36160 → this one)

- `--avrcp` is now a known-broken opt-in. It runs if explicitly requested (useful for the proxy work below) and prints a startup warning. **Excluded from `--all`.**
- `--bluetooth` no longer sets `persist.bluetooth.avrcpversion=avrcp14`. The remaining audio.conf / `auto_pairing.conf` / `blacklist.conf` / `ro.bluetooth.class` / `ro.bluetooth.profiles.*.enabled` properties are pairing-essential and stay.
- The recommended baseline is `--all` (without `--avrcp`): pairing works, A2DP audio works, AVRCP 1.0 PASSTHROUGH (play / pause / skip) works, no metadata over BT.
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

Trigger the failure scenario (Y1 ↔ Sonos with `--avrcp` on so peer engages enough to send a COMMAND). Whichever breakpoint fires when the single `AVCTP_EVENT:4` arrives is the candidate drop site. Dump the inbound packet bytes from r0 / r1 / stack at that point and confirm they're a real AVRCP COMMAND PDU (op_code 0x4 = GetCapabilities is the most likely first command).

`tools/probe-postroot.sh` §11 confirmed SELinux is absent on this firmware and §12 confirmed `/proc/sys/kernel/yama/ptrace_scope` doesn't exist either, so ptrace attach is unblocked.

### Phase 2 — Patch a trampoline (~3-5 days)

At the identified drop site, replace the silent-drop branch with a `bl <trampoline>`. The trampoline (in a code-cave or appended to mtkbt's `.text`) marshals the inbound packet into a new IPC message — e.g., msg_id 999 — and writes it to the existing `bt.ext.adp.avrcp` abstract socket that already carries msg_ids JNI↔mtkbt. The IPC framing and the existing send wrapper at vaddr `0x511c0` are documented in the appendix below.

Verification: `tools/btlog-parse.py` should now show the AVRCP COMMAND bytes flowing through the new msg_id; logcat should show the JNI receiving msg_id 999 (or whatever ID we pick).

### Phase 3 — Java AVRCP COMMAND parser / responder (~1-2 weeks)

Extend `Y1MediaBridge` (or add a sibling Java component) to:
1. Receive the new msg_id from the JNI via the existing Binder path.
2. Parse the AVCTP+AVRCP frame: AV/C control header, op_code, PDU ID, transId, params.
3. Build the appropriate AVRCP RSP for at minimum:
   - `GetCapabilities` (PDU `0x10`)
   - `RegisterNotification` (PDU `0x31`) for `EVENT_TRACK_CHANGED` (`0x05`) and `EVENT_PLAYBACK_STATUS_CHANGED` (`0x01`)
   - `GetPlayStatus` (PDU `0x30`)
   - `GetElementAttributes` (PDU `0x20`)
4. Use the existing `IBTAvrcpMusic` / `IBTAvrcpMusicCallback` plumbing for the actual track / state data — Y1MediaBridge already sources this from the music player via broadcast intents and RCC.

`PASS_THROUGH` (op_code `0x7C`) commands should pass through to the existing 1.0 path so play / pause keeps working — don't intercept those.

### Phase 4 — Outbound RSP path (~3-5 days)

Patch a second trampoline (or extend the first) that takes a Java-built AVRCP RSP frame, marshals it into an outbound msg_id, and routes it through mtkbt's existing AVCTP TX path so it reaches the peer's AVCTP channel. The IPC dispatcher map in the appendix below (msg_ids 500-611, second TBH at vaddr `0x518ac`) names the candidate slots.

### Verification target

`tools/dual-capture.sh` against Sonos should show:
- `cardinality:N` non-zero in `MMI_AVRCP: ACTION_REG_NOTIFY for notifyChange ... cardinality:N`
- `MMI_AVRCP: registerNotificationInd eventId:` firing for events 1/2/9
- Sonos app showing title / artist / album for the currently-playing track
- Y1MediaBridge log lines `notifyAvrcpCallbacks code=N targets=>=1` (currently always logs `targets=0` because MtkBt never registers a callback — the proxy work fixes this by routing peer-side `RegisterNotification` through Java)
- Physical play / pause from Sonos still working (PASSTHROUGH path unbroken)

### Known prerequisites for the next agent

- Read this entire document top-down — the failure modes earlier in the doc (G1 / G2 SIGSEGV at NULL, blanket xlog redirect being too fragile, etc.) are real and re-tripping them wastes days.
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
[Car Stereo CT] <--SDP / AVRCP--> [mtkbt daemon] <--socket bt.ext.adp.avrcp--> [libextavrcp.so]
                                       |                                           ^
                                       | HCI / UART                     [libextavrcp_jni.so]
                                       v                                           ^
                                  [/dev/stpbt]                            [MtkBt.apk Java layer]
                                       |
                                       v
                              [mtk_stp_bt.ko (kernel)]
                                       |
                                       v
                                [MT6627 chip — HCI / radio only]
```

The socket `bt.ext.adp.avrcp` lives in `ANDROID_SOCKET_NAMESPACE_ABSTRACT` (namespace=0). Abstract sockets have no filesystem file and are auto-released on FD close; no stale socket is possible across BT toggle cycles.

**Trace #7 confirmed** all four `libbluetooth*.so` libs (`libbluetoothdrv.so`, `libbluetooth_mtk.so`, `libbluetoothem_mtk.so`, `libbluetooth_relayer.so`) are HCI / transport-only. Combined `strings` search returned zero hits for `avrcp / avctp / profile / capability / notif / metadata / cardinal`. The cardinality:0 gate cannot live anywhere except inside `mtkbt`.

Additionally, mtkbt exposes an undocumented `SOCK_STREAM` listener at the abstract socket `@btlog` (created by `socket_local_server("btlog", ABSTRACT, SOCK_STREAM)` at vaddr `0x6b4d4`). Connecting to it as root yields a stream of mtkbt's `__xlog_buf_printf` output **plus** decoded HCI command / event traffic — the diagnostic capability used by `tools/dual-capture.sh` and Trace #9. See `src/btlog-dump/README.md` for the framing format.

## The legacy 11-patch `--avrcp` byte-patch set — DELETED in v2.0.0

The pre-v2.0.0 `--avrcp` flag shipped 11 byte patches against `mtkbt` (B1-B3 AVCTP version, C1-C3 AVRCP version, A1 runtime SDP MOVW, D1 registration-guard NOP, E3/E4 SupportedFeatures, E8 op_code=4 dispatcher gate) plus 4 against `libextavrcp_jni.so` (C2a/b/C3a/b) plus 1 against `libextavrcp.so` (C4 version constant). All advertised AVRCP 1.4 / AVCTP 1.3 / SupportedFeatures 0x0033 on the wire (sdptool-confirmed) but mtkbt's compiled-1.0 command-handling layer NACK'd every metadata COMMAND that 1.4 controllers then sent — net regression vs stock 1.0 PASSTHROUGH.

The Browsing-bit experiment (Trace #11) and Pixel-shape experiment (set features `0xd1` including Browsing + Multi-Player) closed the question: when we advertised Browse, Sonos opened browse PSM `0x1B`, mtkbt's L2CAP rejected (`+@l2cap: cannot find psm:0x1b!`), and Sonos gave up on AVRCP altogether. Since v2.0.0 the served record advertises AVRCP 1.3 / AVCTP 1.2 (V1+V2) with no 0x000d AdditionalProtocolDescriptorList — see [`PATCHES.md`](PATCHES.md) for the current shipped set.

Several reverted-during-development entries (E1, E2, E5, E7a/b state-gate / op_code-dispatcher NOPs; G1 / G2 xlog-redirect thunks that broke BT init) were closed mid-stream during the legacy era and don't survive in the current tree either. Conclusion specifically for the xlog-redirect line of work: blanket redirect at the consolidated wrapper at vaddr `0x675c0` is too fragile (hit ~3000 times in mtkbt's lifecycle including very early init). The `@btlog` passive tap from Trace #9 supersedes the read-only-observation need entirely; behavioural instrumentation, if ever needed, must be surgical (hardcoded tag / fmt strings via a trampoline at a small number of high-value sites).

Byte-level offsets and tables for any of these patches: `git log --all -- src/patches/patch_mtkbt.py src/patches/patch_libextavrcp_jni.py src/patches/patch_libextavrcp.so` covers the full edit history through the v2.0.0 deletion commit.

## adbd Root Patches (H1 / H2 / H3) — Closed 2026-05-03 (failed on hardware), superseded by setuid `/system/xbin/su`

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

**Why arg-zero, not NOP-the-blx (history).** An earlier revision NOPed the three `blx` calls outright. **On hardware**, however, `adb shell` and `adb root` both returned "device offline" — adbd starts and the USB endpoint enumerates, but the ADB protocol handshake never completes. The bionic setuid wrapper at `0x19418` does `bl 0x27b30` *before* reaching the actual syscall stub, doing capability bounding-set / thread-credential bookkeeping that downstream adbd code depends on. NOPing the call entirely skips that bookkeeping → process is uid 0 nominally but has inconsistent credentials / capabilities → the USB ADB protocol layer never fully initializes. The arg-zero revision keeps every syscall and every bionic wrapper intact; `setuid(0)` when EUID is already 0 is a no-op that runs all the same bookkeeping. Same for `setgid(0)`. `setgroups(0, _)` clears supplementary groups, which is the desired end state anyway. **Even so, arg-zero ALSO failed on hardware** ("device offline"); root cause never fully diagnosed because losing ADB makes diagnosis circular.

`patch_bootimg.py` extracted `/sbin/adbd` from the boot.img ramdisk cpio in-place, applied H1 / H2 / H3 via `patch_adbd.patch_bytes()`, and wrote it back. Same file size (223,132 bytes) so cpio record offsets are unchanged.

## Root via setuid `/system/xbin/su` — v1.8.0 (verified on hardware 2026-05-04)

> **Status: hardware-verified 2026-05-04.** First flash + `adb shell` → `su` → `id` returned `uid=0(root) gid=0(root)`. Replaces the failed H1 / H2 / H3 adbd byte-patch path; got us out of the "patched adbd is broken / can't even diagnose because we just broke ADB" trap.
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
- **No supply chain beyond GCC + this source.** No SuperSU / Magisk / phh-style binary imported, no manager APK, no whitelist.
- **Build via `cd src/su && make`.** Output: 892-byte statically-linked ARMv7 ELF, soft-float, EABI v5, no `NEEDED` entries. Output MD5 (current): `a87dc616085e1a0e905692a628e747e7`.

The bash patcher's `--root` flag does:

```
sudo install -m 06755 -o root -g root src/su/build/su /mnt/y1-devel/xbin/su
```

against the mounted system.img. No boot.img extraction, no ramdisk repack, no `/sbin/adbd` byte-patches.

### Trade-offs

- **Anyone who can exec `/system/xbin/su` becomes root.** No permission-prompt UI, no whitelist. Acceptable for a single-user research device. Not appropriate for a consumer ROM.
- The binary is intentionally tiny + direct so every byte is auditable. Statically linked means a future bionic mismatch can't brick the escalator.

### Why this should work where H1 / H2 / H3 didn't

The H1 / H2 / H3 failure mode was: patched `/sbin/adbd` got into a state where ADB protocol initialization failed, and once you've shipped a broken adbd you can't diagnose what broke it (you've lost ADB). The `su` install touches NOTHING in the boot path — adbd, init, ramdisk, even `default.prop` are all stock. If `/system/xbin/su` somehow doesn't work post-flash, ADB still works fine; we can pull `/system/xbin/su` and check what's wrong (perms? mode bits? signing? wrong arch?) without losing visibility.

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

This section's table previously enumerated the legacy 11-patch `--avrcp` set against `mtkbt` plus the C2a/b/C3a/b in `libextavrcp_jni.so` plus C4 in `libextavrcp.so` plus H1-H3 in `/sbin/adbd` — every entry of which has since been deleted (the legacy `--avrcp` set in v2.0.0; H1-H3 in v1.7.0, then `patch_adbd.py` / `patch_bootimg.py` deleted in v2.1.0). The current shipped patch set is in [`PATCHES.md`](PATCHES.md) Patch ID Legend. F1 and F2 against `MtkBt.odex` survive into the current tree (with current docstrings reflecting the 1.3 wire shape, not the legacy 1.4 framing) — see [`PATCHES.md`](PATCHES.md) §`patch_mtkbt_odex.py`.

## Binary Reference Data

Stock MD5s and structural reference for every binary the patcher chain touches. Output (patched) MD5s are not pinned here — each `src/patches/patch_*.py` carries its own `STOCK_MD5` + `OUTPUT_MD5` constants and updates them in lockstep with the patch logic; that's the authoritative source.

### `mtkbt`

| Property | Value |
|---|---|
| Stock MD5 | `3af1d4ad8f955038186696950430ffda` |
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
| Format | ODEX `dey\n036\0`, embedded DEX `dex\n035\0` at offset `0x28` |

### `libextavrcp_jni.so`

| Property | Value |
|---|---|
| Stock MD5 | `fd2ce74db9389980b55bccf3d8f15660` |
| Format | ELF32 LE ARM, ET_DYN, base `0x00000000` |
| Global `g_tg_feature` | `0xD29C` |
| Global `g_ct_feature` | `0xD004` |
| CONNECT_CNF handler | `0x62EA` (msg_id=505, TBH index=4) |
| connect_ind handler | `0x619C` (msg_id=506, TBH index=5) |
| `getCapabilitiesRspNative` | `0x5DE8` (FUN_005de8) |
| `activateConfig_3req` | `0x375C` |

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
| File size | 17,552 bytes |
| `btmtk_avrcp_send_activate_req` | `0x19CC` |
| `AVRCP_SendMessage` | `0x18EC` |

(`libextavrcp.so` carried the legacy C4 patch through v1.x; deleted in v2.0.0. Stock now ships unmodified.)

### `libaudio.a2dp.default.so`

| Property | Value |
|---|---|
| Stock MD5 | `0d909a0bcf7972d6e5d69a1704d35d1f` |
| File size | 58,660 bytes |
| Format | ELF32 LE ARM, ET_DYN |
| `A2dpAudioStreamOut::standby_l` | `0x8654` (AH1 patch site at file offset `0x000086ab`) |
| `A2dpAudioStreamOut::standby` | `0x86c0` |
| `A2dpAudioStreamOut::setSuspended(bool)` | `0x8958` |

(The legacy `/sbin/adbd` Binary Reference Data subsection — Stock + arg-zero + NOP-the-blx Patched MD5s — was removed when `patch_adbd.py` / `patch_bootimg.py` were deleted in v2.1.0. See "adbd Root Patches (H1 / H2 / H3)" earlier in this doc for the historical analysis. Current root mechanism is `/system/xbin/su` per `src/su/`.)

## Eliminated Paths — Do Not Pursue

| Path | Why eliminated |
|---|---|
| Patching record [13] blob alone | Not the served ProfileDescList — record [18] overrides via last-wins. |
| Old patches #2 / #3 as "read-back only" | Both target live ProfileDescList minor-version bytes — superseded by C1 / C2 at 1.4. |
| Patching 0xeba1d / 0xeba4e (legacy claim) | Unrelated bytes; 0x0311 IS registered in all three groups. |
| Descriptor table flags / ptr patches (0x0f97b2) | `flags` = element size, not control bit. |
| FUN_00022cec MOVW cluster (0x00012d7c, 0x00012d84) | Not on any SDP path. |
| `ldrb.w` intercept at 0x0000ead4 | FUN_000108d0 ignores its r1 parameter. |
| Version sink at FUN_000afd60 (0x000afd6a) | Downstream of SDP record construction. |
| Code caves in RX segment | All null blocks are live SDP / string data. |
| Code caves in `.data` | RW- segment — non-executable; causes BT crash. |
| BSS caves | No file bytes; loader zeroes before execution. |
| **E1** `0x29be4` BNE.W→NOP | State gate is intentional; bypass caused unsolicited responses → car disconnect. **Reverted 2026-05-01.** |
| **E2** `0x0309ec` BNE→NOP | Branch routes 1.3/1.4 cars to *correct* count=4 path; NOP'ing it bypassed init. **Reverted 2026-05-01.** |
| **E5 / E7a / E7b** | Empirically inert across all three peers; Trace #1f confirmed the patched functions ARE reachable via PIC callback registration, but the patched code paths are not exercised at runtime for our peer state. **Removed 2026-05-02.** |
| **G1 / G2** xlog→logcat redirect | Crashed mtkbt at NULL fmt; even with NULL guard, BT framework couldn't enable. **Reverted 2026-05-03.** Path closed within current constraints. |
| `__xlog_buf_printf` capture without root | Special MTK tooling required. **Superseded by `@btlog` passive tap (Trace #9, requires root).** |
| Property-only adbd root via `default.prop` | OEM adbd has stripped the standard `should_drop_privileges()` gating; `ro.secure=0` is inert (confirmed empirically 2026-05-03 — `adb shell id` returned `uid=2000(shell)` with all properties correctly set). |
| H1 / H2 / H3 binary patches in `/sbin/adbd` (NOP-the-blx and arg-zero revisions) | **Tried 2026-05-03; both caused "device offline" on hardware.** Static analysis found no `getuid()` gate, no uid==2000 compare; the failure mode is something we can't see without on-device visibility (which we lose the moment we ship a broken adbd). `--root` flag removed from the bash in v1.7.0; **superseded 2026-05-03 (v1.8.0) by the setuid `/system/xbin/su` install** which leaves `/sbin/adbd` untouched. |
| `AttrID 0x0311` SupportedFeatures via SDP response | Initial claim "not registered" was incorrect — IS registered in all three groups. E3 / E4 patches the served value. |
| IBTAvrcpMusic / binder dispatch | Not the gate (Trace #4 ruled out the Java layer). |
| HCI snoop (`persist.bt.virtualsniff`) | Breaks BT init. **Superseded by `@btlog` passive tap (Trace #9).** |
| Chip firmware (`mt6572_82_patch_e1_0_hdr.bin`) | WMT common subsystem only — sleep / coredump / queue / GPS / Wi-Fi power. Zero AVRCP code. |
| `libbluetooth*.so` libs (Trace #7) | All four libs are HCI / transport-only — UART link to MT6627, GORM / HCC chip-bringup, NVRAM BD-address management. Zero hits for `avrcp / avctp / profile / capability / notif / metadata / cardinal`. mtkbt is the AVRCP processor. |
| `0x6d04a` AV/C parser as patch site | Confirmed dead code via multiple independent searches (no callers via any mechanism). |
| Java-side patches beyond F1 / F2 (Trace #4) | Java initializes correctly for AVRCP 1.4; no version gate or capability check would suppress events when they DO arrive. |
| **Browsing-bit experiment** (E3 / E4 `0x33 → 0x73`) | Landed on the wire (sdptool confirmed `0x0073`); peer behaviour identical to baseline. **Disproven 2026-05-04.** Tooling deleted. |
| **Pixel-shape experiment** (B / C bumped to AVCTP 1.4 + AVRCP 1.5; E3 / E4 `0x33 → 0xd1` Cat1+PAS+Browsing+MultiPlayer) | Landed on the wire; peer (Sonos) tried to open AVCTP browse PSM `0x1B`; mtkbt has no L2CAP listener for that PSM (`+@l2cap: cannot find psm:0x1b!`); peer gave up. **Disproven 2026-05-04.** Tooling deleted. |
| **Pixel-1.3 mimicry experiment** (B / C dropped to AVCTP 1.2 + AVRCP 1.3; E3 / E4 → 0x01; A1 / F1 reverted) | Landed on the wire; peer (Sonos) sent one AVRCP COMMAND (AVCTP_EVENT:4 with transId:0); mtkbt dropped silently; peer gave up. **Disproven 2026-05-04.** Tooling deleted. |
| **Features-only experiment** (E3 / E4 `0x33 → 0x01` keeping AVRCP 1.4) | Same dropped-COMMAND failure as Pixel-1.3 mimic. **Disproven 2026-05-04.** Tooling deleted. |
| **Y1MediaBridge actively interfering** | Bridge-disable test 2026-05-04 confirmed bridge is innocent: same failure mode with bridge present (`mbPlayServiceInterface=true`) or disabled (`mbPlayServiceInterface=false`). The 1.4-version push comes from F1 (in odex) + B / C / E patches, not from the bridge. Bridge implements `IBTAvrcpMusic` correctly via raw `onTransact` dispatch — but MtkBt's `BTAvrcpMusicAdapter` never calls `registerCallback` against it because no peer-side AVRCP COMMAND ever reaches MtkBt to trigger the call. Bridge stays idle as a downstream consequence of the upstream silence. |

## Post-Flash Verification Checklist

After `apply.bash --avrcp [other flags]` lands on the device, sanity-check from a host with `adb shell`:

- **SDP record shape** — `sdptool browse <Y1_BT_ADDR>` from a paired peer:
  - AVRCP TG record (UUID `0x110c`): `AV Remote (0x110e) Version: 0x0103` (V1) and `AVCTP uint16: 0x0102` (V2)
  - Attribute `0x0100` ServiceName "Advanced Audio" present (S1); attribute `0x0311` SupportedFeatures absent (S1 swap)
- **Patcher output MD5 ↔ on-device MD5** — pull each patched binary and compare against the `OUTPUT_MD5` constant pinned in the corresponding patcher:
  - `mtkbt` → `src/patches/patch_mtkbt.py::OUTPUT_MD5`
  - `libextavrcp_jni.so` → `src/patches/patch_libextavrcp_jni.py::OUTPUT_MD5` (regenerated when the trampoline blob changes)
  - `MtkBt.odex` → `src/patches/patch_mtkbt_odex.py::OUTPUT_MD5`
  - `libaudio.a2dp.default.so` → `src/patches/patch_libaudio_a2dp.py::OUTPUT_MD5`
- **Y1MediaBridge installed and running** — `dumpsys package com.y1.mediabridge | grep versionCode` matches `src/Y1MediaBridge/app/build.gradle`; `ps | grep com.y1.mediabridge` shows the service.
- **Trampoline chain emitting metadata** — `tools/dual-capture.sh` against a peer CT exercising play/pause + metadata fetch; in the resulting btlog look for outbound `msg=540` (GetElementAttributes response) frames carrying the seven §5.3.4 attributes after a CT-side metadata query.
- **AVRCP NACKs absent** — same capture, count of inbound `msg=520` (NOT_IMPLEMENTED) reject frames should be zero (or scoped to the explicit T_continuation reject for unsolicited 0x40 / 0x41).
- **AH1 holding A2DP up across pauses** — pause + wait ≥3 s + resume from peer; capture should show zero `[A2DP] a2dp_stop. is_streaming:1` lines around the pause/resume window.
- **Root works** — `adb shell` → `su` → `id` returns `uid=0(root) gid=0(root)`; prompt `$`→`#`.

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

- **HCI command / event traffic** — fully decoded: `HCC_INQUIRY`, `HCC_CREATE_CONNECTION`, `HCC_WRITE_SCAN_ENABLE`, `HCC_AUTH_REQ`, `HCC_READ_REMOTE_FEATURES`, `HCC_READ_REMOTE_VERSION`, `HCC_READ_REMOTE_EXT_FEATURES`, `HCE_COMMAND_COMPLETE`, `HCE_READ_REMOTE_FEATURES_COMPLETE`, `HCE_READ_REMOTE_VERSION_COMPLETE`, `[BT]GetByte:` / `[BT]PutByte:` byte-level transport.
- **`__xlog_buf_printf` output** — every `[AVRCP]…`, `[AVCTP]…`, `[L2CAP]…`, `[ME]…`, `[BT]…`, `SdpUuidCmp:…`, `ConnManager: event=…` log line that's invisible to logcat.

**Framing format (preliminary, by inspection):**

| Bytes | Field |
|---|---|
| 1 | Start marker `0x55` ('U') |
| 1 | Always `0x00` (separator / flag?) |
| 1 | Frame length |
| 2 | Sequence ID (alphabetic, increments — `bl`, `bm`, `bn`, …) |
| 1 | Severity / category (`0x12` for xlog text, `0xb4` for HCI snoop) |
| 1 | `0x00` pad |
| body[0..1]   | Often constant `00 e5` |
| body[2..6]   | Timestamp (`u32` LE; monotonic per process lifetime, **separate domains per severity**) |
| body[6..10]  | Zero / flag bytes |
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

- Sonos Roam (`38:42:0B:38:A3:3E`) initiates the connection 22 s after the JNI activate completes — likely after Sonos's own scan / discover cycle.
- L2CAP / AVCTP come up cleanly: 3× `l2cap conn_rsp result:0`, 7× `handleconfigrsp result:0` on `psm:0x19`, then `[AVCTP] chid:66` (channel ID varies between captures — was `0x67` in Trace #9).
- AVRCP profile-level connect succeeds end-to-end: `connect_ind` (msg 506) → `CONNECT_RSP` (msg 507) → `CONNECT_CNF` (msg 505).
- After the connect, **only one `AVCTP_EVENT:4` (RECV_DATA-class event) fires from the peer**, accompanied by `[AVRCP] transId:0`, then **silence** — no further AVCTP RX activity, no `GetCapabilities`, no `RegisterNotification`. Sonos is not following up the basic AVRCP-profile connect with the AVRCP COMMAND PDUs a 1.4 controller should send.
- The Y1 stays in this connected-but-silent state indefinitely until A2DP drops, at which point mtkbt cleans up via `AVRCP: disconnect because a2dp is lost`.
- Java-side `cardinality:0` in `ACTION_REG_NOTIFY` lines is exactly what we'd expect from this state — `mRegBit` is empty because no peer has issued REGISTER_NOTIFICATION.

## Trace #11 (2026-05-04, post-root) — Browsing-bit experiment failed, real-world reference peer comparisons settle the gate-location question

Three independent threads, one conclusion.

### Thread A: Browsing-bit experiment

Hypothesis: served `SupportedFeatures = 0x0033` omits Browsing bit (`0x40`); some 1.4 controllers may decline AVRCP COMMANDs against a TG that doesn't claim Browsing.

Built a non-destructive bash wrapper that swaps `src/patches/patch_mtkbt.py` for an alternate that overrides E3 / E4 `after` bytes from `0x33` → `0x73`, runs the standard `--avrcp --bluetooth` flow, then restores the original on EXIT. Flashed and re-captured against Sonos.

Direct evidence the experiment landed on the wire: btlog `SdpUuidCmp:uuid1, len=2, (11  e,  9  1,  4  9,  0 73)` — the served bytes for AVRCP TG (`0x110e`) are now `Version=0x0104` + `SupportedFeatures=0x0073`. Compare to Trace #9's `0x0033`.

**Result: peer behaviour identical to baseline.** 14× `cardinality:0` lines, none non-zero. Same single `[AVRCP] avctpCB AVCTP_EVENT:4` → `[AVRCP] transId:0` → silence. Same `MSG_ID_BT_AVRCP_CONNECT_CNF result:4096 bws:0 tg_feature:0 ct_featuer:0`. L2CAP / AVCTP config exchange clean.

**Hypothesis #1 dead.** Tooling deleted on cleanup.

### Thread B: hypothesis-#3 static (`[AVRCP] transId:0`)

Two callers of the `[AVRCP] transId:%d` log function (`0x11374` static / `0x400d2374` live). Both read transId directly from inbound packet bytes (`event[1]` in caller `0x1457c..0x1458a`; `event[5]` in caller `0x51a20`). The `transId:0` we observe is **the actual transId byte the peer sent on the wire** — not mtkbt mangling the value. transId is a 4-bit AVCTP-header field; `0` is a perfectly valid value for the first packet on a fresh AVCTP channel. **Hypothesis #3 dead.**

Bonus from the same pass: `@btlog`'s `[BT]GetByte:` / `[BT]PutByte:` lines around the AVCTP_EVENT:4 timestamp give a per-byte HCI trace. Decoded, mtkbt sends an outbound L2CAP CONFIG_REQ; Sonos sends back its own CONFIG_REQ for our `cid 0x42` carrying MTU=1024 — standard AVCTP control-channel config. Then AVCTP_EVENT:4 fires once and AVRCP-layer activity stops.

### Thread C: real-world reference-peer comparisons (the decisive evidence)

User-supplied empirical data:

| Test | AVRCP works? | Implication |
|---|---|---|
| Pixel 4 (TG) ↔ Sonos Roam (CT), Sonos app shows now-playing metadata | ✅ | Sonos *is* a real working 1.4 controller |
| Y1 (TG) ↔ Sonos Roam (CT), our captures | ❌ | Y1's TG is broken |
| Y1 (TG) ↔ car head unit (CT) | ❌ — no metadata, **play / pause broken** | Y1's TG is broken end-to-end on the actual goal device (cars are the project's primary AVRCP target per the README's history) |

**The play / pause break is the load-bearing finding.** Play / pause flows car→Y1 as AVRCP `PASS_THROUGH` commands. Functional break in the CT→TG command path — not just notification-cosmetic. Same root cause as cardinality:0.

### Combined verdict

**The gate is on the Y1 side**, not on any peer. Sonos's "single AVCTP_EVENT:4 then silence" pattern is Sonos sending its first AVRCP command (likely `GetCapabilities`), getting nothing usable back from Y1, and giving up.

**Pixel 4 SDP record across all four AVRCP versions (Pixel Developer-Options-forced, captured 2026-05-04):**

| Attribute | Pixel-1.3 | Pixel-1.4 | Pixel-1.5 | Pixel-1.6 |
|---|---|---|---|---|
| 0x0004 AVCTP version | `0x0102` (1.2) | `0x0103` (1.3) | `0x0104` (1.4) | `0x0104` (1.4) |
| 0x0009 AVRCP version | `0x0103` | `0x0104` | `0x0105` | `0x0106` |
| 0x000d AdditionalProtocolDescList | **MISSING** | PSM `0x001b` AVCTP 1.3 | PSM `0x001b` AVCTP 1.4 | PSM `0x001b` AVCTP 1.4 + OBEX (Cover Art) |
| 0x0311 SupportedFeatures | **`0x0001`** (Cat1 only!) | `0x00d1` | `0x00d1` | `0x01d1` (extra bit 8) |

User-confirmed: at every Pixel-AVRCP-version setting (1.3 / 1.4 / 1.5 / 1.6), Sonos receives full title / artist / album metadata + responds correctly to play / pause from Pixel. Cover art doesn't transfer (Sonos-side limitation).

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

The candidate next patch is at file `0x6526` of `libextavrcp_jni.so`: `cmp.w lr, #8` → `cmp.w lr, #9` (single byte 0x08 → 0x09). That'd route size-9 frames into the size-8 branch and onward to the BT-SIG vendor check at `0x656a`. Risk: the size-8 branch's downstream reads (sp+381, sp+382, sp+385) assume a specific stack layout that size-9 frames may not satisfy, AND the path eventually calls `btmtk_avrcp_send_pass_through_rsp` which is the wrong response builder for a VENDOR_DEPENDENT command. May need additional patches to skip the pass_through_rsp call and / or to invoke Java's `BTAvrcpMusicAdapter.checkCapability()` via JNI.

A clean patch will require static-analyzing what `0x65a4+` actually does (whether it reaches Java or just logs+returns) before committing to a byte rewrite.

**2026-05-05 follow-up.** The single-byte J1 (cmp 8 → 9) was tried and rolled back — it routed size-9 frames through the PASSTHROUGH dispatch, generating fake `key=1 isPress=0` events and never reaching Java. Path forward (now in `patch_libextavrcp_jni.py`) is **trampoline T1**: redirect `bne.n 0x65bc` at file 0x6538 to a code-cave at file 0x7308 (overwriting the unused JNI debug method `testparmnum`). The trampoline checks the PDU byte at sp+382, and on `0x10` (GetCapabilities) calls `btmtk_avrcp_send_get_capabilities_rsp` directly via PLT 0x35dc, then exits.

**Iter5 capture (2026-05-05) — T1 confirmed working.** `/work/logs/dual-sonos-avrcp-min-iter5/` shows: 1 size:9 inbound (GetCapabilities) → 1 outbound msg=522 (size 30, the response) → 4 size:13 inbound (Sonos's first-ever follow-up VENDOR_DEPENDENT commands, 2-second retry pattern indicating RegisterNotification with no INTERIM ACK). For comparison, iter4 (J1) had the same size:9 inbound but msg=520 NOT_IMPLEMENTED instead of msg=522, and zero size:13 follow-ups — Sonos gave up. T1 is the first patch that gets Sonos past the GetCapabilities gate.

**T2 added 2026-05-05.** Trampoline T2 at file 0x72d0 (overwriting unused `classInitNative` debug method) handles inbound RegisterNotification(EVENT_TRACK_CHANGED). T1's fall-through arm (originally `b.w 0x65bc`) now bridges to T2 stage 2 at 0x72d4. T2 verifies the PDU is 0x31 and event_id is 0x02, then calls `btmtk_avrcp_send_reg_notievent_track_changed_rsp` (PLT 0x3384) with INTERIM (reasonCode 0x0F) and track_id = 0xFFFFFFFFFFFFFFFF ("no track"). Other registered events (0x01, 0x09, 0x0a, 0x0b) fall through to the original "unknow indication". T3/T4/T5/T6/T8/T9 follow-ups are now live in `src/patches/_trampolines.py`; see [`ARCHITECTURE.md`](ARCHITECTURE.md) for the current trampoline chain and [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md) for the current spec-coverage state.

**Iter6 capture (2026-05-05) — T2 confirmed working.** `/work/logs/dual-sonos-avrcp-min-iter6/` shows: 1× size:9 → 1× msg=522 (T1 GetCapabilities response, same as iter5); 5× size:13 inbound (RegisterNotification for the 5 advertised events); **2× msg=544 size=40 outbound** firing in the same millisecond as inbound size:13 with event_id=0x02 (T2's TRACK_CHANGED INTERIM response — first-ever AVRCP 1.3-shape metadata response built by mtkbt for this device); Sonos accepted and **immediately started sending size:45 GetElementAttributes** (PDU 0x20, 26 retries at 2-second intervals). The size:45 retries continue indefinitely because we don't have a T4 trampoline yet — Sonos is asking "give me the track metadata!" and getting no answer. Y1MediaBridge's `MediaBridgeService` is being connected (`PlayService onServiceConnected`) so track strings are plumbed and ready; the remaining work is T4 (call `btmtk_avrcp_send_get_element_attributes_rsp` with the strings). T4 is the last remaining patch in the metadata path.

**Iter7 / iter8 / iter9 — fix the unknow-indication path via ELF-extension T4 stub.** iter6 also surfaced a separate problem: unhandled inbound frames (size:13 events ≠ TRACK_CHANGED, size:45 GetElementAttributes) generated zero outbound responses. The b.w 0x65bc fall-through from T1 / T2 was reaching the original "unknow indication" code, but that code requires `r0 = r5+8` (conn buffer; set at 0x6528 in original flow) AND `lr = halfword at sp+374` (= SIZE; loaded at 0x644e) — both of which the trampolines clobber. Iter7 restored r0 only (no msg=520 yet); iter8 added the lr restore (8 → 12 bytes at the 0xac54 stub). Iter9 hardware test: msg=520 NOT_IMPLEMENTED now flows for unhandled frames. **Major side effect**: AVRCP service stops restart-looping (iter6 had 30 PIDs cycling; iter9 has 2 stable), so PASSTHROUGH play / pause / skip now actually works on Sonos. First-ever transport-control delivery to a peer for this device.

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

1. **File format**: y1-track-info grows to 776 B with the `mCurrentAudioId` (big-endian) at bytes 0..7 ahead of the 3 × 256 B Title / Artist / Album slots. Y1MediaBridge writes the track_id alongside the strings.
2. **State file**: a 16 B y1-trampoline-state file (mode 0666, pre-created by Y1MediaBridge at startup) lets the BT process remember (a) the last track_id we told Sonos about (bytes 0..7) and (b) the last RegisterNotification transId (byte 8). The `extended_T2` trampoline writes both fields on every RegisterNotification(TRACK_CHANGED); the `T4` trampoline reads them on every GetElementAttributes.
3. **Trampoline rewrite**: T2's logic moves out of the cramped 44-byte `classInitNative` slot into LOAD #1's page-padding region. T2 stub at 0x72d4 becomes a single `b.w extended_T2`. extended_T2 dispatches PDU/event-id internally and falls through to T4 for PDU 0x20 or to 0x65bc otherwise. T4 is rewritten cleanly (memset → open/read y1-track-info → open/read y1-trampoline-state → cmp track_id → conditionally emit `track_changed_rsp CHANGED` with state[8] as transId + write new state → 3× `get_element_attributes_rsp`). The whole blob is now built dynamically from a tiny Thumb-2 assembler in `src/patches/_thumb2asm.py` + `_trampolines.py`, rather than hand-encoded as a hex array. Total 572 bytes of trampoline + paths; LOAD #1 grows from 0xac54 → 0xae90 (still well under the 0xbc08 LOAD #2 boundary).

**Hardware-tested 2026-05-06: deadlocked Sonos.** Returning the file's real `track_id` in the INTERIM(TRACK_CHANGED) response flipped Sonos into "stable identity per track, only refresh on CHANGED" mode. Our `T4` only fires when Sonos polls `GetElementAttributes`; Sonos won't poll until it sees a `CHANGED`. After the first `RegisterNotification` (transId=0x00, track_id=0x147), Sonos went silent for 14+ minutes despite 10 track changes. Forensics confirm:

- `y1-trampoline-state` mtime 14 min before capture-end; bytes 0..7 = 0x147 (audioId 327)
- `y1-track-info` track_id at capture time = 0x151 (audioId 337) — 10 tracks ahead
- 0 inbound VENDOR_DEPENDENT commands across 60 s capture (vs 2,933 in iter14c)
- AVCTP control channel up; only PASSTHROUGH (PLAY / PAUSE) flowed
- Sonos display: "No Content" / "Unknown Content" / stale "Trouble Maker" cached from the previous iter14c session

Cause: AVRCP 1.3 §5.4.2 Table 5.30 + ESR07 §2.2 / AVRCP 1.5 §6.7.2 — peer behaviour depends critically on whether the TG advertises a stable track identity in the EVENT_TRACK_CHANGED `Identifier` field. With a real id we entered a CT / TG handshake that requires us to push asynchronous CHANGED edges, but our trampolines are reactive only.

**Iter16 — same architecture, INTERIM / CHANGED track_id pinned to 0xFF×8.** Output md5 `5d74443293f663bcd3765721bb690479`. The change-detection bookkeeping (file bytes 0..7 vs state bytes 0..7) is preserved; only the wire-level `track_id` field in the response is hardcoded to the `0xFFFFFFFFFFFFFFFF` "not bound to a particular media element" sentinel. Implementation: an 8-byte 0xFF constant labelled `sentinel_ffx8` is appended after the path strings; `extended_T2`'s INTERIM emit and `T4`'s CHANGED emit both `ADR.W r3, sentinel_ffx8` instead of computing a stack address. Trampoline blob grows 572 → 580 bytes; LOAD #1 ends at 0xae98.

**Hardware-tested 2026-05-06: iter16 protocol layer fully working.** Sonos engaged (115 inbound CMD_FRAME_INDs in 71 s, 67 RegisterNotification responses, 43 GetElementAttributes responses). Forensic dump of y1-track-info (audioId 360 = "The Kintsugi Kid (Ten Years)" / Fall Out Boy) and y1-trampoline-state (audioId 358 = "Bleed American" / Jimmy Eat World, transId=0x00) confirmed Y1MediaBridge writes the file correctly and the trampolines update state when fired. The remaining defect is **polling cadence**: Sonos polled aggressively for the iter16 capture window (UI was being viewed) but its idle poll rate is too slow for shuffle-heavy playback. State froze 2 audioIds behind reality, so display was stuck on "Bleed American" while the current track was "The Kintsugi Kid". The iter16 reactive trampolines can't push CHANGED without an inbound query — fundamentally a chicken-and-egg with Sonos's polling.

**Iter17a — proactive CHANGED via Java→JNI hook.** Output md5s libextavrcp_jni.so `37ad4394efe7686d367d08f20e6f623b`, MtkBt.odex `ca23da7a4d55365e5bcf9245a48eb675`. Adds asynchronous CHANGED emission triggered by Y1MediaBridge's existing track-change broadcast, independent of Sonos's polling rate.

  Y1MediaBridge sends `com.android.music.metachanged` → MtkBt's BluetoothAvrcpReceiver intercepts → updates internal state and calls `BTAvrcpMusicAdapter.passNotifyMsg(2, 0)` (Message what=34, arg1=2 = TRACK_CHANGED) → handleKeyMessage's sparse-switch lands at sswitch_1a3 → cardinality check `BitSet.get(2)` (Java-side bookkeeping; never populated because our JNI trampolines bypass the Java path → permanently 0) → if-eqz skips the native call.

  Patch A (`MtkBt.odex` @ 0x03c530): NOP the `if-eqz v5, :cond_184` (4 bytes `38 05 da ff` → `00 00 00 00`). The native call now fires on every track-change broadcast.

  Patch B (`libextavrcp_jni.so` @ 0x3bc0): replace `notificationTrackChangedNative`'s `stmdb` prologue with a 4-byte `b.w T5`. T5 lives in LOAD #1 padding alongside T4 / extended_T2 / sentinel_ffx8 and:
  1. Calls the JNI helper at 0x36c0 (same one the stock native used) to obtain the BluetoothAvrcpService per-conn struct → conn buffer at +8.
  2. Reads `y1-track-info` first 8 bytes (current track_id from Y1MediaBridge).
  3. Reads `y1-trampoline-state` 16 bytes (last-synced track_id at bytes 0..7, last RegisterNotification transId at byte 8).
  4. If the track moved since the last sync, calls `btmtk_avrcp_send_reg_notievent_track_changed_rsp` via PLT 0x3384 with `reason=CHANGED`, `transId=state[8]`, `track_id=&sentinel_ffx8` (same iter16 sentinel — keeps Sonos in poll-on-each-event mode), then writes the new track_id back to state[0..7].
  5. Returns jboolean(1).

  Trampoline blob grows 580 → 768 bytes; LOAD #1 ends at 0xaf54. The reactive T4 and extended_T2 are unchanged — iter17a layers proactive CHANGEDs on top, so we get both reactive (Sonos polls) and proactive (Y1 changes track) refresh paths.

**Iter17a hardware test (2026-05-06): proactive layer working, T4 multi-attribute regression discovered.** Capture under `/work/logs/dual-sonos-avrcp-min-iter17a/`. The proactive CHANGED path is firing — msg=544 outbound count reached 4172 over the test window vs ~30 in iter16 — confirming the Java cardinality NOP + `notificationTrackChangedNative` → T5 chain works end-to-end. But Sonos is rendering metadata field-by-field with visible flicker (Title appearing intermittently while Artist/Album swap in/out). Diagnosed from logcat: 1299 outbound msg=540 (`get_element_attributes_rsp`) for ~433 inbound `GetElementAttributes` queries — exactly 3:1 — meaning T4 is emitting *three separate msg=540 frames* per query instead of one frame containing all three attributes packed in. This is the iter12 bug that iter13 had originally fixed: T4's three calls to PLT 0x3570 had `arg2 = transId, arg3 = 0`, hitting the function's legacy `arg3 == 0 → EMIT each call` path. The dynamically-assembled T4 in `_trampolines.py` regressed it during iter15's rewrite. The reactive change-detection logic, the file I/O, the proactive CHANGED via T5 — all working. Just the response packing is wrong.

**Iter17b: T4 multi-attribute single-frame fix.** Restored iter13's calling convention in `_trampolines.py::_emit_t4`:
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

---

## Hardware test history per CT

Per the spec-compliance directive (every Koensayr/AVRCP change must move toward strict AVRCP-spec compliance — spec-permissible options can be chosen for CT-compat reasons, but the chase starts from "what does the spec say"), per-device test results live here as research context, not in active code or implementation docs. Implementation files (`patch_*.py`, `_trampolines.py`, `MediaBridgeService.java`, `docs/PATCHES.md`, `docs/BT-COMPLIANCE.md`) cite AVRCP spec sections for rationale and reference this section for empirical validation.

CTs referenced below were used during pre-iter22 development. Future CT additions append here without changing implementation files.

### Sonos Roam (deprioritized 2026-05-06 — unreliable pairing)

A2DP Bluetooth speaker. Used as the most-permissive reference baseline for iter5 → iter18d hardware verifications (`/work/logs/dual-sonos-avrcp-min-iter*/`). Notable observations:

- Stays in poll-on-each-event mode when TRACK_CHANGED carries the `0xFF×8` sentinel (AVRCP 1.3 §5.4.2 Table 5.30 + ESR07 §2.2 / AVRCP 1.5 §6.7.2 8-byte clarification — "not bound to a particular media element"). T4's reactive emit fires per-poll, metadata refreshes on every track change.
- iter15 deadlock: real synthetic track_id in INTERIM flipped Sonos into "stable identity, refresh on CHANGED" mode, but iter15's T4 was reactive only — Sonos waited for a CHANGED edge that never came (Sonos didn't poll). 14-min zero-AVRCP-traffic confirmed via state-file forensics. Resolution: iter17a added T5 for proactive CHANGED.
- iter17b verified flicker-free: msg=540:size:45 ratio held 1:1, all three attributes pack into single frame.
- iter18d verified synthetic audioId fix: three track changes captured with synthetic audioIds, real metadata via FD path, msg=544 = 1071 INTERIM + 3 CHANGED (one per track change), ratios 1:1 with no flicker.
- 2026-05-06 onwards: pairing became unreliable in user testing. Dropped from active test matrix; past captures retained as reference.
- **2026-05-08 postflash (`/work/logs/dual-sonos-postflash/`):** resume-from-pause needed double-tap. AVRCP 0x44 PLAY arrived at the kernel as `KEY_PLAYCD` (7 events confirmed in `getevent.txt`) but `Y1Patch: PlayerService.play(Z) entry` never fired in the music app. `PlaySongReceiver.MEDIA_BUTTON keyCode=` forwarding log fired zero times either — the registered MediaButton dispatch ended up in a hole somewhere between AudioService and `PlaySongReceiver`. **Open investigation.** Attempted-fix on 2026-05-09 (drop `registerMediaButtonEventReceiver` so AudioService's broadcast fallback could deliver to the music app's manifest-filter receiver) was reverted same-day after Kia confirmed it broke metadata delivery (MtkBt uses the registered MediaButton client to find Y1MediaBridge's `IBTAvrcpMusic` Binder). Need to gdb-attach AudioService and watch where the PendingIntent send goes for `KEYCODE_MEDIA_PLAY` (126), or whether some other component is bumping us off the slot.

### Samsung The Frame Pro (active — TV / indoor)

Smart-TV head unit. Subscribes to event 0x02 TRACK_CHANGED only (3919 RegisterNotifications in `/work/logs/dual-tv-iter22b/`, all event 0x02). Notable observations:

- iter19b real track_id in INTERIM destabilized the TV: ~90 Hz RegisterNotification subscribe storm against TRACK_CHANGED INTERIMs (3401 inbound `size:13` over 38 seconds, sustained ~7 ms inter-frame). AVCTP saturated; PASSTHROUGH release frames dropped, producing held-key fast-forward at ~32× speed and stuck-haptic "vibrate-loop" symptoms. iter19d reverted to the 0xFF×8 sentinel which restores the spec-permissible "no media bound" mode and avoids the storm.
- iter21 (Patch D in `patch_y1_apk.py`) was a music-app-side defense: the FF/RW seek lambda bounded at 50 iters × 100 ms ≈ 5 s, clearing `fastForwardLock` on cap. **Reverted in iter24** — iter23/U1 fixes the AVRCP-side trigger at the kernel input layer (no more auto-repeat on `/dev/input/event4`), and iter21's cap was bounding local hardware-button hold-FF/RW too, breaking long scrubs through audiobooks/DJ mixes. iter21 captures (`/work/logs/dual-tv-iter21/`) remain useful as the empirical baseline for the dropped-release symptom.
- Does not subscribe to event 0x01 PLAYBACK_STATUS_CHANGED — uses TRACK_CHANGED edges only for any state inference. T9 (iter22b) is forward-compat for this CT.
- **2026-05-08 postflash (`/work/logs/dual-tv-postflash/`):** stuck `KEY_PAUSECD DOWN` in `getevent.txt` for ~15 s before the matching UP arrived (epoch `1778236775` → `1778236830`). High RegisterNotification subscribe-storm cardinality (`size:13` to `size:45` ratio ≈ 3.7:1) suggests the TV is re-subscribing because expected CHANGED edges aren't arriving fast enough. Likely shares root cause with the Sonos / Kia discrete-key chain-break (TG missing PASSTHROUGH releases under load) but with subscribe-storm amplification. Both still open — same investigation as Sonos's 2026-05-08 postflash entry above.
- **iter22d still produced the haptic loop (`/work/logs/dual-tv-iter22d-vibloop/`).** `getevent -lt` capture pinned the source: a single PASSTHROUGH FORWARD (`0x4B`) press whose RELEASE was dropped emitted **`KEY_NEXTSONG DOWN` once on `/dev/input/event4` ("AVRCP" uinput, `BUS_BLUETOOTH`), then 458 `KEY_NEXTSONG REPEAT` events at strict 40 ms intervals** until something else cancelled the held-key state. KEY_PAUSECD showed identical kernel-side behavior (1 DOWN, 0 UP, 126 REPEATs). At the mtkbt boundary the ratio was strict 1:1 between PASSTHROUGH PRESS frames and `MMI_AVRCP KEY_INFO` emissions — the amplification lives below mtkbt, in the kernel's `evdev` `EV_REP` soft-repeat timer (`REP_DELAY=250ms, REP_PERIOD=33ms` Linux defaults). Closed by **iter23 / U1**: NOP the `UI_SET_EVBIT(EV_REP)` ioctl at file offset `0x74e8` inside `libextavrcp_jni.so`'s `avrcp_input_init` so the device never claims `EV_REP` and `input_register_device()` never enables soft-repeat for it. Spec-correct per AVRCP 1.3 §4.6.1 (PASS THROUGH command, defined in AV/C Panel Subunit Specification ref [2]): CT is responsible for periodic re-send during held button; TG should forward one event per frame, not synthesize extras at the input layer.
- **2026-05-09 stock baseline (`/work/logs/dual-tv-20260509-2217/`).** TV connecting to a Y1 running stock firmware (no Koensayr patches). Confirms the TV-side AVRCP path is healthy by itself. Wire shape: 190 AVRCP-tagged log lines; msg=507 ×3 (connect_ind → outbound CONNECT_RSP); CONNECT_CNF returned the legacy `result:4096 bws:0 tg_feature:0 ct_feature:0` shape (= mtkbt-1.0 default); msg=520 ×20 (all PASSTHROUGH key acks — clean play/pause traffic, zero NOT_IMPLEMENTED rejects); zero msg=519 (no inbound META PDUs reaching JNI visibility); zero msg=540 / msg=544 (no META responses emitted). Confirms the structural finding: TV doesn't probe a 1.0 TG with 1.3 META commands, so stock can't surface the bridge-app metadata even if the TV's UI would render it. Once `--avrcp` advertises 1.3 (V1/V2 SDP bumps), the TV begins firing the META PDUs that the trampoline chain handles. Use as the canonical "TV-side-healthy, issue-on-our-end" reference when triaging post-flash regressions.

### Chevrolet Bolt EV (active — car / highway)

GM Infotainment 3 head unit. Strict CHANGED-driven CT (doesn't poll metadata; relies on TRACK_CHANGED edges + targeted GetElementAttributes). Fully META + PApp capable. Notable observations:

- Bolt EV `/work/logs/dual-bolt-iter18d/` showed PDU 0x17 InformDisplayableCharacterSet (UTF-8) issued once at connect; our pre-iter19a TG NACKed with msg=520. Bolt then registered TRACK_CHANGED 30 times but only ever issued a single GetElementAttributes — consistent with "the TG won't acknowledge my charset declaration so I distrust subsequent metadata." iter19a closed by adding T_charset.
- iter19b confirmed the TRACK_CHANGED wire-shape correctness fix (r1=0 to take the response builder's spec-correct path) on Bolt: first CHANGED edge fetched metadata, but every subsequent CHANGED edge after the first was ignored. UI-side block at a layer not visible in our captures; remains an open investigation.
- **2026-05-08 postflash (`/work/logs/dual-bolt-postflash/`):** three findings. (1) **Pause-during-play does not pause the Y1**, but pause works fine from Pixel 4 ↔ Bolt (user-confirmed 2026-05-08 — Bolt is not at fault). Whatever the Bolt sends as PAUSE never surfaces in our `MSG_ID_BT_AVRCP_CMD_FRAME_IND` logs as `rawkey:70` / `0x46`, and `getevent.txt` over the entire session shows only `KEY_PLAYCD`, `KEY_NEXTSONG`, `KEY_PREVIOUSSONG` on `/dev/input/event4` — never `KEY_PAUSECD` or `KEY_PLAYPAUSE` from event4. Some path inside `mtkbt`'s AVCTP RX is dropping the Bolt's pause primitive before it reaches our logged INDs. Open investigation: dump `mtkbt`'s AVCTP frame parser via `tools/attach-mtkbt-gdb.sh` while pressing pause from the Bolt to capture the raw bytes, and compare against the Pixel 4's framing for the same action. Candidate hypotheses: (a) Bolt uses an AVRCP 1.4+ Browse-channel command (PSM `0x1B`) we don't expose; (b) Bolt issues a vendor-specific PASSTHROUGH op_id outside the standard 0x44 / 0x45 / 0x46 range; (c) AVDTP-level SUSPEND that the Pixel propagates to its AVRCP layer but we don't. (2) PLAY-resume **still broken** — the 2026-05-08 attempted fix (drop `registerMediaButtonEventReceiver` from Y1MediaBridge) was reverted 2026-05-09 after a Kia metadata regression confirmed MtkBt depends on the registered MediaButton client to find the bridge's `IBTAvrcpMusic` Binder. (3) **No metadata** displayed by the Bolt despite `msg=540` GetElementAttributes being emitted with all 7 §5.3.4 attrs — separate Bolt-side ingestion issue, open investigation.
- **Pre-iter25:** Bolt is a strict CT and issues discrete PASSTHROUGH 0x44 PLAY (not the toggle 0x46 PAUSE). `dual-bolt-iter23` capture shows 5 discrete PLAY presses while Y1 was already PLAYING. iter22d's Patch E routed all of `KEY_PLAY` (85), `KEYCODE_MEDIA_PLAY` (126), and `KEYCODE_MEDIA_PAUSE` (127) through `playOrPause()` (toggle), which **inverted Bolt's intent on each press** — toggling away from PLAYING when Bolt asked for PLAY. User perceived the PLAY button as unresponsive. **Closed by iter25**: Patch E split into three discrete arms — KEY_PLAY → `playOrPause()` (toggle, legacy MediaButton); KEYCODE_MEDIA_PLAY → `play(false)` (discrete); KEYCODE_MEDIA_PAUSE → `pause(0x12, true)` (discrete). Spec-aligned with AVRCP 1.3 §4.6.1 + AV/C Panel Subunit Spec [ref 2]: PLAY (op_id 0x44) transitions to PLAYING from any state; PAUSE (op_id 0x46) transitions to PAUSED from any state. Concrete frame in AVRCP 1.3 §19.3 Appendix D.
- **2026-05-09 F4-iter1 postflash (`/work/logs/dual-bolt-20260509-2249/`)** — first capture against the V1/V2/V3/V4/V5 SDP shape + the full F4-iter1 trampoline chain (T_papp + T8 event 0x08). **Reframes prior "Bolt is PASSTHROUGH-only" framing as wrong**: the earlier pre-V1/V2 captures simply hadn't advertised AVRCP 1.3, so Bolt never had a reason to issue META commands against us. With the 1.3 advertisement live, Bolt fully exercises the META + PApp surface:
  - Connect → GetCapabilities (msg=522, T1 fired) → PDU 0x17 InformDisplayableCharacterSet (msg=536, T_charset fired) → 5× RegisterNotification → PASSTHROUGH play/forward press/release pairs (clean 1:1) → GetElementAttributes once (msg=540, T4 fired with all 7 attrs in 644 B IPC frame) → continuous RegisterNotification re-subscribes (20 inbound size:13, 72 outbound msg=544 with 52 proactive emits from T5/T9/extended_T2).
  - **PDU 0x14 SetPlayerApplicationSettingValue retry storm.** Starting ~21 s after connect, Bolt issues a size:11 PDU 0x14 every 3 s — 14 retries across the capture, all rejected by iter1's `T_papp` Set arm with `0x06 INTERNAL_ERROR` (msg=530, 8-byte reject frame). This is the **first concrete evidence** that a real CT in our matrix actively wants PApp Set support; iter1's reject path is exactly what's gating Bolt's PApp flow. Iter3 (real Set support) is therefore the high-priority next move; iter2's read pipeline (T_papp 0x13 + state observation) is **lower-priority for Bolt** because Bolt skips ListAttrs (PDU 0x11) and GetCurrent (PDU 0x13) entirely — goes straight to blind Set, suggesting Bolt's behavior is "set Repeat / Shuffle to a known state at connect" rather than "discover what's supported then mirror".
  - **2026-05-09 gdb-capture (`/work/logs/papp-gdb.log`)** — `tools/attach-libextavrcp-gdb-papp.sh` attached to the patched library, broke at `papp_set` (file `0xb13c`), and dumped 14 inbound PDU 0x14 frames with the following distribution:

    | attr_id | value | hits | meaning |
    |---|---|---:|---|
    | 0x02 Repeat | 0x01 | 2 | OFF |
    | 0x02 Repeat | 0x02 | 2 | SINGLE TRACK REPEAT |
    | 0x02 Repeat | 0x03 | 2 | ALL TRACK REPEAT |
    | 0x03 Shuffle | 0x02 | 8 | ALL TRACK SHUFFLE |

    Every frame is `n=1` (single attr/value pair). Bolt issues each Set ×2 (one on user press + one auto-retry after our reject). User cycled Repeat through all three supported values (OFF/SINGLE/ALL — never the AVRCP 0x04 GROUP value Y1 doesn't model), then pressed Shuffle ON four times. Confirms the Trace #18 enum mapping is correct and complete: AVRCP Repeat `0x01/0x02/0x03` ↔ Y1 `musicRepeatMode` `0/1/2`; AVRCP Shuffle `0x02` ↔ Y1 `musicIsShuffle=true`, `0x01` ↔ `musicIsShuffle=false`. Bolt is spec-conformant — no vendor-specific values, no GROUP variants, no multi-pair Sets. Iter3 can ship with the documented mapping and not have to defensively handle GROUP/oversized-n cases.
  - PASSTHROUGH path healthy: 7 press/release pairs (rawkey 68 PLAY ↔ 196 RELEASE; 75 FORWARD ↔ 203 RELEASE) all delivered. Y1MediaBridge state-tracking shows PLAYING/PAUSED transitions firing in lockstep — discrete-key chain is now correctly handled.
  - Subscribe re-registration cadence: 20 RegisterNotification inbounds across ~2 min (~10 s mean inter-frame) — much lower than the TV's storm shape, consistent with Bolt being a CHANGED-driven CT that re-subscribes on natural intervals rather than on every CHANGED edge.

### Kia EV6 (active — car / highway)

Hyundai Motor Group head unit. Polls GetPlayStatus (PDU 0x30) at ~1 Hz, subscribes to event 0x02 TRACK_CHANGED only. Notable observations:

- iter22b/22c capture (`/work/logs/dual-kia-iter22{b,c}/`): all 5 RegisterNotifications were event 0x02; uses GetPlayStatus polling for play_status display rather than subscribing to event 0x01.
- Pre-iter22c: T6 GetPlayStatus returned stale `playing_flag` because Y1MediaBridge's `onStateDetected` (play / pause path) wasn't refreshing y1-track-info before broadcast. Symptom: car-side icon stuck on initial value until next track change. Closed by iter22c.
- Pre-iter22d: Kia HMI's discrete PLAY button (PASSTHROUGH 0x44 → uinput KEY_PLAYCD → KEYCODE_MEDIA_PLAY 126) found no music-app handler; only KEYCODE_MEDIA_PLAY_PAUSE (85) was wired. Symptom: pressing PLAY while paused did nothing; Kia eventually fell back to PAUSE (which toggles via 85) after ~11 s and 4 button presses. iter22d Patch E added a handler for keycode 126 but routed it through `playOrPause()` (toggle); refined in iter25 to call `play(false)` (discrete) per AVRCP 1.3 §4.6.1 + AV/C Panel Subunit Spec — see Bolt EV section above for the empirical reason for the iter25 refinement.
- Pre-iter22d: Kia hid the playback-progress scrubber during playback because T6 returned static `position_at_state_change_ms` (iter20a deferral). Closed by iter22d's `clock_gettime(CLOCK_BOOTTIME)`-based live extrapolation.
- `mIBTAvrcpMusic` binder doesn't connect — zero `IBTAvrcpMusic.*` log entries in iter22c / d captures. AVRCP transport commands reach the music app via the libextavrcp_jni `avrcp_input_sendkey` → uinput path only. Open investigation.
- **2026-05-08 postflash (`/work/logs/dual-kia-postflash/`):** play-during-pause broken on the discrete-key chain (same symptom as Sonos — `KEY_PLAYCD` reaches kernel cleanly, `play(Z)` never fires in music app). **2026-05-09 re-test of an attempted fix (drop `registerMediaButtonEventReceiver`) confirmed MtkBt depends on the registered MediaButton client to locate Y1MediaBridge's `IBTAvrcpMusic` Binder** — the change broke metadata delivery on Kia entirely (no Title / Artist / Album), and AVRCP behavior degraded toward 1.0 fallback. Reverted. The discrete-key chain-break remains open; whatever fix we try has to keep the registration intact. Track playing time + scrub-bar advance verified working pre-revert.

# Lower BT profile-stack disassembly (2026-05-09)

Trigger: scoping the per-profile ICS-scoreboard pass (BT-COMPLIANCE.md §9.9). Goal: byte-level inventory of A2DP / AVDTP / AVCTP / GAVDP version + capability surfaces in the stock binaries so the existing AVRCP-1.3-paired V1 / V2 patches sit alongside an explicit map of the audio-triad gap.

Reads against `/work/v3.0.2/system.img.extracted/`. Current patch set (V1 / V2 / S1 / P1 + trampolines, post-v2.0.0) is the known-good wire baseline — V1 = AVRCP 1.0 → 1.3, V2 = AVCTP 1.0 → 1.2, both confirmed effective on the wire (Trace #12). Triad upgrade scope is the residual A2DP / AVDTP gap, not anything AVRCP / AVCTP.

## Binary inventory

In-scope BT-related ELFs in stock v3.0.2:

| Path | Size | md5 | Role |
|---|---:|---|---|
| `bin/mtkbt`                    | 1029140 | `3af1d4ad8f955038186696950430ffda` | BlueAngel daemon — L2CAP / HCI / AVCTP / AVDTP / GAVDP / A2DP / AVRCP TG |
| `lib/libextavrcp.so`           |   17552 | `6442b137d3074e5ac9a654de83a4941a` | AVRCP response builders (T-trampoline targets) |
| `lib/libextavrcp_jni.so`       |   50992 | `fd2ce74db9389980b55bccf3d8f15660` | JNI bridge — trampoline blob host |
| `lib/libmtka2dp.so`            |   17552 | `6dc3e453cd3ea05d7c0a7a07a100c0f7` | userspace A2DP stream socket bridge |
| `lib/libmtkbtextadpa2dp.so`    |   50320 | `b41be49baeeefbdb427e00bba2e0d2e2` | Java↔mtkbt A2DP shim (SEP register / stream-state IPC) |
| `lib/libmtkbtextadp.so`        |   17504 | `f084b8b3973c39bcb54a98dfaf068a31` | Java↔mtkbt main extadp (binder ↔ IPC) |
| `lib/libaudio.a2dp.default.so` |   58660 | `0d909a0bcf7972d6e5d69a1704d35d1f` | AOSP A2DP HAL (`standby_l`, `A2dpSuspended`) |
| `lib/libbtcust.so`             |    5204 | `898de90dcdca935f9acc563e491209d7` | customisation flags |
| `lib/libbtcusttable.so`        |    5256 | `271139c43691f90ed5d83aea342c19d0` | customisation tables |
| `lib/libem_bt_jni.so`          |   17764 | `2376b561f10267e1d047a06b11ba3948` | engineer-mode JNI |

Every profile from L2CAP up through AVRCP TG lives in `mtkbt`. Of the surrounding `lib*.so` files only `libaudio.a2dp.default.so` carries a BT-protocol-relevant function (`standby_l` → `a2dp_stop` → AVDTP SUSPEND on the wire), now covered by `patch_libaudio_a2dp.py` (AH1) — see §9.2 in BT-COMPLIANCE.md.

## Static SDP record region

Profile-version bytes in stock mtkbt's SDP source live at file offset `0xeb9d0..0xebd00` (LOAD #1 rodata, vaddr == file_off). DataElement-decoder walk finds eight UUID-paired version entries (`35 06 19 HH LL 09 VH VL` shape), all but one reading 0x0100 in stock:

| File offset (LSB of uint16 version) | Profile UUID | Stock value | Patched by |
|---|---|---|---|
| 0x0eb9f2 | 0x110D AdvancedAudioDistribution | `0x0100` (A2DP 1.0) | — |
| 0x0eba09 | 0x0019 AVDTP                     | `0x0100` (AVDTP 1.0) | — |
| 0x0eba25 | 0x0017 AVCTP                     | `0x0100` (1.0) | — |
| 0x0eba37 | 0x0017 AVCTP                     | `0x0100` (1.0) | — |
| 0x0eba4b | 0x110E AVRCP (legacy)            | `0x0100` (1.0) | — |
| 0x0eba58 | 0x110E AVRCP (legacy)            | `0x0100` (1.0) | **V1**: → `0x0103` (1.3) |
| 0x0eba6d | 0x0017 AVCTP                     | `0x0100` (1.0) | **V2**: → `0x0102` (1.2) |
| 0x0eba77 | 0x110E AVRCP (legacy)            | `0x0103` (1.3) | (already 1.3 in stock) |

Stock `[AVRCP] AVRCP V10 compiled` build banner + dispatch behaviour (PASSTHROUGH-only, all metadata commands NACK) match these stock byte values: AVRCP 1.0 / AVCTP 1.0 / A2DP 1.0 / AVDTP 1.0 across the board, with one already-1.3 AVRCP entry (which the V1 site mirrors post-patch).

A 12-byte-stride attribute table at vaddr `0xfa700..0xfa9c0` indexes these byte ranges by SDP attribute ID (`{ uint32 ptr; uint32 reserved; uint16 attr_id; uint16 length }`). The current served record set deliberately omits attribute 0x000d (AdditionalProtocolDescriptorList — Browse PSM 0x001b) per the disproven Browsing-bit / Pixel-shape experiments above — staying off-Browse keeps Sonos and similar CTs from opening a channel mtkbt can't service.

V1 and V2 are wire-confirmed (Trace #12, line 1346). The remaining seven version sites (six unpatched + one stock-1.3 mirror) are not currently consulted by any peer in the test matrix, but the AVCTP-multiplicity question is open: only one of the three AVCTP sites is V2-patched, and whether the unpatched two would matter against a stricter CT than we've tested is unverified.

## A2DP / AVDTP advertised at 1.0 — gap, not deviation

Both bytes at 0xeb9f2 and 0xeba09 read 0x0100 in stock and remain unpatched in the current `--avrcp` set. Spec-acceptable (we ship the SBC-only TG behaviour A2DP 1.0 demands), but spec-incomplete — features added in A2DP 1.2 (Content Protection / SCMS-T) and 1.3 (DELAY_REPORT) are off the table at the advertisement layer.

## AVDTP signal coverage — codepoints vs handlers

ARCHITECTURE.md §"AVDTP signal codes" lists sig_id 0x01..0x0d as confirmed in mtkbt code. Provisional read superseded — the dispatcher disassembly in Trace #13 below shows real handler entries for **all** of sig 0x01..0x0d. Sig 0x0c (GET_ALL_CAPABILITIES) reaches a stub at 0xab4de that always returns BAD_LENGTH; sig 0x0d (DELAYREPORT) reaches 0xab540 with substantive logic. Sig 0x08 (CLOSE) and sig 0x09 (SUSPEND) jump-table entries point to the dispatcher's epilogue (0xab786) — handled elsewhere or trivially.

The pre-Trace-#13 read ("no log strings → silent drop") was wrong because:
1. radare2's `aaa` linear sweep failed to analyse the dispatcher at 0xaa72c (invalid bytes at 0xaa720 trapped its analyser → it silently skipped the function).
2. `grep` for log strings doesn't pick up handlers that don't emit log lines for the per-sig case (the dispatcher logs via fcn.000675c0 with a small per-sig string-id inside the prologue, before the TBH dispatch).

Implication for AVDTP version-byte bump (V3+V4 — AVDTP 1.0→1.3 in served A2DP Source SDP): now corroborated by disassembly. Advertising AVDTP 1.3 is not a pure paper claim — sigs 0x0c (GET_ALL_CAPABILITIES, AVDTP 1.3 ICS Acceptor Mandatory row 9) and 0x0d (DELAYREPORT, Optional) both have dispatch entries, though sig 0x0c's stub fails the response. V5 (sig 0x0c → sig 0x02 alias) closes the row 9 gap.

## A2DP codec scope

Confirmed SBC-only. `GavdpAvdtpEventCallback` rejects non-SBC SEPs (`[AVDTP_EVENT_CAPABILITY]not AVDTP_CODEC_TYPE_SBC` → "try another SEP" fallback). No AAC / MP3 / ATRAC strings in `mtkbt` or `libmtkbtextadpa2dp.so`. SBC is Mandatory for A2DP 1.0+ TGs, so this is spec-compliant; AAC is Optional and not advertised.

## Trace #13 (2026-05-09) — AVDTP signal dispatcher disassembled, V5 design candidate identified

### Why this trace exists

Open-question items 1+2 ("AVDTP DELAY_REPORT and GET_ALL_CAPABILITIES handler — real or NOT_IMPLEMENTED stub?") and the V3+V4 SDP version bumps (A2DP/AVDTP 1.0→1.3) needed empirical answers from the binary. Static analysis using `grep` / `objdump` had been inconclusive because mtkbt is stripped + heavily MOVW/MOVT-encoded.

### Tooling pivot

Per `feedback_install_proper_tools.md` memory rule, switched from grep-grinding to `dnf install -y radare2` (one command, ~30 s), then used `axt` xref analysis on candidate per-sig handler functions. radare2's auto-analysis (`aaa`) had silently SKIPPED the dispatcher function because invalid bytes at 0xaa720 trapped its linear-sweep analyser — manual disassembly via `r2 -c "pd 200 @ 0xaa72c"` was needed.

### The dispatcher

Located at file offset **0xaa72c** in stock mtkbt (md5 `3af1d4ad…`). Prologue:

```
0xaa72c: push.w {r4-r8,sb,sl,fp,lr}    ; full-context save
0xaa73a: ldrb.w sb, [r1]               ; sig_id = first byte of cmd struct
0xaa7f6: add.w sb, sb, -1              ; sb = sig_id - 1 (TBH index)
0xaa812: cmp.w sb, 0x28                ; bounds check (accepts 0..0x28 = 41 entries)
0xaa816: bhi.w 0xab786                 ; OOB → epilogue
0xaa81a: tbh [pc, sb, lsl 1]           ; jump-table dispatch
0xaa81e: <halfword table>              ; entry n*2 → target = 0xaa81e + 2*halfword
```

### Decoded jump table (sig_id 1..13)

| sb | sig_id | wire signal           | target  | meaning                                  |
|----|--------|-----------------------|---------|------------------------------------------|
| 0  | 0x01   | DISCOVER              | 0xaa870 | full handler                             |
| 1  | 0x02   | GET_CAPABILITIES      | 0xaa924 | full handler — capability list builder   |
| 2  | 0x03   | SET_CONFIGURATION     | 0xab66e | full handler                             |
| 3  | 0x04   | GET_CONFIGURATION     | 0xaaaf6 | full handler                             |
| 4  | 0x05   | RECONFIGURE           | 0xaab64 | full handler                             |
| 5  | 0x06   | OPEN                  | 0xaac6c | full handler                             |
| 6  | 0x07   | START                 | 0xaacde | full handler                             |
| 7  | 0x08   | CLOSE                 | 0xab786 | jump to epilogue (handled elsewhere — TBD) |
| 8  | 0x09   | SUSPEND               | 0xab786 | jump to epilogue (handled elsewhere — TBD) |
| 9  | 0x0a   | ABORT                 | 0xab008 | full handler                             |
| 10 | 0x0b   | SECURITY_CONTROL      | 0xab072 | full handler                             |
| 11 | **0x0c** | **GET_ALL_CAPABILITIES** | **0xab4de** | **STUB — always returns BAD_LENGTH error** |
| 12 | 0x0d   | DELAYREPORT           | 0xab540 | full handler (sufficient for AVDTP 1.3)  |

### Sig 0x0c stub anatomy (file 0xab4de)

```
0xab4de: ldrb.w lr, [r4, 8]      ; load response-buffer state byte
0xab4e2: cmp.w lr, 8
0xab4e6: bls 0xab51a              ; if state <= 8 → error path
0xab4e8: ldrb.w r8, [r4, 9]
0xab4ec: cmp.w r8, 0
0xab4f0: bne 0xab51a               ; if [r4+9] != 0 → error path
... (small "main" path that just calls a logger and returns to epilogue —
     no actual capability-list construction)
0xab51a: <error path>
  movs r0, 8 ; strb [r4, 8]    ; "8" stored
  movs r1, 6 ; strb [r6, 1]    ; "6" stored — internal state code
  bl fcn.000af4cc               ; error-response sender
  b 0xab786                     ; epilogue
```

A fresh inbound GET_ALL_CAPABILITIES has [r4+8] (response-buffer length) initially 0 or small → `bls 0xab51a` taken → error response. **In effect mtkbt advertises sig 0x0c as supported but always rejects it.**

### V5 design candidate — 2-byte jump-table alias

Cleanest V5: change the halfword at file offset **0xaa834** from `60 06` (target 0xab4de stub) to `83 00` (target 0xaa924 sig 0x02 handler).

Pros:
- 2 bytes total. No code injection / trampoline needed.
- Sig 0x0c response body is a wire-compatible subset of GET_ALL_CAPABILITIES per V13 §8.8 (response = GET_CAPABILITIES content + optional service capabilities; we send only the GET_CAPABILITIES core, peer reads it as "no extended caps").
- Closes GAVDP 1.3 ICS Acceptor Table 5 row 9 (GET_ALL_CAPABILITIES_RSP).

Risk — **unverified as of 2026-05-09**: response wire sig_id may be hardcoded to 0x02 by the sig 0x02 handler. At 0xaa9fa the handler does `strb r2, [r6, 1]` with r2=2. Whether [r6+1] is the response wire sig_id field or an internal state byte is ambiguous from static analysis alone. Other per-sig handlers also write to [r6+1] (sig 5 writes 5, sig 0x0c stub writes 6) suggesting it's a state code, but the response wire sig_id origin needs runtime confirmation before V5 lands.

### Validation plan

`tools/attach-mtkbt-gdb-avdtp.sh` updated to BP at:
- 0xaa72c (dispatcher entry — captures sig_id from [r1] and full cmd-buffer first 16 B)
- 0xaa924 (sig 0x02 handler entry — captures r4/r5/r6/r1 for state struct mapping)
- 0xab4de (sig 0x0c stub entry — confirms which inbound triggers it)
- 0xab51a (sig 0x0c error path — confirms always-reject behavior)
- 0xaeb9c (response sender called from sig 0x02 — captures sig_id arg location)
- 0xaf4cc (error response sender called from sig 0x0c — captures error format)

Drive a fresh pair attempt against any A2DP Sink CT (Sonos / Bolt / TV); the BPs at 0xaa72c will fire for every inbound AVDTP signal and let us cross-correlate sig_id source with the response builder's input.

### Trace #13c (2026-05-09 night) — 0xaa72c reconfirmed as AVDTP dispatcher; full TBH table decoded

The 8-BP run (with peer driving a pair attempt) captured 13 wire-tagged dispatch fires through 0xaa72c with `[r1]` carrying AVDTP wire signal_ids. Sequence (chronological from `/work/logs/mtkbt-gdb-avdtp.log`):

| Fire | sig_id | wire signal           | LR        | notes |
|------|--------|-----------------------|-----------|-------|
| 1    | 0x0d   | DELAYREPORT           | 0x401a483b | pre-config |
| 2    | 0x03   | SET_CONFIGURATION     | 0x401a49cd | |
| 3    | 0x01   | DISCOVER              | 0x401a49cd | same caller |
| 4-7  | 0x04   | GET_CONFIGURATION ×4  | 0x401a54b3 | per-SEP polling |
| 8    | 0x05   | RECONFIGURE           | 0x401a5803 | |
| 9    | -      | cap-parser fcn.000afd5c | 0x401a557d | one fire only |
| 10   | 0x06   | OPEN                  | 0x401a3e91 | |
| 11   | 0x07   | START                 | 0x401a3ec3 | streaming begins |
| 12   | 0x18   | (internal)            | 0x401a5803 | |
| 13   | 0x0b   | SECURITY_CONTROL      | 0x401a5bc5 | |
| 14   | 0x0f   | (internal)            | 0x401a5803 | |
| -    | 0x17   | heartbeat ×308        | 0x401a5bc5 | background |

**Notably absent**: sig 0x02 (GET_CAPABILITIES) and sig 0x0c (GET_ALL_CAPABILITIES) — peer skipped capability probing entirely. Likely cached capabilities from a prior pair, or the peer trusts the SDP record. This means V5 (sig 0x0c handler) is mechanically valid (dispatcher confirmed; jump-table entry at 0xaa834 routes sig 0x0c to 0xab4de stub) but won't be empirically tested against this CT — needs a stricter peer or unprimed re-pair to fire.

**Struct geometry** (constant across all dispatch fires):
- `[r1+0]`: msg/sig_id byte (the dispatched value)
- `[r1+4..7]`: pointer back to r0 (state struct ref, always 0x415e92b8)
- `[r1+8..11]`: function pointer (0x4028b8d4 = file 0xb8d4 — likely a shared completion callback)
- `[r1+12..15]`: per-msg payload (e.g., 0x14 for OPEN, 0x18 for sig 0x18)
- `[r1+24..27]`: 0x40290d29 (file 0x19cd29) — common state struct ptr

So `[r1]` is an **internal mtkbt event message** tagged with AVDTP wire-format sig_ids, not a raw L2CAP frame. The dispatcher routes 41 codepoints: sig_id 1-13 (AVDTP wire) + sig_id 14+ (internal events 0x17 background, 0x18 / 0x0f synthetic). Same TBH function handles both — V5's jump-table alias edit is geometrically sound.

**Reverting Trace #13 follow-up's claim** that fcn.000b0c30 is the real dispatcher: that function fired but only as the AVDTP state-machine driver (positive control), not as the wire-RX path. r1 args at fcn.000b0c30 had state=0x00 (7 fires) and state=0x03 (7 fires) with [r1+1] varying — internal state transitions, not signal frames. fcn.000b0c30 is a state-handler called BY the dispatcher's per-sig handlers, not the dispatcher itself.

### Trace #13 follow-up (2026-05-09 evening) — 0xaa72c hypothesis invalidated, real dispatcher relocated to fcn.000b0c30

The 6-BP run captured in `/work/logs/mtkbt-gdb-avdtp.log` (828 lines) shows:

- **267 fires at 0xaa72c** with `[r1] = 0x17` (dec 23) — out of AVDTP wire signal range (0x01..0x0d). Single fire with `[r1] = 0x10` (dec 16). Same r0 across all hits (`r0 = 0x415e92b8`, equal to `[r1+4]`).
- **Zero fires** at 0xaa924 (sig 0x02 handler), 0xab4de / 0xab51a (sig 0x0c stub + error path), 0xaeb9c, 0xaf4cc.

Conclusion: **0xaa72c is NOT the AVDTP wire-signal dispatcher.** It's BlueAngel's internal task-message dispatcher — the function pointer at `[r4+0x464]` referenced from the orchestrator at 0xb1bc2 (a `blx r3` indirect call). The TBH at 0xaa81a routes 41 internal task-message types, of which AVDTP wire signals are not one. My V5 design (jump-table alias at 0xaa834) was therefore based on the wrong jump table — patching it would change internal-message-type-12 routing, not AVDTP sig 0x0c.

**The real AVDTP RX dispatcher**: `fcn.000b0c30` (file 0xb0c30). 6482 bytes, 239 basic blocks, 152 cyclomatic complexity — radare2's `aaa` linear sweep had silently skipped it because invalid bytes at 0xb0c20-0xb0c2e trap the analyser. Manual disasm at 0xb0c30:

```
0xb0c30: push.w {r4-r8,sb,sl,fp,lr}
0xb0c34: mov r8, r0                    ; r0 = stream / channel struct
0xb0c40: mov r5, r1                    ; r1 = AVDTP signal frame ptr
0xb0c44: ldrb r3, [r1]                 ; r3 = AVDTP byte 0 (header[0])
0xb0c4c: cmp r3, 7                     ; bound check
0xb0c4e: bhi.w 0xb19c8                 ; oob error
0xb0c52: tbh [pc, r3, lsl 1]           ; state-machine dispatch
```

Confirmed dispatcher because:

1. fcn.000afeec (`AvdtpSigParseConfigCmd` — confirmed via the `[AvdtpSigParseConfigCmd]insert stream to channl stream list` log string at 0xea67a) is called from `bl` at 0xb1012, which lies inside fcn.000b0c30's body.
2. Six other avsigmgr.c-tagged functions (0xafd5c, 0xb01b4, 0xb0270, 0xb0468, 0xaedd8, 0xafeec) are reachable from inside this function.
3. The byte-0 dispatch (cmp r3, 7) appears to be on AVDTP state code (8 states), not on signal_id — the signal_id parse happens after state selection. This matches BlueAngel's "stream signaling state machine" architecture.

Per AVDTP V13 §8.5, sig_id lives in **byte 1 (low 6 bits)** of the signal frame, not byte 0. Earlier interpretation (sig_id = [r1+0]) was geometrically wrong — that would have been transaction-label/packet-type/msg-type, not signal_id. The new BPs read `[r1+1] & 0x3f` for the wire signal_id.

`tools/attach-mtkbt-gdb-avdtp.sh` re-targeted at 0xb0c30 + 0xafeec + 0xb1012 + 0xb0b50. Re-run on next pair attempt will show:
- Sig_id of every inbound AVDTP signal (decoded from wire byte 1).
- AVDTP state code on each dispatch.
- SET_CONFIGURATION path (b1012 → afeec) for cross-confirmation.

V5 design TBD — depends on next capture's data on how sig 0x0c is currently rejected (or if it is at all) inside fcn.000b0c30.

### Trace #14 (2026-05-10) — L2CAP PSM 0x19 callback registration located in mtkbt

Hunt: locate where mtkbt registers its inbound L2CAP callback for PSM 0x19 (AVDTP signaling/media), to anchor the RX chain that ultimately drives the dispatcher at 0xaa72c.

**Method.** AVCTP has the log string `[AVCTP] register psm 0x%x status:%d` at 0xdbea0; AVDTP doesn't have an equivalent log line, so the AVCTP register call site was used as the fingerprint. radare2 `axt @ 0xdbea0` resolves to a PC-relative ADD at 0x6d2de inside an init function. The instruction immediately preceding is `bl fcn.0007c78c` at 0x6d2c8 — that's BlueAngel's `L2CAP_RegisterPsm`. Confirmed by inspecting `fcn.0007c78c`: it logs `Protocol->inLinkMode:%d` / `Protocol->outLinkMode:%d`, validates MTU between 0x12 and 0xfff9, and walks a 20-slot global registration table looking for an empty slot.

**15 callers of `fcn.0007c78c`** (all distinct profiles registering different PSMs). The AVDTP candidate is `fcn.000ae9bc` — sits in the AVDTP code region (0xae9bc in mtkbt), single-caller (`bl` at 0xab8a8 inside a larger init sequence). Disassembly confirms PSM 0x19:

```
0x000ae9bc      push.w {r4..fp, lr}
0x000ae9c2      bl fcn.000b54ec                 ; alloc / lookup PsmCtx
0x000ae9ca      bl fcn.000afb34                 ; init AVDTP local state
0x000ae9d0      movw r0, #0x69b                  ; MTU = 1691  (struct[+0x30])
0x000ae9d6      movs r3, #0x30                   ; channel mode flags (struct[+0x32])
0x000ae9d8      mov.w sb, #0x19                  ; PSM = 0x19  (struct[+0x2c])
0x000ae9dc      add r4, pc; ldr r4, [r4]         ; r4 = *(GOT slot 0xf9c38) = AVDTP state struct (R_ARM_RELATIVE → BSS @ link-vaddr 0x1b7d28)
0x000ae9e0      add r1, pc; ldr r1, [r1]         ; r1 = *(GOT slot 0xf9c3c) = AVDTP L2CAP callback fnptr (R_ARM_RELATIVE → 0xafc69, thumb-bit fnptr to fcn at 0xafc68)
0x000ae9e4      strh r0, [r4, #0x30]             ; PsmCtx.MTU = 0x69b
0x000ae9e6      str  r1, [r4, #0x28]             ; PsmCtx.callback_struct = r1
0x000ae9e8      add.w r0, r4, #0x28              ; r0 = &PsmCtx[0x28] (= L2CAP register arg)
0x000ae9ec      strh.w sb, [r4, #0x2c]           ; PsmCtx.PSM = 0x19
0x000ae9f0      strh r3, [r4, #0x32]             ; PsmCtx.flags = 0x30
0x000ae9f6      strb.w r7, [r4, #0x36]           ; outLinkMode = 1
0x000ae9fa      strb.w r7, [r4, #0x35]           ; inLinkMode  = 1
0x000aea06      bl fcn.0007c78c                  ; L2CAP_RegisterPsm(&PsmCtx[0x28])
0x000aea0a      mov r5, r0                        ; r5 = register status (0 = OK)
0x000aea0e      bne 0x000aea7c                   ; on error → return r8
0x000aea10..7a                                   ; success: 4-iteration SEP-init loop, stride 0x18
```

**L2CAP register-arg layout** (relative to r0 passed into `fcn.0007c78c`):

| Offset | Field           | Init value | Notes |
|--------|-----------------|------------|-------|
| +0x00  | callback table ptr | PIE-relocated | data_ind / conn_ind / config_ind / disc_ind |
| +0x04  | PSM             | 0x0019     | matches `ldrh r3, [r0, 4]` in L2CAP_RegisterPsm |
| +0x06  | (zero)          | 0          | |
| +0x08  | MTU             | 0x069b (1691) | matches `ldrh r3, [r0, 8]` validation |
| +0x0a  | flags           | 0x0030     | |
| +0x0d  | inLinkMode      | 1          | matches `ldrb r1, [r4, 0xd]` log line |
| +0x0e  | outLinkMode     | 1          | matches `ldrb r1, [r4, 0xe]` log line |

**Caller context** (0xab8a8 in some larger AvdtpInit-like entry, hosted inside r2's mis-spanning fcn.0000d98c):

```
0x000ab884      bl fcn.0004ca90                 ; memset some ctx region
0x000ab88a      add.w r0, r4, #0x1ac
0x000ab88e      str r3, [r4, #4]                ; flag = 1
0x000ab890      strb r3, [r4, #1]               ; flag = 1
0x000ab892      strb r5, [r4]                    ; type = arg
0x000ab894      bl fcn.0006ce5c                 ; init list / queue ×3 at +0x1ac, +0x1b4, +0x1bc
0x000ab8a8      bl fcn.000ae9bc                 ; ← AVDTP L2CAP register
0x000ab8ac      mov r0, r5; pop {r4, r5, r6, pc}
```

**Implications.**

1. **Single registration, single PSM** (0x19) — AVDTP signaling and media share PSM 0x19 per AVDTP V13 §6 (multiplexed via L2CAP CIDs at runtime). No separate "media-channel" register exists.
2. **MTU advertised: 1691** (0x69b). AVDTP V13 §6.4.1 mandates ≥ 672 for signaling; 1691 is consistent with BasePoint default. Sufficient for AVDTP 1.3 GET_ALL_CAPABILITIES_RSP (worst case ~50 B per Service Capability × N caps, well under 1691).
3. **Mode flags 0x30 + inLinkMode/outLinkMode = 1** — Basic L2CAP mode (no ERTM/streaming-mode), consistent with stock A2DP 1.0 era. ERTM is optional per AVDTP V13 §A.2 ICS row M.4 — not advertised here.
4. The callback table at PIE-resolved address 0xf9c3c contains AVDTP signaling layer's connect/disconnect/data-indication entry points. The data-indication path is what eventually feeds `fcn.000b0c30` (the stream signaling state machine) and the per-signal handlers (0xaa924 sig 0x02, 0xab4de sig 0x0c stub, etc.) — closing the upstream side of the RX chain Trace #13c established the downstream side of.

**Anchors for future work** (this register call is **not patched** today; documented for completeness in case a future deviation requires altering MTU, channel mode, or the callback set):

- AVDTP register fcn: file offset **0xae9bc** (one caller at 0xab8a8)
- L2CAP_RegisterPsm: file offset **0x7c78c** (15 callers covering all profiles)
- AVCTP register caller: 0x6d2c8 (PSM 0x17, MTU 0x69b same advertised cap, inside fcn.0000f79c-region init)
- AVDTP state-struct GOT slot: vaddr **0xf9c38** → R_ARM_RELATIVE addend **0x001b7d28** (link-time vaddr in `.bss`; fcn.000b54ec memsets 0x8fc=2300 bytes there at boot; 16 internal AVDTP module fns reference this slot for state access — confirmed via `axt @ 0xf9c38`)
- AVDTP L2CAP callback GOT slot: vaddr **0xf9c3c** → R_ARM_RELATIVE addend **0x000afc69** (thumb-bit fnptr to **fcn at 0xafc68** — the inbound-L2CAP-frame handler installed in the BlueAngel global PSM registry)

**The L2CAP callback (fcn at 0xafc68)** — entry point for every inbound AVDTP frame on PSM 0x19. r2 doesn't recognise it as a function (no fcn label) but disasm is clean from 0xafc68 onward with a standard prologue. Signature: `(arg0, arg1)` where `arg1` is an L2CAP event/frame struct (`arg1[0]` = event-type byte, `arg1[+4]` = payload pointer). Body:

```
0x000afc68      push {r4, r5, r6, lr}
0x000afc6a      mov r6, r1                       ; arg1 = event/frame struct
0x000afc6c      ldr r4, [r1, #4]                 ; r4 = arg1[+4] = payload ptr
0x000afc6e      mov r5, r0                       ; arg0 = channel/conn handle
0x000afc72      bl fcn.000afb7c                  ; helper (reads AVDTP state via slot 0xf9c38)
0x000afc76      ldrb r3, [r6]                    ; r3 = event_type byte
0x000afc78      cbz r0, 0xafca0                  ; helper returned 0 → connection-init path
0x000afc7a      cmp r3, #1                        ; event_type == 1?
0x000afc7c      beq 0xafc8c                       ; 1 → config_ind / specific event
                                                  ; else → fcn.000afbfc (data dispatch)
0x000afc8c..afc9e                                 ; case-1: fcn.000afbfc(0); store r5 → r4[+0]
0x000afca0..afcc4                                 ; helper-returned-0 path: alloc via fcn.000afba0 OR recurse via fcn.000afc2c
0x000afcb8      pop {r4, r5, r6, lr}
0x000afcbc      b.w fcn.0007d624                 ; tail call back into L2CAP module
0x000afccc..end                                   ; common success path: bl fcn.00084240 (alloc?), TBH dispatch via slot at [r3 + r0 lsl 2]
```

**Helper map** (all read AVDTP state via slot 0xf9c38):

- `fcn.000afb7c` (in-degree 1): channel/SEP lookup helper, called once per inbound frame from 0xafc72.
- `fcn.000afbfc` (in-degree 3): data-handler, fires from 0xafc80 (event_type ≠ 1) and 0xafc8e (event_type == 1). Likely the path that walks per-channel state and dispatches into `fcn.000b0c30` (the AVDTP state machine) — verifiable via gdb breakpoint.
- `fcn.000afba0` (in-degree 1): allocator, fires from 0xafca4 on connection-init path.
- `fcn.000afc2c` (in-degree 1): recursive sub-handler, fires from 0xafcc4.

**Indirect dispatch into 0xb0c30 / 0xaa72c**. r2 reports **zero direct callers** for both `fcn.000b0c30` (state-machine dispatcher) and `0xaa72c` (`GavdpAvdtpEventCallback`) — they're invoked exclusively via function pointer fields in the AVDTP state struct (offset 0x464 for the event callback, per the `blx r2` indirect pattern noted earlier in fcn.000b09fc). Those fnptrs are written to the BSS state struct during AVDTP layer init — at link time the struct is zero, so init-time stores set them up. The chain is therefore:

```
inbound L2CAP frame on PSM 0x19
  → BlueAngel L2CAP RX (fcn.0007d624 region)
  → registered PSM-callback dispatch
  → fcn at 0xafc68              [the AVDTP L2CAP callback, this trace]
  → fcn.000afbfc / afb7c         [helpers; resolve channel from frame]
  → channel-specific data-ind    [stored fnptr in per-channel struct, set at config time]
  → fcn.000b0c30                 [AVDTP signaling state machine, Trace #13c]
  → per-signal handler            [0xaa924 sig 0x02, 0xab4de sig 0x0c stub, etc.]
  → AVDTP state struct callback at offset 0x464
  → 0xaa72c (GavdpAvdtpEventCallback) [GAVDP-layer event dispatch, Trace #13c]
```

**Empirical close-out left for hardware**. The fnptr stored at AVDTP-state[0x464] is the only remaining gap and it's runtime-populated. Adding two BPs to the existing `tools/attach-mtkbt-gdb-avdtp.sh` — one at `0xafc68` (logging arg0 / arg1[0] / arg1[+4]) and one at the indirect `blx r2` site at 0xb0b46 (logging r2 = the resolved fnptr) — would print the full chain on the next pair attempt. No patch action implied; this is map-completion work.

### Trace #15 (2026-05-10) — GET_CAPABILITIES response builder calling convention; V5 risk re-evaluated

Hunt: characterise the call signature of `fcn.000aeb9c` (the function the sig 0x02 handler at 0xaa924 calls to "send the response") so we can validate whether V5's redirect of sig 0x0c into the same handler produces a wire-correct response or breaks signal-id pairing per V13 §8.5.

**Calling convention.**

```c
int fcn_000aeb9c(AvdtpChannel *channel, uint8_t ack_flag);
```

- **r0 (channel)**: pointer to AVDTP signaling channel context. Validated non-null; offset+8 looked up against a global registry via `fcn.0006ccdc`. Returns `0x12` on null, `0xd` on lookup-miss, `0xb` on state error from `fcn.0006d9ac`. Otherwise tail-calls `fcn.000afd40(channel+8, ack_flag)`.
- **r1 (ack_flag)**: u8. Observed values at the sig 0x02 handler call sites:
  - `0` at `0xaa948` — error path ("no registered SEP" log, follows the search for a SEP that found none).
  - `1` at `0xaa9fe` — success path (taken after the handler builds the per-SEP capability payload and stores the request sig_id into the global state).

**Tail chain.**

```
fcn.000aeb9c (channel, ack_flag)
  → bl fcn.0006ccdc       [registry lookup]
  → bl fcn.0006d9ac        [state validate]
  → b.w fcn.000afd40       [tail call]

fcn.000afd40 (channel, ack_flag)
  → r0 = *(channel)        [halfword: L2CAP CID]
  → if (ack_flag == 0) r1 = 0, r2 = 0   [reject path]
  → else                  r1 = 4, r2 = ack_flag   [accept path]
  → bl fcn.0007d624

fcn.0007d624 (cid, accept_flag, link_modes)
  → log "l2cap: Upper accept" (or "Upper reject")
  → look up channel via fcn.00083014
  → if channel state != 0xa: dispatch to fcn.000860d8 ("createchannel"), log "l2cap: pass to createchannel status:%d"
  → else: validate, store config bytes (link_modes[0..0x14]) into channel ctx at offsets 0xd4/0xd8/0xe0/0x104/0x108/0x143
  → tail-call fcn.0007d500 (L2CAP signal-frame TX dispatch)
```

**The reframe**: `fcn.0007d624` is BlueAngel's **`L2CAP_ConnectRsp`** — the upper-layer accept/reject for an inbound L2CAP CONNECT_REQ. It is **not** the AVDTP signal-frame TX path. So `fcn.000aeb9c` is the GAVDP-to-AVDTP-layer handshake function: GAVDP says "I accept this signaling channel" (or rejects it), and BlueAngel's AVDTP layer emits the corresponding L2CAP CONNECT_RSP.

The pre-call state setup (memcpy into `[channel + 0xc4]` from `[msg.field_at_0x1c + 0xc0]`, stores at `[channel + 0xa4]` / `[channel + 0xa8]`, write to `[GlobalAvdtpState + 1] = 2`) is **not** wire-frame construction — it's per-channel context setup so that BlueAngel's AVDTP layer can subsequently parse and respond to in-band signal frames.

**Where the actual GET_CAPABILITIES_RSP wire frame is built**: not yet localised. The sig 0x02 handler at 0xaa924 prepares state, then calls fcn.000aeb9c which goes to `L2CAP_ConnectRsp` — but the AVDTP signal-frame response (with sig_id byte 1, msg_type=RSP_ACK in byte 0) must be emitted somewhere else, presumably by AVDTP layer code triggered by a state transition rather than by direct call from the handler. fcn.000afd40 only writes the L2CAP CID and the accept-flag — no AVDTP signal_id byte construction.

**V5 risk re-evaluation** (refines the "best-effort workaround" wording from Trace #13):

1. **The handler at 0xaa924 doesn't write the wire signal_id byte.** It writes `2` to `[GlobalAvdtpState + 1]`, but that's an internal state byte (the `r6` base is the AVDTP module's global state struct loaded from GOT slot 0xf9c14, not the wire frame).
2. **The L2CAP CONNECT_RSP path doesn't carry an AVDTP signal_id either.** It just accepts/rejects the L2CAP channel.
3. **Therefore the wire response sig_id is determined elsewhere**, and almost certainly preserves the request's sig_id (since BlueAngel's AVDTP layer parses each request, records the sig_id internally, and pairs the response to it per V13 §8.5).
4. **V5's redirect of sig 0x0c to the case-2 handler is therefore likely wire-correct**: the handler builds Service-Capabilities payload (the same content valid for both GET_CAPABILITIES_RSP and GET_ALL_CAPABILITIES_RSP per V13 §8.8), and the AVDTP layer's wire-frame TX preserves sig_id=0x0c from the request.
5. **Empirical risk remains** in the "wire frame builder localisation" gap — without finding the actual TX code that writes byte 1 of the response, we can't prove sig_id is preserved. A peer that exercises sig 0x0c (GET_ALL_CAPABILITIES) is the only definitive test.

**Net for the V5 ship** (committed in `e51da3f`): the risk language can be downgraded one notch — from "best-effort workaround that may break signal-id pairing" to "best-effort alias whose wire-correctness depends on AVDTP layer preserving the request sig_id, which is the architectural norm for BlueAngel-style stacks but not statically verified in our binary." Patch is still safe (sig 0x0c stub at 0xab4de currently does nothing; redirect to 0xaa924 cannot regress that), still empirically untested (no peer probes with sig 0x0c on the current CT matrix), and the only path forward to definitive verification is a peer that fires sig 0x0c.

**Anchors for the wire-frame-builder hunt** (next session, if anyone wants to close the V5 verification gap):

- Search for str / strb instructions writing a halfword/byte where bit pattern matches `(tlabel<<4)|(0<<2)|2` (= AVDTP RSP_ACK byte 0) — these are the wire byte 0 emitters.
- Check `fcn.00083014` (called by `fcn.0007d624`): looks up channel by CID; the channel struct after offset 0x100 might contain pending-response sig_id state.
- The TX path is likely fired by AVDTP signaling state machine (`fcn.000b0c30`, Trace #13c) on a state transition when GAVDP returns ACK via fcn.000aeb9c — so dynamic BPs at fcn.000b0c30's exit edges + the L2CAP TX call site after fcn.0007d624's state-change branch would catch it.

### Trace #16 (2026-05-10) — AVDTP signal-frame TX site localised; V5 wire-correctness upgraded to "verified by decoupling"

Goal of this trace: close the verification gap left open at the end of Trace #15 — find the actual wire-frame builder that writes byte 1 (sig_id) of the AVDTP signal response, and prove that V5's redirect of sig 0x0c into the sig 0x02 handler at 0xaa924 produces a wire-correct response (sig_id=0x0c, not sig_id=0x02).

**Method.** Searched for callers of `L2CAP_SendData` (= `fcn.0007d204`, identified via the "L2CAP_SendData state:%d return:%d" log string at `0xe0062`). Three callers fall in the AVDTP/AVCTP region: `fcn.000ae418` (AVDTP), `fcn.000b1c38`, and `fcn.000b31ac` (AVCTP-side). `fcn.000ae418` is the AVDTP signal-frame TX builder.

**fcn.000ae418 entry signature.**

```
0x000ae418  push {r3,r4,r5,r6,r7,r8,sb,lr}
0x000ae41c  mov  r4, r0                ; r4 = arg1 (channel/state context)
0x000ae41e  ldrh.w r0, [r0, #0x60]     ; r0 = halfword at [r4+0x60] (CID)
0x000ae422  bl   fcn.0007ccb4          ; CID lookup
```

The function operates on a per-channel context `r4` whose layout includes:
- `r4 + 0x10`  pointer to per-transaction state struct (call it `txn`)
- `r4 + 0x1c`  transaction-label byte
- `r4 + 0x20`  packet body buffer base
- `r4 + 0x5d / 0x5e / 0x5f`  packet header byte slots
- `r4 + 0x60`  L2CAP CID

**Wire byte 1 (sig_id) origin.** The single-packet path writes the sig_id byte from the per-transaction state struct, not from anything the dispatch handler at 0xaa924 set:

```
0x000ae472  ldr  r3, [r4, #0x10]      ; r3 = txn (per-channel transaction state)
0x000ae474  ldrb r1, [r3, #0xd]       ; r1 = txn->[0xd]  (msg_type / pkt_type latch)
0x000ae476  cmp  r1, 2                 ; check msg_type == RESPONSE_ACCEPT
0x000ae478  ittt ne
0x000ae47a  ldrh r3, [r3, #0xe]       ; r3 = txn->[0xe..0xf] halfword (sig_id at low byte)
0x000ae47c  add.w sb, r5, #-1
0x000ae480  strb r3, [r5, #-1]        ; *(r5-1) = low byte of txn->[0xe] → wire byte 1 (sig_id)
```

So **byte 1 of the response frame on the wire = `txn->[0xe]`**, where `txn = *(r4 + 0x10)`. This is the per-transaction state struct populated by the AVDTP request parser when the request is received; the sig-handler dispatch (the TBH table at 0xaa81e + sig-handlers like the one at 0xaa924) does not touch it.

**Wire byte 0 (header).** Built from `(tlabel << 4) | pkt_type<<2 | msg_type`:

```
0x000ae492  lsls r2, r6, 4             ; r2 = tlabel << 4
0x000ae494  ldrb r1, [r4, 0x1c]
0x000ae496  adds r0, r2, 4             ; +4 = pkt_type=01 (START), msg_type=00 (CMD)  — fragmented-cmd path
...
0x000ae510  strb.w r6, [r4, 0x5f]      ; wire byte 0
```

For the single-packet RSP_ACCEPT path the constant added is the response-msg-type bits (msg_type=10, pkt_type=00 → +2), not 4. Either way, byte 0 is composed from the transaction-label register `r6` (sourced from per-channel context), not from a dispatch-handler-tied constant.

**Frame TX call.** After header + body assembled at `r4+0x20..r4+0x60`:

```
0x000ae586  ldrh.w r0, [r4, 0x60]     ; r0 = CID
0x000ae58a  add.w  r1, r4, 0x20        ; r1 = packet base
0x000ae58e  bl     fcn.0007d204        ; L2CAP_SendData(cid, packet, ...)
```

**Net for V5 wire-correctness.**

The V5 patch redirects `tbh[11]` (sig 0x0c GET_ALL_CAPABILITIES) to dispatch into the sig 0x02 handler at 0xaa924. The sig_id byte that appears on the wire in the response frame is read from `txn->[0xe]`, populated by the request parser (in the L2CAP RX → state-machine path under fcn.000b0c30, not in the per-signal handler). The handler at 0xaa924 does not write to `txn->[0xe]`; the only byte it stores is to `[r6, 1]` where r6 is the AVDTP module's global state struct (loaded from `.got` slot 0xf9c14), and that store is a state-machine field (value `2`), not a wire-frame field.

Therefore, when a peer sends `GET_ALL_CAPABILITIES_REQ` (sig_id = 0x0c), the request parser stores 0x0c at `txn->[0xe]`, the dispatcher (post-V5) routes through the GET_CAPABILITIES handler at 0xaa924, the handler runs and updates state, and the response builder `fcn.000ae418` reads `txn->[0xe]` = 0x0c and writes 0x0c into the response frame's byte-1 slot. **Peer receives `GET_ALL_CAPABILITIES_RSP_ACCEPT` with sig_id=0x0c — wire-correct.**

**Risk language upgrade.** §9.13 (and the V5 patch comment in `patch_mtkbt.py`) can move from "wire-correctness plausible but not statically proven" to "wire-correct: the response sig_id byte is sourced from per-transaction state populated at request-parse time, decoupled from the dispatch handler." The remaining unverified surface is the response payload — V13 §8.8 mandates that GET_CAPABILITIES_RSP_ACCEPT and GET_ALL_CAPABILITIES_RSP_ACCEPT carry the same Service-Capability TLVs (the latter is a strict superset, but legacy capability servers may answer either with the same Service-Capability set, which is spec-permissible since the additional 1.3 capability categories — DELAY_REPORTING etc. — are all Optional). Since the handler at 0xaa924 emits the GET_CAPABILITIES Service-Capabilities payload, that's spec-conformant for either request.

**Anchor for any future GET_ALL_CAPABILITIES_REQ injection test:** patch a peer or test harness to issue sig 0x0c on the AVDTP signaling channel, capture the response, verify `byte1 = 0x0c` and the Service-Capabilities TLV list matches what stock issues for GET_CAPABILITIES.

### Trace #17 (2026-05-10) — PlayerApplicationSettings response builders disassembled

Goal: map calling conventions for the six PDU response builders + event 0x08 builder needed by Phase F4 (PApp Settings: ICS Table 7 rows 12-17 + 30). All seven builders are present in `libextavrcp.so` and PLT-linked from `libextavrcp_jni.so`, so the disassembly is purely compiler-RE — no missing-symbol gaps.

Final calling conventions (also tabulated in `ARCHITECTURE.md`):

**PDU 0x11 — `btmtk_avrcp_send_list_player_attrs_rsp`** (file 0x1e24, 80 B): `(conn, reject, n_attrs, *attr_ids)`. arg1=r0=conn, arg2=r1=reject_flag, arg3=r2=count of attribute IDs, arg4=r3=pointer to byte array. Stack buffer 14 B; emits `msg_id=0x20c=524`. Reject path: stores `1` at sp+7 and reject byte at sp+8.

**PDU 0x12 — `btmtk_avrcp_send_list_player_values_rsp`** (file 0x1e74, 92 B): `(conn, reject, attr_id, n_values, *values)`. r0/r1 same; r2=attr_id, r3=n_values, arg5 (sp+0x28 in callee frame) = pointer to value array. msg_id=0x20e=526.

**PDU 0x13 — `btmtk_avrcp_send_get_curplayer_value_rsp`** (file 0x1ed0, 94 B): `(conn, reject, n_pairs, *attr_ids, *values)`. r0/r1 same; r2=n_pairs, r3=attr_id_array, arg5 (sp+0x30) = value_array. Loop writes attr_ids at sp+12+i and values at sp+16+i. Wire format on AVRCP layer is interleaved (attr,val pairs) — `AVRCP_SendMessage` handles the IPC→wire repacking. Stack buffer 18 B; msg_id=0x210=528.

**PDU 0x14 — `btmtk_avrcp_send_set_player_value_rsp`** (file 0x1f2e, 40 B): `(conn, reject_status)`. Smallest builder; `reject_status==0` emits an ACK, otherwise emits a reject with that status code. msg_id=0x212=530, 8 B payload.

**PDU 0x15 — `btmtk_avrcp_send_get_player_attr_text_rsp`** (file 0x1f58, 228 B): `(conn, reject, idx, total, attr_id, charset, length, *str)`. Accumulator pattern parallel to `…send_get_element_attributes_rsp` from the existing T4: caller invokes once per attribute (idx=0..total-1) and the function emits `AVRCP_SendMessage` only when `idx+1==total AND total!=0` (or on reject). Internal static buffer at vaddr `0x5ea4` (`g_avrcp_playerapp_attr_rsp`); per-attribute string slot is 80 B (cap `0x4f`=79 B usable). Args5-8 on caller's stack at offsets 0,4,8,12. msg_id=0x214=532.

**PDU 0x16 — `btmtk_avrcp_send_get_player_value_text_value_rsp`** (file 0x203c, 252 B): `(conn, reject, idx, total, attr_id, value_id, charset, length, *str)`. Same accumulator shape as 0x15 but with both attr_id and value_id since each value gets its own text. Internal buffer at vaddr `0x5ffe` (`g_avrcp_playerapp_value_rsp`). Args5-9 on stack. msg_id=0x216=534.

**Event 0x08 — `btmtk_avrcp_send_reg_notievent_player_appsettings_changed_rsp`** (file 0x2720, 144 B): `(conn, reject, type, n, *attr_ids, *values)`. type: 0=INTERIM, 1=CHANGED. n is internally capped at 4 (max attribute count per AVRCP V13 §5.2). Args5-6 on stack. event_id constant `0x08` baked at sp+13. msg_id=0x220=544 (same msg_id as other notification events; the event_id byte at sp+13 is what differentiates).

**Common shape across all seven builders:**
- arg1 (r0) is always the conn buffer (= r5+8 in saveRegEventSeqId frame).
- arg2 (r1) is always reject/changed_flag: 0 = success path (full payload), !=0 = reject (truncated payload, status byte placed in a builder-specific slot).
- transId is sourced from `conn[17]` and written into the wire frame internally — no caller responsibility.
- AVRCP_SendMessage(conn, msg_id, sp_buffer, length) closes each builder.

**PLT linkage in `libextavrcp_jni.so` (verified by `r2 -A "ii~+player"`):**

| PDU / event | PLT addr |
|---|---|
| 0x11 list_player_attrs | 0x35d0 |
| 0x12 list_player_values | 0x35c4 |
| 0x13 get_curplayer_value | 0x35b8 |
| 0x14 set_player_value | 0x3594 |
| 0x15 get_player_attr_text | 0x35ac |
| 0x16 get_player_value_text | 0x35a0 |
| event 0x08 player_appsettings_changed | 0x345c |

All seven exist as proper PLT entries. None require new dynamic-linker resolutions.

**Implementation implications for F4 (next-iteration anchors):**

1. **Decoder dispatch.** The existing trampoline chain (T1 → T2 → T_charset → T_battery → T_continuation → T6 → T8 → T9 → T4 → fall-through-to-0x65bc) reads the PDU byte at sp+382 and routes by exact match. Adding F4 means six new PDU comparisons (0x11..0x16). Cleanest insertion is a single new T-trampoline that hosts all six dispatchers internally — call it T_papp (or T10 for naming continuity). It chains in *before* T4's fall-through, so unknown-to-F4 PDUs flow into the existing 0x20 GetElementAttributes handler.

2. **Inbound parameter parsing.** AVRCP body for PDUs 0x11-0x16 starts at sp+388 (after the 6-byte AV/C BT-SIG header at sp+378-383 and 2-byte param_length at sp+384-385). PDU 0x12 needs 1-byte attr_id; PDU 0x13 needs 1-byte n + n attr_ids; PDU 0x14 needs 1-byte n + n×{attr_id, value_id}; PDU 0x15 needs 1-byte n + n attr_ids; PDU 0x16 needs 1-byte attr_id + 1-byte n + n value_ids.

3. **State storage.** Y1MediaBridge already has the file-based contract for track metadata (`/data/data/com.y1.mediabridge/files/y1-track-info`, world-readable). Mirror this with `y1-papp-state` containing the current Repeat (id=2) and Shuffle (id=3) values. Y1MediaBridge writes when AndroidMediaController state changes; T_papp reads when responding to 0x13. Set commands (PDU 0x14) get applied by writing a `y1-papp-set` request file that Y1MediaBridge picks up via FileObserver and applies to the Android session via `MediaController.transportControls.setRepeatMode/setShuffleMode`.

4. **Event 0x08 emission.** Existing T2/T8/T9 register-notification trampolines remember the registering peer's transId in BSS. Add an analogous slot for event 0x08 transId (already-allocated globals: `tc_transId` for event 0x02, `pb_transId` for event 0x01, `pos_transId` for event 0x05; add `pas_transId`). Y1MediaBridge's onStateChange triggers re-firing T_papp's CHANGED-emit path when Repeat/Shuffle move.

5. **Padding budget.** Current `_trampolines.py` uses 1652 B of LOAD #1 padding (0xac54..0xb2c8); 2368 B remain. F4's T_papp is estimated at 400-600 B (six PDU dispatchers + one event re-emit path). Comfortable fit.

6. **Strict scope alignment.** Per AVRCP V13 §5.2.1 Player Application Settings, supporting any one PApp attribute makes ICS C.14 fire and rows 12-17 + 30 become Mandatory. Anchoring on Repeat (attr_id=2) + Shuffle (attr_id=3) maps cleanly to AndroidMediaController's repeat/shuffle modes (Y1's KitKat-era stack supports both via `setRepeatMode` / `setShuffleMode`). These are the two universally-implemented PApp attributes on real CT/TG implementations and represent the strictest spec-conformance posture without adding device-specific equalizer/scan plumbing that doesn't exist on Y1.

### Trace #18 (2026-05-10) — F4 iter2/3/4 staged plan: real Repeat/Shuffle state binding

iter1 ships hardcoded "Repeat OFF + Shuffle OFF, Set rejects with 0x06 INTERNAL_ERROR". The next iterations replace the hardcoded values with real state binding to the Y1 music app's `SharedPreferencesUtils` (where Repeat/Shuffle currently live). This trace captures the staged plan so the work can be sequenced cleanly without compounding unverified changes.

**Music app state surfaces (from `com/innioasis/y1/utils/SharedPreferencesUtils.smali`).**

```
public final getMusicIsShuffle()Z          // SharedPreferences key "musicIsShuffle"
public final setMusicIsShuffle(Z)V         // Editor.putBoolean + commit
public final getMusicRepeatMode()I         // SharedPreferences key "musicRepeatMode"
public final setMusicRepeatMode(I)V        // Editor.putInt + commit
```

`PlayerService.smali` defines the integer enum used by `musicRepeatMode`:

```
public static final REPEAT_MODE_OFF:I = 0x0
public static final REPEAT_MODE_ONE:I = 0x1
public static final REPEAT_MODE_ALL:I = 0x2
```

AVRCP 1.3 §5.2.4 Tbl 5.20 (Repeat) values: `0x01 OFF / 0x02 SINGLE / 0x03 ALL / 0x04 GROUP`. Mapping (Y1 ↔ AVRCP): `0 ↔ 0x01`, `1 ↔ 0x02`, `2 ↔ 0x03` (Y1 has no GROUP). **Verified 2026-05-09 via gdb-capture (`/work/logs/papp-gdb.log`):** Bolt sends `0x01/0x02/0x03` (never `0x04`) — bidirectional mapping is sound for both inbound Set and outbound GetCurrent.

AVRCP §5.2.4 Tbl 5.21 (Shuffle) values: `0x01 OFF / 0x02 ALL / 0x03 GROUP`. Mapping (Y1 ↔ AVRCP): `false ↔ 0x01`, `true ↔ 0x02`. **Verified 2026-05-09**: Bolt sends `0x02` (never `0x03`).

**Cross-app context handle (already available).** `Y1Application$Companion.getAppContext():Context` is reachable from any smali via `Y1Application;->access$getAppContext$cp()Landroid/content/Context;`. This eliminates the "no Context handle in static-ish methods" blocker noted in earlier deferral notes — `setMusicIsShuffle` / `setMusicRepeatMode` can sendBroadcast directly using the app-singleton context.

**Iter2 (read path).** Make `T_papp 0x13` and `T8 event 0x08 INTERIM` reflect real Y1 Repeat/Shuffle state.

- **B1 / B2 in `patch_y1_apk.py`.** Inject sendBroadcast at the end of `setMusicIsShuffle` and `setMusicRepeatMode`. Action `com.y1.mediabridge.PAPP_STATE_CHANGED`; extras `isShuffle:Z` (B1) and `repeatMode:I` (B2). Pre-condition: `.locals` bumped from 4 → 5 to give us a free local register to save the original `pN` value across the SharedPreferences write (the existing body clobbers `p1` via `sget-object p1, …editor`). Post-condition smali shape:
  ```
  .method public final setMusicIsShuffle(Z)V
      .locals 5
      move v4, p1                         ; iter2: save original boolean
      …existing body unchanged…
      ; iter2 inject — broadcast new value
      invoke-static {}, Lcom/innioasis/y1/Y1Application;->access$getAppContext$cp()Landroid/content/Context;
      move-result-object v0
      if-eqz v0, :iter2_skip
      new-instance v1, Landroid/content/Intent;
      const-string v2, "com.y1.mediabridge.PAPP_STATE_CHANGED"
      invoke-direct {v1, v2}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V
      const-string v2, "isShuffle"
      invoke-virtual {v1, v2, v4}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Z)Landroid/content/Intent;
      invoke-virtual {v0, v1}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V
      :iter2_skip
      return-void
  .end method
  ```
  B2 mirrors with `Z → I` and key `"repeatMode"`.

- **Y1MediaBridge.** Add `PappStateReceiver` inner class (BroadcastReceiver) registered for `com.y1.mediabridge.PAPP_STATE_CHANGED`. On receipt: read both extras (default to current cached values when only one is present), translate to AVRCP enum, write 2 bytes (`[avrcp_repeat, avrcp_shuffle]`) to `/data/data/com.y1.mediabridge/files/y1-papp-state` via the same atomic `tmp + rename` pattern as `y1-track-info`. `prepareTrackInfoDir()` creates the file with default `[1, 1]` (OFF, OFF) on first launch so trampolines can read it before any music-app write fires.

- **`T_papp 0x13` (GetCurrent).** Replace the hardcoded `papp_current_values` ADR with: open + read 2 bytes from `y1-papp-state` into stack scratch; if read fails, fall back to the hardcoded `[1, 1]`. Same pattern T4 uses for `y1-track-info`. Frame growth: +8 B for the file-I/O scratch + outgoing arg ptr.

- **`T8 event 0x08 INTERIM`.** Same file-read pattern; emit `n=2 + [(2, repeat_value), (3, shuffle_value)]`.

**Iter3 (write path).** Make `T_papp 0x14` (Set) actually apply changes.

- **`T_papp 0x14` (Set).** Replace the iter1 reject path with: open `/data/data/com.y1.mediabridge/files/y1-papp-set`, write 2 bytes `[attr_id, value]` from the inbound param body (sp+387 / sp+389 — first attr/value pair; multi-pair Sets fall back to first), close, ACK.

- **Y1MediaBridge.** Add `FileObserver` watching `y1-papp-set` for `MODIFY`. On fire: read the 2 bytes, dispatch by AVRCP attr_id (2 → setMusicRepeatMode mapped back from AVRCP enum; 3 → setMusicIsShuffle). Use sendBroadcast to a music-app-side BroadcastReceiver added by:

- **B3 in `patch_y1_apk.py`** (or new dynamic register in `Y1Application.onCreate`): a BroadcastReceiver listening for `com.y1.mediabridge.PAPP_SET_REQUEST` with extras `attr:I` + `value:I`. On receipt, calls back into `SharedPreferencesUtils.setMusicRepeatMode` / `setMusicIsShuffle` with the AVRCP→Y1 inverse mapping. The setters then fire B1/B2 broadcasts which Y1MediaBridge consumes — closing the loop.

- **PlayerService application of the change.** `setMusicRepeatMode` / `setMusicIsShuffle` only update SharedPreferences; the actual playback behavior (does the next track repeat? does the next track shuffle?) is driven by `PlayerService` reading those preferences at the right time. Confirming that PlayerService re-reads on every track-end transition (vs caching at startup) is open work.

**Iter4 (CHANGED).** Make event 0x08 fire CHANGED on real edges.

- **MtkBt cardinality NOP.** `BTAvrcpMusicAdapter.handleKeyMessage` has per-event-id cardinality checks (`if-eqz v5` patterns; same shape as the existing event-0x01 + event-0x02 NOPs) that drop the CHANGED firing if the cardinality field is 0. The event 0x08 sswitch arm needs the same NOP. Locate via grep on the smali / odex disassembly for the dispatch table.

- **Native jump-patch in `libextavrcp_jni.so`.** Add a `notificationPlayerAppSettingsChangedNative` analogue: identify which native method MtkBt calls for event 0x08 (or whether one exists), patch its first instruction to `b.w T_papp_changed`. T_papp_changed reads `y1-papp-state` + emits `reg_notievent_player_appsettings_changed_rsp` with `REASON_CHANGED`.

- **T9-style edge detect.** State byte in `y1-trampoline-state` (currently 16 B with last_play_status / last_battery_status at bytes 9-10) gains last_repeat_value / last_shuffle_value at bytes 11-12. T_papp_changed compares vs file bytes, emits CHANGED on inequality, updates state.

**Sequencing rationale.** Each iter is independently shippable + verifiable:
- Iter2 changes wire shape only on Get/INTERIM (read path); Set still rejects → iter2 adds zero new failure modes.
- Iter3 adds Set + write path → can be smoke-tested by issuing a Set from a peer CT and observing the music app's Repeat/Shuffle UI flip.
- Iter4 closes the CHANGED notification gap → can be smoke-tested by changing Repeat/Shuffle from the Y1 UI and watching for an AVRCP CHANGED frame on the wire.

Each iter is one commit, one OUTPUT_MD5 bump, one Y1MediaBridge versionCode bump.

### Trace #19 (2026-05-10) — F4 iter4 shipped: T9 papp edge block + Patch B4 listener; deviations from #18's staged plan

iter4 ships, but the as-built wiring deviates from the Trace #18 plan in three load-bearing places. Captured here so the next iteration can read the as-shipped reality first.

**JNI symbol enumeration (the unknown #18 flagged).** `BluetoothAvrcpService_notificationApplicationSettingChangedNative` exists at file `0x47b4` (radare2 `is~Native` against stock `libextavrcp_jni.so`). 248 B function, signature `(JNIEnv*, jobject*, signed char ack_type, signed char num_attrs, signed char count, _jbyteArray* attrs, _jbyteArray* values)`. Tail-calls `btmtk_avrcp_send_reg_notievent_player_appsettings_changed_rsp` at file `0x4878` (PLT `0x345c`) — same import T8 INTERIM and T9 papp CHANGED both call directly. So the dispatch path *exists* but is fed only from `BluetoothAvrcpService.run()`'s native-event listener loop, NOT from `BTAvrcpMusicAdapter.handleKeyMessage` (which is what T9 / sswitch_18a piggybacks would have needed for a direct hook).

**MtkBt smali finding (invalidates #18's "MtkBt cardinality NOP for event 0x08" plan).** `BTAvrcpMusicAdapter.handleKeyMessage`'s inner sparse-switch (`sswitch_data_21c`, smali line 1787) only contains arms for 0x1 / 0x2 / 0x9 (PlayStatus / Track / NowPlaying). There is **no** arm for 0x8. The only invocation of `notificationApplicationSettingChangedNative` in the entire MtkBt smali tree is in `BluetoothAvrcpService.smali::run()`'s pswitch_7f, fed by the native-event listener — wrong direction for proactive Y1→CT CHANGED. So iter4 cannot add a "matching cardinality NOP for event 0x08" — there's nothing to NOP.

**As-built design (replaces the cardinality NOP + new native jump-patch from #18).** Piggyback on T9 entirely:

1. T9's existing entry hook at `notificationPlayStatusChangedNative` (file `0x3c88`) and the existing MtkBt sswitch_18a cardinality NOP (msg=0x1, file `0x3c4fe` in `MtkBt.odex`) already wake the trampoline on every Y1MediaBridge `playstatechanged` broadcast. No new MtkBt edits.
2. Extend T9 with a fourth edge-detection block (papp): read `y1-track-info[795..796]` (repeat_avrcp / shuffle_avrcp), compare against `y1-trampoline-state[11..12]`, emit `reg_notievent_player_appsettings_changed_rsp(conn, 0, REASON_CHANGED, 2, &papp_attr_ids, &file[795])` on inequality. Frame grew 824→832 B (+8 B for the outgoing-args region the existing 0x08 INTERIM call shape needs at sp[0]/sp[4]; the values pointer is the file_buf address `sp+T9_OFF_FILE_REPEAT` — file_buf already holds `[r,s]` contiguously, no scratch copy needed).
3. T8 0x08 INTERIM also reads file[795..796] now (replacing the static `papp_current_values` ADR with `addw r0, sp, T8_OFF_FILE_REPEAT`). T_papp 0x13 GetCurrent retains the static fallback because Bolt postflash showed zero PDU 0x13 calls in practice; CTs subscribe to event 0x08 and never poll GetCurrent.

**Y1-side broadcaster (replaces #18's #B1/B2 setMusicIsShuffle/setMusicRepeatMode injections).** Patch B4 adds a single new class `com.koensayr.PappStateBroadcaster` implementing `OnSharedPreferenceChangeListener`. Registered against the `"settings"` SharedPreferences in `Y1Application.onCreate` (alongside the B3 PappSetReceiver registration). The listener fires on any write to any key but filters to `musicRepeatMode` / `musicIsShuffle`. On match, reads both live values via `SharedPreferencesUtils.INSTANCE.getMusicRepeatMode()` / `getMusicIsShuffle()`, maps to AVRCP enum bytes (Y1 0/1/2 → AVRCP 0x01/0x02/0x03 for Repeat; Y1 false/true → AVRCP 0x01/0x02 for Shuffle — the §5.2.4 mapping verified by Trace #18's gdb-capture), and broadcasts `com.y1.mediabridge.PAPP_STATE_DID_CHANGE` to package `com.y1.mediabridge` with extras `repeat_avrcp:I` + `shuffle_avrcp:I`.

Why a listener over per-setter sendBroadcast injections (#18's plan): the listener fires uniformly for AVRCP-driven Sets (which come in via Patch B3 calling `SharedPreferencesUtils.setMusicRepeatMode` / `setMusicIsShuffle`, which write the prefs and trip the listener) and Y1-UI toggles (in-app Settings screen calls the same setters). Single source of truth; no per-setter smali edit; no `.locals` bumping in `SharedPreferencesUtils.smali`. The listener is rooted via a static `sInstance` field inside `PappStateBroadcaster` so the GC doesn't reclaim it (Android holds `OnSharedPreferenceChangeListener` instances by weak reference).

**Y1MediaBridge as-built.** New `mPappStateReceiver` consumes `ACTION_PAPP_STATE_DID_CHANGE`, updates `mCurrentRepeatAvrcp` / `mCurrentShuffleAvrcp` (volatile bytes, default 0x01 OFF), calls `writeTrackInfoFile` (now writes `buf[795] = repeat_avrcp; buf[796] = shuffle_avrcp;`), and fires `com.android.music.playstatechanged` so MtkBt invokes `notificationPlayStatusChangedNative` → T9 picks up the edge. The intent extras are clamped to AVRCP §5.2.4 spec ranges (Repeat 0x01..0x04, Shuffle 0x01..0x03); out-of-range folds to OFF.

**No separate y1-papp-state file.** #18 proposed a 2-byte `y1-papp-state` file written by Y1MediaBridge and read by T_papp 0x13 + T8 0x08 INTERIM. As-shipped uses the existing y1-track-info schema's reserved bytes 795..799 instead — saves a write syscall (track-info is already written on every broadcast) and a read syscall (file_buf is already loaded by T8/T9 above the papp blocks). The y1-track-info schema comment at `MediaBridgeService.java:1502` already had `795..799 pad (PlayerApplicationSettings shuffle_flag / repeat_mode reservation)` — iter4 just makes that reservation real.

**Initial-state sync.** `Y1Application.onCreate` calls `PappStateBroadcaster.sendNow()` once on registration so a fresh music-app start (e.g. after reboot) syncs Y1MediaBridge to actual SharedPreferences state. There's no music-app-side query handler, so if Y1MediaBridge boots *after* the music app, the music-app initial broadcast was missed and Y1MediaBridge defaults to OFF/OFF until the first user toggle. In practice both processes are spawned at boot; the gap is short.

**Open verification work** (rolled into the active queue in `docs/BT-COMPLIANCE.md` §1):
- Hardware verify on Bolt: Y1-UI Repeat/Shuffle toggle → CT subscriber sees CHANGED frame following the edge.
- T_papp 0x13 GetCurrent live binding deferred (zero observed calls; not on critical path).

## Open questions
3. **A2DP SupportedFeatures (attribute 0x0311) value** — what feature bits does the served A2DP record advertise today, and what do A2DP 1.2 / 1.3 add? Confirms whether bumping A2DP 1.0 → 1.3 needs a paired feature-mask edit.
4. **A2DP / AVDTP version-byte authority** — confirm via experimental flash + sdptool re-capture: bump 0xeb9f2 (A2DP) from 0x00 to 0x03, capture, see if the wire moves. If yes, static byte drives advertisement (same shape as V1 / V2). If no, mtkbt has runtime version logic too and we need to find it.
5. **AVCTP-multiplicity** — V2 patches one of three AVCTP version sites. The other two (0xeba25 / 0xeba37) are unpatched at 0x0100 and may sit on dead code paths; verifying static-vs-runtime authority via experimental patch is the cheapest answer.
6. **GAVDP** — no separate SDP record advertised (UUID 0x1203 hits in the SDP region are part of the HFP / HSP records, not GAVDP). Per GAVDP 1.3 §6 versioning piggybacks AVDTP; no independent byte-patch needed.

Verification path for any triad version-byte bump: experimental flash + `tools/dual-capture.sh` + sdptool browse + a peer CT that exercises GET_CAPABILITIES (AVDTP sig 0x02) — the captured exchange tells us what we advertise *and* what the peer does with it.

# §9.2 A2dpSuspended Java approach reverted, HAL byte-patch landed (2026-05-09)

Context: §9.2 of `BT-COMPLIANCE.md` shipped in v2.7-v2.8 driving `audioManager.setParameters("A2dpSuspended=true|false")` from `Y1MediaBridge/MediaBridgeService.java::onStateDetected` on every play-state edge. Theory: setting A2dpSuspended=true would make `libaudio.a2dp.default.so::standby_l` skip its `a2dp_stop` call, leaving the AVDTP source stream alive across pauses (per AVDTP 1.3 §8.13 / §8.15).

**Empirical falsification** in capture `/work/logs/dual-tv-20260509-1538` (TV pause/play exercise, post-flash with §9.2 + Patch H″):

```
15:38:40.527 D Y1MediaBridge: State change: avrcpStatus=2 (PAUSED)
15:38:40.529 D A2dpAudioInterface: +setSuspended 1
15:38:40.546 I [A2DP] a2dp_stop. is_streaming:1            ← stream torn down INSIDE setSuspended(1)
15:38:40.546 D A2dpAudioInterface: -setSuspended 1
```

Every PAUSED edge produces this pattern: `+setSuspended 1` is followed within 17-31ms by `[A2DP] a2dp_stop. is_streaming:1`, on the same thread, before `-setSuspended 1` returns. The AOSP A2DP HAL implements `setSuspended(true)` as a *synchronous* tear-down. Stock semantics: A2dpSuspended is the system's way of telling A2DP "drop the stream so I can route audio elsewhere (e.g., for a phone call)" — not the protective skip we assumed.

Net effect comparison (TV pause/play exercises):

| Capture | Standby events | a2dp_stop (streaming) |
|---|---:|---:|
| `dual-tv-20260509-1410` (pre-§9.2) | 8 | 8 |
| `dual-tv-20260509-1538` (post-§9.2 Java) | 3 | 7 |

§9.2 reduced silence-induced standby events (8→3) but **introduced** an equal number of pause-edge-triggered teardowns (1 per PAUSED edge). Burst-on-resume + playhead-drift symptom unchanged.

**Pivot:** drop the Java setParameters call entirely. Add `patch_libaudio_a2dp.py` (AH1) which flips a single ARM cond byte (`0x0a` → `0xea` at file offset `0x000086ab`) inside `A2dpAudioStreamOut::standby_l`. Original conditional `beq 8684` becomes unconditional `b 8684`, making the call to `a2dp_stop@plt` at vaddr `0x86b0` unreachable. Standby still completes; AVDTP stream stays alive. No Java-side coupling.

**Patch H″ verification (same capture):** ✓ working — 0 kernel REPEAT events on `event4` (U1 holds), 0 `repeatCount=N>0` lines in `dumpsys-input.txt` (framework synthetic-repeat filter stays inactive because there are no synthetic repeats), clean DOWN/UP pairs in getevent. The framework-synthetic-FF cascade is closed.

## Trace #20 (2026-05-11) — Y1MediaBridge retirement Phase 1: parallel in-app `y1-track-info` writer (B5/iter1)

Phase 1 of `docs/PLAN-Y1MEDIABRIDGE-RETIREMENT.md` lands. Music app gains its own `y1-track-info` writer at `/data/data/com.innioasis.y1/files/`; trampolines still read `Y1MediaBridge`'s file at `/data/data/com.y1.mediabridge/files/`. Two writers run in parallel so the diff between them on every state edge is the verification gate for Phase 2's trampoline-path-string flip.

**Failure mode driving the pivot.** `Y1MediaBridge`'s `LogcatMonitor` scrapes `BasePlayerActivity` UI render lines (`刷新一次专辑图`) and `BaseActivity` LiveData observer lines (`播放状态切换 N`) to learn about state changes. Empirically (2026-05-10 1119/1409/1901/1910 captures referenced in the plan doc): the scrape only fires when the music app's UI activity is in the foreground. KEYCODE_HOME → audio keeps playing → bridge sees nothing for the duration of the backgrounded session → metadata + play-state on the wire freeze.

**Plan-vs-reality deltas surfaced by Phase 0 recon (`docs/RECON-MUSIC-APP-HOOKS.md`, commit `bee0416`).** Plan §4.2 assumed `MediaPlayer.OnCompletion/Prepared/Error` registration and `MediaMetadataRetriever` for tag extraction. Actual:

- Primary engine is `tv.danmaku.ijk.media.player.IjkMediaPlayer` (Bilibili IJK FFmpeg fork); secondary is `android.media.MediaPlayer` (`player2`). Listener interfaces differ — IJK uses `IMediaPlayer$OnCompletionListener` etc.; both engines have 3-listener registration sites in `initPlayer()` (line 875) and `initPlayer2()` (line 1091). The R8-generated `$$ExternalSyntheticLambda{0..5}` thunks call into `initPlayer$lambda-{10,11,12}` (IJK Completion/Prepared/Error in source order — confirmed by chasing the `$r8$lambda$*` accessors) and `initPlayer2$lambda-{13,14,15}` (MediaPlayer same). Six lambda bodies, six prepend hooks.
- `Static.setPlayValue(II)V` at `Static.smali:334` is THE canonical play-state-edge entry. The `BaseActivity.setObserve$lambda-7` log line (line 819) we currently scrape is the LiveData *observer's reaction* — fires after `setPlayValue` updates the LiveData. Hooking the observer means waiting for activity resume. Hooking `setPlayValue` catches every edge regardless of foreground state. setPlayValue's newValue space empirically includes 0/1/3/5 (mapped per Y1MediaBridge.LogcatMonitor's existing dictionary to STOPPED/PLAYING/PAUSED/STOPPED) plus internal Y1 transitions 2/4/6/7/8/9 which we ignore.
- Music app does no `MediaMetadataRetriever` — metadata lives in the Room `Song` entity (already populated at scan time). `TrackInfoWriter` reads `PlayerService.getPlayingMusic()` / `getPlayingSong()` Song getters directly, no re-extraction. No `duration` field on the entity though — duration comes from `PlayerService.getDuration()` (live from the engine).
- Single-process app — no `android:process` anywhere. `AvrcpBridgeService` (Phase 3) will be co-resident with `Y1Application` and `PlayerService` automatically. Plan §5 risk row #1 closed empirically.
- Music app does NOT emit `com.android.music.metachanged` / `playstatechanged` natively — Y1MediaBridge is the sole sender today. Phase 1 keeps Y1MediaBridge installed so those broadcasts still wake `T9` via the existing `MtkBt.odex` cardinality NOPs; Phase 3's `AvrcpBridgeService` will replicate the broadcast emission inside the music app.

**Architecture as-built (Phase 1).** Four new classes under `com.koensayr.y1.*` (smali sources at `src/patches/inject/com/koensayr/y1/`, copied into `smali_classes2/` at patcher time):

- `trackinfo.TrackInfoWriter` — singleton state holder + atomic file writer. Mirrors the byte schema and field semantics of `MediaBridgeService.writeTrackInfoFile` (1104 bytes, atomic tmp+rename, `setReadable(true, false)` for the `bluetooth` uid). audio_id at bytes 0..7 from `syntheticAudioId(path) = (path.hashCode() & 0xFFFFFFFFL) | 0x100000000L` — same hash Y1MediaBridge falls back to when MediaStore _ID lookup fails, so the byte should match the bridge's value when both are running against the same Song entity. State fields: `mPlayStatus` (B), `mPositionAtStateChange` (J), `mStateChangeTime` (J — `SystemClock.elapsedRealtime()` for lockstep with T6's `clock_gettime(CLOCK_BOOTTIME)`), `mPreviousTrackNaturalEnd` (Z), `mPendingNaturalEnd` (Z — latched between `onCompletion` and the next `onTrackEdge`), `mBatteryStatus` (B), `mRepeatAvrcp` (B, default 0x01 OFF), `mShuffleAvrcp` (B, default 0x01 OFF). All public mutators are `declared-synchronized` on `INSTANCE` with manual `monitor-enter`/`monitor-exit` (Dalvik doesn't auto-wrap on the access flag).
- `playback.PlaybackStateBridge` — stateless static dispatcher. `onPlayValue(II)V` maps newValue → AVRCP byte. `onPrepared/onCompletion/onError` fire from the listener lambdas; `onCompletion` latches natural-end (player engine guarantees `OnCompletion` fires only at EOS — no extrapolated-vs-duration heuristic needed); `onPrepared` consumes the latch into `mPreviousTrackNaturalEnd`, resets position+time, flushes; `onError` clears the latch.
- `battery.BatteryReceiver` — `Intent.ACTION_BATTERY_CHANGED` consumer, sticky-broadcast value processed inline at registration so cold boot has a real bucket. Same FULL_CHARGE > EXTERNAL > CRITICAL > WARNING > NORMAL bucket ordering as `Y1MediaBridge.handleBatteryIntent`.
- `papp.PappSetFileObserver` — `FileObserver(/data/data/com.innioasis.y1/files/y1-papp-set, CLOSE_WRITE)`. Reads 2 bytes (attr_id, value), maps to Y1 enum, calls `SharedPreferencesUtils.setMusicRepeatMode/setMusicIsShuffle` directly. Inert in Phase 1 because trampolines still write the bridge's path; goes live in Phase 2 when the trampoline path strings flip. Pre-deployed so Phase 2 doesn't need a fresh smali edit, only an `_trampolines.py` constant change.

Hook injection sites (Patch B5.1..B5.4 in `patch_y1_apk.py`):

| Inject | Anchor | Why |
|---|---|---|
| `Static.setPlayValue` top | After `.locals 5` | Canonical state-edge entry (recon §2). |
| `PlayerService` six lambda tops | After each lambda's `.locals N` | Six listener entries (one per (engine × callback) pair). Empty-arg `invoke-static {}` so no register pressure on the lambda's existing scratch use. |
| `Y1Application.onCreate :cond_3` | Between B3 and B4 | Order matters: `TrackInfoWriter.init` must run before `PappStateBroadcaster.sendNow` (which calls `setPapp` → `flushLocked` — would no-op if `mFilesDir` was null). |
| `PappStateBroadcaster.sendNow` tail | After `sendBroadcast` | Phase-1 parallel write: keep the Y1MediaBridge broadcast (so the bridge's `mCurrentRepeatAvrcp` updates and its file matches), and ALSO call `TrackInfoWriter.setPapp` directly (so the music-app file matches). |

**Smali smoke-test gotcha #1.** First apktool reassembly failed with `4294967296 cannot fit into an int` on `const-wide/32 v0, 0x100000000L`. `const-wide/32` literal is 32-bit sign-extended; 2^32 needs full `const-wide` (64-bit). Fixed both occurrences in `syntheticAudioId`. Also confirmed `declared-synchronized` is purely an access-flag annotation in Dalvik — explicit `monitor-enter`/`monitor-exit` are required regardless.

**Runtime gotcha #3 — `PlayerService.getDuration()` during prepareAsync nukes the new MediaPlayer (B5/iter1.3).** Second on-device boot of B5 (post-iter1.2 hardening, commit `2101495`) booted clean but playback stuck at 0:00 on every track requiring the `android.media.MediaPlayer` engine (`isUseIjk(path) == false`). Captured in `/work/logs/logcat-20260510-2132.log`. Trace:

```
436:D/MediaPlayerService: [3] prepareAsync                            # native MP starts async prep
437:D/DebugY1 restore: restart 使用mediaPlayer完毕                    # music app's restart returns
438:E/MediaPlayer: Attempt to call getDuration without a valid mediaplayer
439:E/MediaPlayer: error (-38, 0)                                     # async OnError
440:I/DebugY1 BaseActivity: 播放状态切换   1                          # Static.setPlayValue(1, 8)
...
484:D/DebugY1 PlayerService: MediaPlayer Crash @414547b8 -38 0        # lambda-15 fires
485:D/DebugY1 PlayerService: player onError 2
486:D/MediaPlayerService: [3] reset                                   # MP back to Idle, stuck
```

Root cause: the music app's restart sequence calls `Static.setPlayValue(1, 8)` to mark PLAYING **after** `prepareAsync` is dispatched but **before** `OnPrepared` arrives. My `Static.setPlayValue` hook fires synchronously → `PlaybackStateBridge.onPlayValue` → `TrackInfoWriter.setPlayStatus` → `flushLocked` → `PlayerService.getDuration()`. `PlayerService.getDuration()` (smali line 2922 `:cond_1`) delegates to `player2.getDuration()` for non-IJK paths. The C++ `MediaPlayer::getDuration` runs on the brand-new MediaPlayer instance #3 (still in `Preparing` state), logs `Attempt to call getDuration without a valid mediaplayer`, returns `INVALID_OPERATION`. The native `MediaPlayer` then transitions into Error state and posts an async `OnError(-38)` — which triggers stock's lambda-15 reset(), leaving the player Idle forever. `playerIsPrepared` never becomes `true`, BasePlayerActivity polls `prepare: false` indefinitely.

The stock app never queries `getDuration` between `prepareAsync` and `OnPrepared`. My flush did, on every play-state edge.

Fix: gate every `PlayerService.getDuration()` call inside `TrackInfoWriter` on `PlayerService.getPlayerIsPrepared()` (a pure `iget-boolean`, safe in any state). When not prepared, write `0` for duration — same "unknown" sentinel `Y1MediaBridge` uses. Two call sites in `flushLocked` (the per-attribute write at offset 776 + the PlayingTime ASCII string at 832) and one in `computeLivePositionLocked` (the duration-cap on extrapolated position). All three guarded.

Lesson for Phase 3: when `AvrcpBridgeService` Binder methods read playback state for inbound CT requests, treat any accessor that touches the underlying native player (`getDuration`, `getCurrentPosition`, `seekTo`, `setVolume`) as unsafe outside the prepared window. Use the same `getPlayerIsPrepared` gate.

**Runtime gotcha #2 — MultiDex cache + system-app reflash interaction (B5/iter1.1).** First on-device run threw `java.lang.NoClassDefFoundError: com.koensayr.y1.trackinfo.TrackInfoWriter` at `Y1Application.onCreate(Y1Application.kt:137)`, with the dalvik verifier logging `VFY: unable to resolve static field 47282 (INSTANCE) in Lcom/koensayr/y1/trackinfo/TrackInfoWriter;` at class-load time and the runtime resolution failing even after `MultiDex` reported `install done`. Captured in `/work/logs/logcat.log` (pid 649 → first crash; pid 850 → second attempt with full trace including the MultiDex install logs). Root cause: classes2.dex extraction is cached under `/data/data/com.innioasis.y1/code_cache/secondary-dexes/`, which survives `/system/app/com.innioasis.y1/com.innioasis.y1.apk` reflashes; MultiDex 1.0.x on Dalvik 1.6 reuses the cached pre-patch classes2.dex (`loading existing secondary dex files / load found 1 secondary dex files`) and the new `Lcom/koensayr/y1/*` classes are nowhere to be found at runtime. Fix: route the four B5 classes into `smali/` (primary DEX) instead of `smali_classes2/` so they load with `Y1Application` itself — same DEX placement as B3/B4. apktool 2.9.3 / smali 3.0.3 reassembly succeeded; both `classes.dex` (9.2 MB, ~+25 methods) and `classes2.dex` (8.97 MB, unchanged) under the 64K-method cap. All four classes verified in `classes.dex` via `unzip -p classes.dex | strings | grep koensayr/y1`.

**Phase 1 → Phase 2 verification gate.** Both files exist on device and update in lockstep (`adb shell md5sum /data/data/com.innioasis.y1/files/y1-track-info /data/data/com.y1.mediabridge/files/y1-track-info`). The plan idealised this as "byte-exact within ±100 ms"; in practice `mStateChangeTime` (low 32 bits of `SystemClock.elapsedRealtime()` at the edge) will differ by the few-ms gap between when each writer fires, and `audio_id` may differ if Y1MediaBridge's MediaStore `_ID` lookup succeeds (music app side always uses the synthetic hash). The realistic gate is "all CT-visible fields (Title/Artist/Album/Genre/Duration/PlayStatus/NaturalEnd/Battery/Repeat/Shuffle/TrackNumber/TotalTracks/PlayingTime) match byte-for-byte; clock + audio_id allowed to differ within their natural skew." Plus the foreground/background test: `adb shell input keyevent KEYCODE_HOME` → trigger track change via Bluetooth PASSTHROUGH → music-app file updates (existing `LogcatMonitor` scrape would not).

**Open work (handed to Phase 2).** `_trampolines.py` flips three `asciiz` literals from `/data/data/com.y1.mediabridge/files/` to `/data/data/com.innioasis.y1/files/`; re-pin `libextavrcp_jni.so` `OUTPUT_MD5`. After the flip, `PappSetFileObserver` becomes the live consumer of T_papp 0x14 writes (replacing Y1MediaBridge's FileObserver). Y1MediaBridge keeps writing to its own path but to a file the trampolines no longer read — dead but installed. Phase 3 then adds `AvrcpBridgeService` to the music app manifest and uninstalls Y1MediaBridge.

## Trace #21 (2026-05-11) — Y1MediaBridge retirement Phase 2: trampoline file-path cutover

Phase 1 (Trace #20) shipped the music app's `TrackInfoWriter` as a parallel writer at `/data/data/com.innioasis.y1/files/y1-track-info`, with on-device `md5sum` comparison against Y1MediaBridge's path confirming all CT-visible fields match byte-for-byte except `state_change_time_ms`, `position_ms`, and (when MediaStore `_ID` lookup succeeds bridge-side) `audio_id`. Verified 2026-05-11 on Killswitch Engage "My Last Serenade (live)":

```
audio_id       OK   music=00000001a957a7e8  bridge=00000001a957a7e8
title          OK   music=4d79204c61737420536572656e616465...  bridge=…
artist         OK   music=4b696c6c73776974636820456e676167  bridge=…
album          OK   music=54686520456e64206f66204865617274…  bridge=…
duration       MISMATCH  music=0000c288 (49800 ms)  bridge=0000c25e (49758 ms)
play_status    OK   music=01  bridge=01
battery        OK   music=00  bridge=00
repeat         OK   music=04  bridge=04
shuffle        OK   music=01  bridge=01
```

The 42 ms duration delta is expected: Y1MediaBridge re-runs `MediaMetadataRetriever` (Xing/LAME header parse) while `TrackInfoWriter.flushLocked` calls `MediaPlayer.getDuration()` (codec-reported). For VBR MP3 those routinely disagree by tens of ms; the trampolines round to seconds for AVRCP `PLAYING_TIME` anyway. `audio_id` matched because both writers fell through to the path-hash fallback (`(hash & 0xFFFFFFFFL) | 0x100000000L`) — the Phase 2 blocker I flagged in Trace #20 ("MediaStore-id mismatch would force a CT track-change on every state edge") is not a problem in practice.

**Path-string flip.** Three `asciiz` literals in `_trampolines.py` (lines 2267 / 2270 / 2273) repointed from `/data/data/com.y1.mediabridge/files/*` to `/data/data/com.innioasis.y1/files/*`. Each path string shrinks by 2 bytes (`com.y1.mediabridge` = 18 chars → `com.innioasis.y1` = 16 chars); all PC-relative references to the path labels resolve through `Asm.label` so the assembler re-computes offsets automatically. Trampoline blob grows by net 700 bytes vs. the figures in `docs/PATCHES.md` that were stale (pre-T_papp); current size = 2736 bytes (was nominally tracked as 2036 / 1652 in older docs), free padding after LOAD #1 = 1284 bytes.

**`OUTPUT_MD5` transition.** Set `OUTPUT_MD5 = None` temporarily, re-ran `patch_libextavrcp_jni.py /work/v3.0.2/system.img.extracted/lib/libextavrcp_jni.so`. Pre-patch verification confirmed 9/9 sites OK against `STOCK_MD5 = fd2ce74db9389980b55bccf3d8f15660`. Post-patch verification 9/9 sites OK. New `OUTPUT_MD5 = f021e71d12c170f2e135281d37ba8477` (was `5b7f5ae685c4c9299f36b1b3f88d564c` in the v1+B5 build). Output size unchanged at 50,992 bytes — confirms the 6-byte path-string shrink fits within existing alignment slack and no LOAD #1 program-header bump is needed.

**Cold-boot gap (known limitation).** Y1MediaBridge auto-starts at boot via its `BOOT_COMPLETED` receiver; the music app's `Y1Application.onCreate` runs only when the music process is launched. Between reboot and first music-app launch, MtkBt's trampolines `open()` the music-app files and get ENOENT (no `O_CREAT` on read paths). Trampolines fail-soft on ENOENT (return INTERIM with sentinel/zero values). In practice the user launches music before connecting a CT, so this only affects the contrived "boot → connect CT immediately" path. Phase 3's `AvrcpBridgeService` (exported, bound by MtkBt) will cold-start the music process implicitly via `bindService`. Mitigation alternative if Phase 3 slips: add a `BOOT_COMPLETED` receiver to the music app that calls `TrackInfoWriter.prepareFiles()` to materialise the files.

**Patches B3 status post-cutover.** B3 (`com.koensayr.PappSetReceiver`) is now inert: Y1MediaBridge's `FileObserver` on its own `y1-papp-set` no longer fires (the trampolines write to the music-app path), so the bridge no longer re-broadcasts `SET_REPEAT_MODE` / `SET_IS_SHUFFLE` intents that B3 listens for. B5's `PappSetFileObserver` is the live consumer. B3 is kept installed as a transitional safety net; Phase 3 / 4 will remove it.

**B4 wake-up loop after Phase 2.** `PappStateBroadcaster.sendNow` calls `TrackInfoWriter.setPapp(repeat, shuffle)` directly (writing the music-app's `y1-track-info[795..796]`) and continues to fire the `com.y1.mediabridge.PAPP_STATE_DID_CHANGE` broadcast. Y1MediaBridge still consumes that broadcast and fires `com.android.music.playstatechanged`, which is what wakes T9 to emit AVRCP §5.4.2 Tbl 5.36 `PLAYER_APPLICATION_SETTING_CHANGED CHANGED` on the wire. The bridge's own y1-track-info write (also at byte 795..796) is a dead path. After Y1MediaBridge is retired, the music app will need to fire `playstatechanged` itself in `setPapp` — currently it relies on the bridge as a broadcast relay.

**Active docs updated for Phase 2 reality.** `ARCHITECTURE.md` "Music app state-writer lifecycle" section now describes the in-process writer chain (TrackInfoWriter + PlaybackStateBridge + BatteryReceiver + PappSetFileObserver + PappStateBroadcaster) replacing the old "Y1MediaBridge lifecycle" walk; cross-component state dependencies table re-anchored to the music app's filesDir; `BT-COMPLIANCE.md` ICS rows + risk table refreshed; `PATCHES.md` B3/B4/B5 narratives reflect Phase 2 owner; `_trampolines.py` and `patch_libextavrcp_jni.py` source comments stripped of stale "Y1MediaBridge writes" framing.

**Phase 2 → Phase 3 verification gate** (per `docs/PLAN-Y1MEDIABRIDGE-RETIREMENT.md` §6): AVRCP CT cold-connect → metadata visible within one polling cycle; T_papp 0x14 Set still round-trips into music-app SharedPreferences via `PappSetFileObserver`; play/pause edges still drive CHANGED notifications. Verified on hardware 2026-05-11 via a Sonos dual-capture (`/work/logs/dual-sonos-20260511-0733/`): 7,272 msg=544 frames (RegisterNotification responses), 227 msg=540 (GetElementAttributes responses with size=644, carrying Title/Artist/Album bytes), 18 msg=520 (PASSTHROUGH ACKs). No FATAL / NoClassDefFoundError. Sonos display tracked correctly per the user's confirmation. Gate passed; Phase 3 unblocked.

## Trace #22 (2026-05-11) — Y1MediaBridge retirement Phase 3: AvrcpBridgeService Binder lands in the music app

Phase 3 retires `Y1MediaBridge.apk` by hosting its `IBTAvrcpMusic` + `IMediaPlaybackService` Binder inside the music app itself. Two new smali classes (`com.koensayr.y1.avrcp.AvrcpBridgeService` + `AvrcpBinder`) implement a minimum-viable Binder; `apply.bash` no longer installs `Y1MediaBridge.apk` and now removes any pre-existing copy from `/system/app/`.

**Recon (already done in Phase 0; reused).** `docs/RECON-MUSIC-APP-HOOKS.md` §7 has the full transaction-code table for both interfaces (38 codes on IBTAvrcpMusic, 32 on IMediaPlaybackService, 8 on IBTAvrcpMusicCallback). Y1MediaBridge's `MediaBridgeService.java::onTransact` ships a working reference implementation for every code; Phase 3 mirrors its dispatch shape but in smali.

**Minimum-viable scope.** ARCHITECTURE.md's existing note on the Binder role — "in the post-patch architecture this Java path is largely unused; the C-side trampolines deliver the real metadata + control on the AVRCP wire" — set the bar low. The Sonos log from Trace #21 confirmed it: `Y1MediaBridge: notifyAvrcpCallbacks code=1 — no callbacks registered` appeared 20× alongside 7,272 T9 wakeups via the broadcast path. MtkBt never actually transacted on the Java callback path; the broadcast wake path drove everything. So `AvrcpBinder.onTransact` implements: code 1 (`registerCallback`) — stash IBinder; code 2 (`unregisterCallback`); code 3 (`regNotificationEvent`) — ACK true (critical: returning false leaves MtkBt's `mRegBit` empty and notifyTrackChanged gets dropped pre-emit); code 5 (`getCapabilities`) — return `[0x01, 0x02]`; codes 6-13 — broadcast media keys to PlayControllerReceiver via DOWN+UP `ACTION_MEDIA_BUTTON`. Every other code: `writeNoException` + return true. Total smali: ~330 lines for AvrcpBinder + ~280 lines for AvrcpBridgeService (Service shell + callback list + media-key sender).

**Descriptor skip.** Same defensive pattern Y1MediaBridge used: skip `strictModePolicy` (int32) + descriptor (string) and dispatch purely by transact code. `enforceInterface` has historically aborted on ROM-variant descriptor mismatches, leaving cardinality at 0. Code path tested cleanly: apktool b's smali compile succeeds (`Smaling smali_classes2 folder into classes2.dex`); androguard's AXMLPrinter re-parses the manifest splice; method count delta is +38 in classes2.dex (52,935 → 52,973, well under the 64K cap; 12,563 slots free).

**Method-count routing.** classes.dex sits at 65,330/65,536 (99.7%, ~176 slots free) after Patch B5. AvrcpBridgeService + AvrcpBinder route to `smali_classes2/com/koensayr/y1/avrcp/` — secondary DEX, where there's 12K+ method headroom. Trade-off: MultiDex 1.0.x on Dalvik 1.6 caches the extracted classes2.dex under `/data/data/com.innioasis.y1/code_cache/secondary-dexes/`, and that cache survives every `apply.bash` invocation (`mtk.py w android` writes the system partition only — no `apply.bash` flag touches userdata). Mitigation: `apply.bash` emits an unconditional instruction at the end of `--avrcp` telling the user to `adb shell rm -rf /data/data/com.innioasis.y1/code_cache/secondary-dexes/` before reboot. Earlier docs (Trace #20 + the dex-budget memory) said `--all` would reprovision userdata as a side effect; that was wrong — corrected here. Every `--avrcp` install needs the manual cache-clear step until/unless we wire a music-app-side cache invalidator (an Application.onCreate shim that calls `getCodeCacheDir().delete()` if a sentinel class is missing).

**AndroidManifest patch (the real engineering decision).** `apktool d --no-res` leaves the manifest as binary AXML; we have no aapt2 binary on the host (`/tmp/bak/prebuilt/linux/aapt2` is 32-bit x86, host is 64-bit; Rocky 10 has no aapt2 package; apktool's bundled aapt2 fails with `cannot execute binary file`). Three paths considered:

1. **Python AXML splicer** — wrote `src/patches/_axml.py` (~250 lines). Reads the binary AXML chunk-by-chunk, exposes `start_element` / `end_element` / `attr_string` / `attr_bool` / `attr_int` builders, and a `write` that re-emits the file with a freshly-serialized string pool. Round-trip on the unmodified manifest is byte-identical (md5 match). Strings get APPENDED to the pool past the resource-mapped prefix (first 31 slots are android.R.attr.* via ResourceMap; new strings at index 172+ have no ResourceMap entry, no conflict).
2. **Install Android SDK + aapt2** — adds a permanent ~1.5 GB toolchain dep. Wrong direction since Phase 4 was meant to drop the gradle dep anyway.
3. **Tiny shim APK** — defeats the "retire to a single APK" intent of the plan.

Went with option 1. Manifest splice inserts (just before `</application>`):

```xml
<service android:name="com.koensayr.y1.avrcp.AvrcpBridgeService" android:exported="true">
  <intent-filter android:priority="100">
    <action android:name="com.android.music.MediaPlaybackService"/>
    <action android:name="com.android.music.IMediaPlaybackService"/>
  </intent-filter>
</service>
```

Verified the splice via androguard's `AXMLPrinter.get_xml()` — independent reader re-emits the exact XML we intended. New manifest is 23,516 bytes (was 22,916; +600 ≈ new chunks (340) + 3 new strings × ~85 bytes each + alignment).

**Output-APK assembly bug surfaced.** The patcher's final zip-rebuild loop swaps only `classes.dex` / `classes2.dex` from staging; manifest comes from the stock APK regardless. Phase 1 + 2 never patched the manifest so the bug was invisible. Fixed in the same commit: also swap `AndroidManifest.xml` from staging.

**Wake-up trigger migration.** Y1MediaBridge previously fired `com.android.music.playstatechanged` on:
- play/pause edge (stock music app fires it too, so duplicative — OK)
- battery bucket transition (Y1MediaBridge-only — now music-app responsibility)
- papp change via `PAPP_STATE_DID_CHANGE` intent bridge (Y1MediaBridge-only — now music-app responsibility)
- 1 s position tick while playing (stock music-app fires it, no change needed)

After Y1MediaBridge is removed, the music app must emit `playstatechanged` for battery + papp. Phase 3 changes:
- `BatteryReceiver.onReceive` (smali): after `TrackInfoWriter.setBattery`, fire `Context.sendBroadcast(Intent("com.android.music.playstatechanged"))` wrapped in `try/catch(Throwable)`.
- `PappStateBroadcaster.sendNow` (Patch B5.4 in `patch_y1_apk.py`): same pattern after `TrackInfoWriter.setPapp`. The existing `com.y1.mediabridge.PAPP_STATE_DID_CHANGE` broadcast is retained as a no-op for the transition window — goes to no listener once Y1MediaBridge is uninstalled.

**apply.bash changes.**
- Dropped the `assembleDebug` prerequisite and the `Y1MediaBridge.apk` install step.
- Added defensive removal of `/system/app/Y1MediaBridge.apk` / `Y1MediaBridge.odex` / `Y1MediaBridge/` at mount time (covers users upgrading from a previous --avrcp build).
- Added a post-flash usage note pointing at the code_cache invalidation `adb shell` command and the `pm uninstall com.y1.mediabridge` defensive cleanup (covers any prior non-system-app installs).

**Patcher smoke test passed.** Full `--clean-staging` run:
- All B1..B5 patches apply as before.
- B6.1 copies `AvrcpBridgeService.smali` + `AvrcpBinder.smali` into `smali_classes2/com/koensayr/y1/avrcp/`.
- B6.2 splices the manifest via `_axml.py`.
- `apktool b` smaling phase: clean, no errors.
- DEX method counts: classes.dex 65,330 (unchanged), classes2.dex 52,973 (+38 vs Phase 2 baseline).
- Output APK contains the spliced manifest (verified via androguard) and both new smali classes.

**Phase 3 → 4 verification gate** (per plan §6): `pm list packages | grep mediabridge` empty post-flash; `ls /system/app/Y1MediaBridge*` returns "No such file or directory"; MtkBt's adapter logs `MMI_AVRCP: PlayService onServiceConnected className:com.koensayr.y1.avrcp.AvrcpBridgeService` (not the old `com.y1.mediabridge`); all three CT scenarios (Bolt/Kia/TV — Sonos deprecated) repeat the Phase 2→3 gate. To be verified on hardware after reflash.

**Known unknowns to watch on first flash.**
- **Cold-boot Binder bind**: MtkBt's bindService should cold-start the music app's process via Android's standard service-binding flow, which means `Y1Application.onCreate` runs (registering TrackInfoWriter etc.) before MtkBt's `onServiceConnected` callback fires. If this races (e.g. `onServiceConnected` arrives before `Y1Application.onCreate` completes), the `AvrcpBridgeService.onCreate` order may be off. The fix would be to make `AvrcpBridgeService.onCreate` defensively trigger `TrackInfoWriter.init` itself, but Android's lifecycle guarantees Application.onCreate runs before any component's onCreate, so this race shouldn't happen in practice. Monitor for it.
- **PackageManager resolution priority**: the intent-filter ships at `android:priority="100"`. Y1MediaBridge's intent-filter has no priority (default 0). PMS should resolve to the music app's filter on first install. If both APKs are present (transition window), the music app wins. After Y1MediaBridge is removed, only the music app has a matching filter.
- **code_cache staleness**: MultiDex 1.0.x reuses any cached classes2.dex it finds at `/data/data/com.innioasis.y1/code_cache/secondary-dexes/`, regardless of whether the underlying APK changed (observed in Trace #20). `apply.bash` never touches /data, so the cache persists across reflashes — `--all`, `--avrcp`, and any other combination of flags. The mandatory post-flash adb-shell step is the cache-clear; no flag bypasses it.

## Trace #23 (2026-05-11) — Phase 3 v1 stand-down: AndroidManifest.xml splice rejected by JarVerifier

Phase 3 v1 (commit `032f655`) shipped an AndroidManifest.xml splice via the new `src/patches/_axml.py` editor, adding a `<service>` declaration for `com.koensayr.y1.avrcp.AvrcpBridgeService` with a priority-100 intent-filter for `com.android.music.MediaPlaybackService`. The intent: MtkBt's `bindService` would resolve to the music app instead of Y1MediaBridge, retiring `Y1MediaBridge.apk` entirely.

User flashed Phase 3 v1 via mtkclient. Boot hung at the boot animation indefinitely. New logcat capture (`/work/logs/logcat-20260511-0927.log`) shows PackageManager rejecting the patched music APK during `/system/app/` scan:

```
W/PackageParser(523): java.lang.SecurityException:
    META-INF/MANIFEST.MF has invalid digest for AndroidManifest.xml
    in /system/app/com.innioasis.y1_3.0.2.apk
E/PackageParser(523): Package com.innioasis.y1 has no certificates
    at entry AndroidManifest.xml; ignoring!
W/PackageManager(523): Failed verifying certificates for package:com.innioasis.y1
D/PackageManager(523): scan package: /system/app/com.innioasis.y1_3.0.2.apk,
    elapsed time = 1831ms
```

PackageManager dropped `com.innioasis.y1` entirely. With no music app installed, the system's launcher (which lives in `com.innioasis.y1.activity.MainActivity`) couldn't start, BootCompleted never fired, the boot animation looped forever.

**Root cause.** `META-INF/MANIFEST.MF` records a SHA1-Digest for each file in the APK. JarVerifier, called via `JarFile.getCertificates(AndroidManifest.xml)` in `PackageParser.collectCertificates`, reads `AndroidManifest.xml` and SHA1s the bytes; comparison against MANIFEST.MF's recorded digest fails on our modified manifest → throws SecurityException → `getCertificates()` returns null → PackageParser reports "no certificates" → package dropped.

Phase 1 + 2 worked because **JarVerifier only digest-checks `AndroidManifest.xml` during scan**. It does NOT check `classes.dex` / `classes2.dex` / `resources.arsc` at parse time. Empirically: we modified classes.dex (Patch B5) without issue. The moment we modified AndroidManifest.xml, JarVerifier fired.

**Why we can't re-sign.** Updating the SHA1-Digest in MANIFEST.MF would invalidate CERT.SF's per-section SHA1 (which signs the MANIFEST.MF section bytes). Updating CERT.SF invalidates CERT.RSA's signature over CERT.SF. Re-signing CERT.RSA requires the OEM platform private key, because `com.innioasis.y1` declares `android:sharedUserId="android.uid.system"`. Without that key the package would either be rejected entirely (unsigned check) or rejected for not matching the platform-cert prerequisite of `android.uid.system`.

**Why Y1MediaBridge.apk works.** Different package (`com.y1.mediabridge`), self-signed test cert, no `sharedUserId` constraint. /system/app/ doesn't require any specific signing key for arbitrary packages — only `sharedUserId`-claiming packages need a matching cert.

**Phase 3 v1 stand-down.** Reverted in commit `<next>`:
- `patch_y1_apk.py`: dropped Patch B6.2 (manifest splice). The B6.1 smali drop (AvrcpBridgeService.smali / AvrcpBinder.smali into `smali_classes2/`) is retained as groundwork for Phase 3 v2 — the classes exist but are not declared anywhere, so nothing instantiates them at runtime.
- `apply.bash`: restored the Y1MediaBridge.apk install step + the gradle-build prerequisite + the post-flash adb-shell note (dropped). Defensive removal of pre-existing Y1MediaBridge.apk removed.
- `patch_y1_apk.py` zip-rebuild: no longer swaps AndroidManifest.xml (it stays bit-exact stock).
- Active docs (ARCHITECTURE.md, PATCHES.md, README.md, CHANGELOG.md) re-anchored to "Y1MediaBridge.apk stays as Binder host; music app does file writes + state production."

**Phase 3 net result.** Same on-the-wire behavior as Phase 2 (verified working on Sonos in Trace #21):
- Music app's `TrackInfoWriter` is the canonical writer for `y1-track-info` / `y1-trampoline-state` / `y1-papp-set` under `/data/data/com.innioasis.y1/files/`.
- Trampolines in `libextavrcp_jni.so` read from the music app's path (Phase 2's path-string flip).
- Y1MediaBridge.apk hosts the Binder declaration MtkBt binds to — its file-write side runs but writes a path nothing reads.
- AvrcpBinder smali classes ship in classes2.dex as groundwork; not load-bearing.
- `BatteryReceiver` and `PappStateBroadcaster` fire `com.android.music.playstatechanged` for non-play-edge wakeups — useful regardless of who hosts the Binder, since these previously relied on Y1MediaBridge as a broadcast relay.

**Phase 3 v2 design space** (not implemented in this stand-down):
- **Shrink Y1MediaBridge to a thin Binder forwarder.** Keep its manifest-declared service, replace the bulk of MediaBridgeService.java with: `onBind` does `bindService(new Intent().setComponent(new ComponentName("com.innioasis.y1", "com.innioasis.y1.service.PlayerService")))` and returns the bound IBinder. Music app's PlayerService.onBind is smali-extended to return AvrcpBinder when called with a specific intent marker. This achieves "Y1MediaBridge is trivial" without touching the music APK manifest. Two APKs but the bridge becomes ~50 lines.
- **Brand-new tiny stub APK.** Build a fresh `Y1AvrcpStub.apk` (own package, own self-signed cert, no platform key needed) whose only job is the intent-filter Service declaration. Replaces Y1MediaBridge.apk; ~5 KB. Build pipeline: minimal source + signapk.jar (no gradle). Same architectural outcome.
- **Patch MtkBt.odex to bindService via component name.** Change `BTAvrcpMusicAdapter.startToBindPlayService` smali to use `Intent().setClassName("com.innioasis.y1", "com.innioasis.y1.service.PlayerService")` instead of action-only Intent. Component-based bindService doesn't require an intent-filter, so the stock manifest's existing PlayerService declaration would resolve. Smali-extend PlayerService.onBind to return AvrcpBinder. Truly retires Y1MediaBridge with zero new APKs. Most invasive: requires inserting smali instructions into MtkBt.odex's method body (shifts code offsets), which the current patch_mtkbt_odex.py infrastructure doesn't support — would need a smali-reassembly path.

All three Phase 3 v2 paths preserve the constraint discovered in Trace #23: **don't modify `com.innioasis.y1`'s AndroidManifest.xml**.


## Trace #24 (2026-05-11) — Phase 3 v2: shrink Y1MediaBridge to a minimal Binder host

Phase 3 v1 (Trace #23) established that we can't retire `Y1MediaBridge.apk` entirely without either (a) the OEM platform key (impossible) or (b) substantial MtkBt.odex smali surgery (deferred). What we CAN do — and what the user asked for as the optimal forward path — is keep all behavioral logic in the music app's process and shrink Y1MediaBridge to nothing but a Binder declaration. The "visibility issues" that drove the original Y1MediaBridge design (logcat scraping, cross-process state observation, foreground/background gaps) are all solved by Phase 1+2's in-music-app components; Y1MediaBridge had been duplicating that work to a dead path since Phase 2 shipped.

### What got deleted from `src/Y1MediaBridge/app/src/main/java/com/y1/mediabridge/MediaBridgeService.java`

Old file: 2152 lines. New file: 130 lines. Deletions:

- **`LogcatMonitor` thread + `processLogLine` + `onStateDetected` + `onTrackDetected` + every state-tracking field** (`mPlayStatus`, `mIsPlaying`, `mCurrentTitle`, `mCurrentArtist`, `mCurrentAlbum`, `mCurrentDuration`, `mPositionAtStateChange`, `mStateChangeTime`, `mPreviousTrackNaturalEnd`, `mCurrentRepeatAvrcp`, `mCurrentShuffleAvrcp`, …). The music app's `PlaybackStateBridge` observes the player engine in-process — no logcat race, no foreground/background gap.
- **The 1104-byte `y1-track-info` schema + `writeTrackInfoFile` + `putBE64` / `putBE32` / `putUtf8Padded` helpers + `prepareTrackInfoDir`.** `TrackInfoWriter` in the music app is the canonical writer (Phase 1+2).
- **`setupRemoteControlClient` + `mAudioManager` + `registerMediaButtonEventReceiver`.** The music app's manifest-declared `PlayControllerReceiver` (priority=MAX_INT for `ACTION_MEDIA_BUTTON`) wins ordered-broadcast dispatch directly; AudioService's RCC fallback to ordered broadcast is the active path.
- **`registerBatteryReceiver` + `handleBatteryIntent` + bucket-mapping helpers.** Music app's `BatteryReceiver` does this and fires `playstatechanged` itself.
- **`registerPappStateReceiver` + `handlePappStateIntent` + `mPappStateReceiver`.** Music app's `PappStateBroadcaster` calls `TrackInfoWriter.setPapp` directly and fires `playstatechanged` itself.
- **`setupPappSetObserver` + the bridge's `FileObserver(y1-papp-set)`.** Music app's `PappSetFileObserver` watches the path the trampolines actually write to.
- **`mPosTickRunnable` + the 1 Hz position-tick `Handler.postDelayed` loop.** Music app's `PlayerService` already emits `playstatechanged` on its own tick.
- **`notifyAvrcpCallbacks` + `notifyPlaybackStatus` + `notifyTrackChanged` + the `mAvrcpCallbacks` `CopyOnWriteArrayList`.** Per Sonos capture in Trace #21, MtkBt never registered a callback (the binder transact 1 never landed); the broadcast wake path drove every T5 / T9 fire. Dead code.
- **`mPlayService` + `mPlayConnection` + every `IMediaPlaybackService.Stub.asInterface` consumer.** The bridge didn't need to bind to anything.
- **`computePosition` + `safeString` + `sendMediaKey` + every other helper.** All deleted.

### What stays in `MediaBridgeService.java` (130 lines)

- Empty `onCreate`.
- `onBind` returns a private `AvrcpBinder` instance.
- `onUnbind` returns `true` so the framework's service record persists across MtkBt re-binds.
- `AvrcpBinder` (~30 LOC inner class): `onTransact` skips `strictModePolicy` + descriptor string (same defensive pattern Y1MediaBridge used to dodge ROM-variant `enforceInterface` failures), dispatches by code. Code 5 (`getCapabilities`) returns `[0x01 PLAYBACK_STATUS_CHANGED, 0x02 TRACK_CHANGED]`. Every other code: `writeNoException` + `return true`.

### `PlaySongReceiver.java` simplification

- **Deleted**: `ACTION_MEDIA_BUTTON` forwarding (music app's PlayControllerReceiver handles directly via ordered broadcast); `MY_PLAY_SONG` handling (was wakeup for the deleted LogcatMonitor); `ABOUT_SHUT_DOWN` handling (was for the deleted shutdown coordination).
- **Kept**: `BOOT_COMPLETED` → `startService(MediaBridgeService)` so the service is alive when MtkBt's first `bindService` fires (bindService would cold-start it anyway, but this makes the first bind cheaper).

Old `PlaySongReceiver.java`: 106 lines. New: 28 lines.

### `AndroidManifest.xml` cleanup

- **Dropped permissions**: `READ_LOGS` (logcat monitor gone), `MEDIA_CONTENT_CONTROL` (no more MediaController APIs), `MODIFY_AUDIO_SETTINGS` (no more RCC / AudioManager), `BLUETOOTH` (we don't talk BT directly — MtkBt does), `READ_EXTERNAL_STORAGE` + `WRITE_MEDIA_STORAGE` (no file IO outside our own data dir), `WAKE_LOCK` (no background work).
- **Kept**: `RECEIVE_BOOT_COMPLETED`.
- **Dropped `<application android:persistent="true">`**: with no background work happening, there's no reason to keep the process resident. MtkBt's `bindService` will cold-start it when needed; the framework keeps the binder bound while there's a client.
- **Dropped `<receiver>` intent-filters**: removed `MY_PLAY_SONG`, `ABOUT_SHUT_DOWN`, `MEDIA_BUTTON`. Only `BOOT_COMPLETED` left.

Old manifest: 74 lines. New: 43 lines.

### `app/build.gradle` lint config

Removed suppressions for warnings that no longer apply: `ProtectedPermissions` (no `MEDIA_CONTENT_CONTROL`), `SetWorldReadable` (no `setReadable(true, false)`). Kept: `ExpiredTargetSdkVersion`, `ExportedService`, `MissingApplicationIcon`.

### Net result

`Y1MediaBridge/` source goes from 2332 lines to 201 lines across three files. The compiled APK should be ~5–10 KB (was ~80 KB pre-shrink). Build pipeline unchanged: `cd src/Y1MediaBridge && ./gradlew assembleDebug` → `app/build/outputs/apk/debug/app-debug.apk` → `apply.bash --avrcp` copies to `/system/app/Y1MediaBridge.apk`.

The wire-level behavior is identical to Phase 1+2 (the bridge wasn't load-bearing for AVRCP events post-Phase-2 anyway — it just had a lot of redundant code running). The user-facing improvement is that the bridge is now trivially auditable: ~30 lines of actual logic.

### Visibility-issue audit (the user's framing question)

| Old Y1MediaBridge issue | Resolution in Phase 1+2+3v2 |
|---|---|
| `LogcatMonitor` parsing `'1'/'3'/'5'` from log lines | `PlaybackStateBridge` hooks `Static.setPlayValue` + player engine listener lambdas — sees state edges in-process, no log race |
| Foreground/background gap (logcat scrape missed background edges) | Hooks live in player engine; fire regardless of UI state |
| `onTrackDetected` natural-end-via-extrapolated-position heuristic | `PlaybackStateBridge.onCompletion` latches real engine EOS |
| Cross-process Battery + PApp observation via Intent bridges | `BatteryReceiver` + `PappStateBroadcaster` run in-process |
| `MediaMetadataRetriever` re-extracting tags Y1 already had | Music app reads tags from its `Song` entity directly |

All visibility-critical logic lives in the music app's process. Y1MediaBridge is purely a "JarVerifier bypass" — a self-signed manifest carrier for the intent-filter MtkBt expects.

### Stretch goals (still future work)

- **Drop the gradle dep**: shrink-source is small enough to build with just `javac` + `d8` + a minimal aapt2 substitute (or commit a prebuilt APK). Not addressed here because the user's flash machine already has gradle set up.
- **Truly retire `Y1MediaBridge.apk`**: requires `MtkBt.odex` smali surgery to component-bind into `com.innioasis.y1/.service.PlayerService` directly (option (c) from Trace #23). `patch_mtkbt_odex.py` would need a smali-reassembly path; currently it only supports same-size byte substitutions.


## Trace #25 (2026-05-11) — Phase 1+2 broadcast-wake regression: `PlaybackStateBridge` now fires `metachanged` + `playstatechanged`

### Symptom
Multi-CT capture session 2026-05-11 afternoon (`dual-bolt-20260511-1339`, `dual-kia-20260511-1336`) surfaced three user-visible regressions vs the pre-Phase-2 Y1MediaBridge.apk-driven behavior:

- **Bolt:** zero metadata rendered. Only 1 × `msg=540` (GetElementAttributes response) in 4 min, vs Sonos's 190 × in 3 min during Phase 3 v3 verification. Bolt's CT subscribes to `EVENT_TRACK_CHANGED` and waits for CHANGED notifications before issuing `GetElementAttributes` — without CHANGED firing on track edges, the CT never queries.
- **Kia:** time-playhead lags real device playhead by ~seconds; timestamps appear only after a pause. Kia polls `GetPlayStatus` (75 × in 3 min) so T6's `clock_gettime(BOOTTIME)` extrapolation returns live position correctly, but the rendering side appears to anchor on `EVENT_PLAYBACK_POS_CHANGED` CHANGED notifications which were only firing every 10 s (BatteryReceiver tick), not the documented 1 s cadence.
- **Bolt:** Repeat toggle from the CT updates `SharedPreferences` but the music-app UI doesn't refresh until the user backs out and returns; shuffle state appears stuck "on" on the CT regardless of Y1 state.

### Root cause
The Y1 music app's `PlayerService` does NOT fire the standard `com.android.music.metachanged` / `playstatechanged` broadcasts at play-state or track edges. It uses its own internal `android.intent.action.MY_PLAY_SONG` instead. Pre-Phase-2 `Y1MediaBridge.apk` had a logcat-scraping `LogcatMonitor` + a 1 s `Handler` loop that synthesised these broadcasts whenever it observed state changes. The Phase 3 v2 shrink removed both.

Verified by grep: `inject/com/koensayr/y1/playback/PlaybackStateBridge.smali` had no `sendBroadcast` call before this fix. Only `BatteryReceiver.smali` and `PappStateBroadcaster.smali` were firing `playstatechanged`, and nothing was firing `metachanged`.

MtkBt.odex's cardinality-NOP-patched `BTAvrcpMusicAdapter.handleKeyMessage` (`patch_mtkbt_odex.py` `sswitch_1a3`/`sswitch_18a`) is what wakes `notificationTrackChangedNative` / `notificationPlayStatusChangedNative` (and thus T5 / T9). Without the broadcasts being emitted, the wake never fires.

### Fix
Added two helper methods to `TrackInfoWriter`:

- `wakeTrackChanged()V` — fires `com.android.music.metachanged` via the stored Application Context.
- `wakePlayStateChanged()V` — fires `com.android.music.playstatechanged` via the same.

Modified `PlaybackStateBridge`:

- `onPlayValue` — after `setPlayStatus(B)` flushes the new state byte synchronously, calls `wakePlayStateChanged()`. Drives T9 → PLAYBACK_STATUS / PLAYBACK_POS CHANGED on real state edges (play→pause, pause→play, etc.).
- `onPrepared` — after `onTrackEdge` flushes the new track to disk, calls `wakeTrackChanged()` + `wakePlayStateChanged()`. The metachanged wake drives T5 → TRACK_CHANGED / REACHED_END (gated) / REACHED_START. The playstatechanged wake drives T9 → PLAYBACK_POS CHANGED for the position reset to 0.

`onCompletion` / `onError` unchanged — the next `onPrepared` will fire the broadcasts.

### Method-count budget
classes.dex post-Patch-B5 was 65330/65536. Two new methods (wakeTrackChanged + wakePlayStateChanged on TrackInfoWriter) take it to 65332. Three new method-ref uses inside `PlaybackStateBridge` reference these same defined methods, so no additional method refs. ~204 slots remain (still under cap; cap-check passes via apktool reassembly succeeding).

### Verification (pending hardware)
Patcher smoke-test: `output/com.innioasis.y1_3.0.2-patched.apk` reassembles cleanly. META-INF + AndroidManifest.xml byte-identical to stock (md5 match) — JarVerifier won't reject. New smali md5s:
- `TrackInfoWriter.smali` → `35496cf01171fa9c5293813a45553cc0` (was `1f6a3f44dd4ac4f3edf7c08caf76eba9` pre-fix)
- `PlaybackStateBridge.smali` → `69d50e5835b23cbf6e546298a7130f06` (was `0d8e4ed14b4dbe5683e8716b30dba76b` pre-fix)

Expected behavior on hardware:
- Bolt: TRACK_CHANGED CHANGED should fire on every `onPrepared`; Bolt CT should query `GetElementAttributes` → metadata renders.
- Kia: PLAYBACK_STATUS / POS CHANGED should fire on every play / pause / track edge, not just the 10 s battery tick. Playhead lag should reduce (full 1 s position cadence is a separate follow-up — that requires a `Handler.postDelayed` tick loop while playing; not in this fix).

### Out of scope for this fix
- 1 s position-tick loop (Kia's residual playhead drift between actual edges). Separate change to PlaybackStateBridge — a Handler scheduled from `onPlayValue(PLAYING)` and cancelled at `STOPPED`/`PAUSED`, calling `wakePlayStateChanged` on tick.
- Shuffle "always on" — likely initial-state issue: `y1-track-info[795..796]` is zero-filled at creation and `0x00` is not a valid AVRCP §5.2.4 Tbl 5.20 / 5.21 value. Need to initialize `TrackInfoWriter` defaults to `0x01 OFF` / `0x01 OFF` before any read. Separate change.
- Music-app Settings UI refresh on CT-driven Repeat/Shuffle change. Music-app side observer, not a wire-level issue.


## Trace #26 (2026-05-11) — Multi-CT verification of Trace #25 + 1 s position tick + cold-boot file-flush

### What the multi-CT capture showed (post-Trace #25)
Captures: `dual-bolt-20260511-1422`, `dual-kia-20260511-1417`, `dual-tv-20260511-1532`. Hardware confirmation that the Trace #25 broadcast-wake fix landed:

| CT | `metachanged` broadcasts | `playstatechanged` broadcasts | msg=540 (GetElementAttributes resp) | Visible result |
|---|---|---|---|---|
| Bolt | 1 | 55 | 1 | Metadata bytes delivered on the wire (full 7 attributes — Title strlen=10, Artist=14, Album=7, TrackNum=1, Total=2, Genre=16, PlayingTime=6, msg=540 size=644). **UI does not render** — CT-side issue, separate investigation. Other passthrough functions partial: PLAY 0x44 routes correctly, but PAUSE 0x46 toggles only the Bolt UI icon without actually pausing music. |
| Kia | 2 | 31 | Many (no count needed) | Metadata works. Playhead absent on PLAY edge, appears after PAUSE. Visible playhead lags real device playhead by ~1 s. Next-track skip works, but playhead disappears on the new track until next pause. |
| TV  | 7 | 42 | Many | Metadata works. Next no longer fast-forwards (Patch E/H + Patch H″ working as designed). Play/pause occasionally still gets stuck (rare, much improved). One observed state desync: Y1 showed "stop" while TV showed "paused" (~2 occurrences). |
| Sonos (prior capture) | n/a | n/a | 190+ | Play/pause/next/prev work. Repeat/shuffle UI elements grayed out — likely Sonos doesn't render PApp Settings over BT for this CT class. |

The Trace #25 wake fix unblocked metadata flow on all CTs that proactively poll `GetElementAttributes` (Kia, TV, Sonos). Bolt still has no UI metadata despite receiving the full response payload — that's a CT-side rendering issue, not a wire-shape issue.

### Fix shipped in this trace
Two compounding follow-ups for the Kia playhead lag + the shuffle initial-state issue:

**1. 1 s position-tick loop.** New class `com.koensayr.y1.playback.PositionTicker` (Runnable + lazy main-thread Handler):

- `PositionTicker.start()` — `Handler.removeCallbacks(INSTANCE)` + `postDelayed(INSTANCE, 1000)`. Idempotent.
- `PositionTicker.stop()` — `Handler.removeCallbacks(INSTANCE)`.
- `PositionTicker.run()` — calls `TrackInfoWriter.wakePlayStateChanged()` then `Handler.postDelayed(this, 1000)`.

`PlaybackStateBridge.onPlayValue` now calls `PositionTicker.start()` on the PLAYING edge (mapped state byte == 0x01) and `PositionTicker.stop()` on STOPPED / PAUSED. The wake fires `com.android.music.playstatechanged` → T9 → AVRCP 1.3 §5.4.2 Tbl 5.33 PLAYBACK_POS_CHANGED CHANGED with `clock_gettime(CLOCK_BOOTTIME)`-extrapolated position.

Expected effect on hardware:
- Kia: playhead appears immediately on first PLAY edge (T9 fires CHANGED within 1 s of `Static.setPlayValue(1, _)`) and stays current within ~1 s of real device playhead.
- TV / Sonos: extra wakes are no-ops for CTs that don't subscribe to event 0x05; small overhead (~one broadcast/sec while playing).
- Bolt: unchanged at the wire metadata layer; downstream of the rendering investigation.

**2. Cold-boot `y1-track-info` flush.** `TrackInfoWriter.init(Context)` now calls `flushLocked()` immediately after `prepareFilesLocked()`. The file lands on disk with the in-memory defaults (mRepeatAvrcp=0x01 OFF, mShuffleAvrcp=0x01 OFF — valid AVRCP §5.2.4 Tbl 5.20 / 5.21 values) before any CT can read it.

Pre-fix sequence:
1. Y1Application.onCreate → TrackInfoWriter.init → prepareFilesLocked creates `y1-trampoline-state` + `y1-papp-set`, but NOT `y1-track-info`.
2. CT subscribes to PApp CHANGED before B4's `PappStateBroadcaster.sendNow()` fires → T8 INTERIM reads `y1-track-info[795..796]`, file doesn't exist, trampoline buffer stays zero-filled, MtkBt sends `[0, 0]` → invalid AVRCP enum.
3. CT latches onto invalid initial state; some CTs (observed: Bolt) refuse to follow subsequent CHANGED events from that point.

Post-fix: file always exists with `[0x01, 0x01]` at boot.

### Method-count budget
classes.dex now 65337/65536 method refs (199 slots free). +7 over the pre-Trace-#25 baseline of 65330. Inside 64K cap.

### Out of scope
- **Bolt no UI metadata.** Wire-level metadata response is correct (full 7 attributes, valid UTF-8, valid SongPosition). Investigation needs btlog.bin parse of the AVRCP frames Bolt sends back, to figure out what specific event/PDU Bolt is waiting for before rendering. Possibilities: missing AVRCP 1.4 ABSOLUTE_VOLUME response, missing PLAYBACK_STATUS_CHANGED INTERIM with status=PLAYING, AVCTP fragmentation parsing on the CT side.
- **Bolt PAUSE not actually pausing.** PASSTHROUGH 0x46 receipt confirmed at the MMI_AVRCP layer; the downstream kernel input → AVRCP.kl → KeyEvent → BaseActivity (Patch H) → PlayControllerReceiver (Patch E) path needs to be traced. Bolt's icon toggling without actual pause action suggests a dispatch hole somewhere in the foreground-activity propagation path.
- **Music-app Settings UI refresh on CT-driven Repeat/Shuffle.** Music-app side observer, not a wire-level issue. Fix needs SharedPreferences listener in the Settings activity or an explicit refresh Intent from PappSetFileObserver.



## Trace #27 (2026-05-13) — AVRCP §6.7.1 per-subscription gate completes the 1.3 pipeline; Bolt's metadata pane remains 1.4-CoverArt-blocked

### Background

Trace #26's hardware verification showed Kia's playhead "appeared, played for 2 seconds, then froze." The two-second window is the smoking gun: the wire was emitting `PLAYBACK_POS_CHANGED CHANGED` continuously at 1 s cadence via PositionTicker, but Kia stopped updating its display after the second frame. Bolt similarly showed its play/pause icon flipping correctly on the first state-edge, then sticking on "Play" forever — and its Shuffle button enabling shuffle on Y1 but never registering as enabled in Bolt's own UI.

Both symptoms point at the same spec gap: AVRCP 1.3 §6.7.1 says "Once a Controller has registered to receive a particular EventID, the Target shall notify the CT of the change to the registered EventID only once." After CHANGED, the subscription is consumed; the CT must re-register to receive another. We were emitting CHANGED unconditionally — on every PositionTicker tick (for event 0x05) and on every actual state edge (for events 0x01/0x02/0x06/0x08).

Cadence comparison from `dual-kia-20260513-1144` vs the older Sonos working capture (`dual-sonos-20260511-1042`):

| CT | size:13 (RegNotify event 0x05) cadence |
|---|---|
| Sonos | ~10 ms between frames, 20 frames in 280 ms after connect, continuously re-registering |
| Kia | 6 in 80 ms at connect, then NONE for 15+ seconds |

Sonos's aggressive re-registration matches the strict §6.7.1 contract — it gets a steady stream of valid CHANGEDs. Kia subscribes once and trusts the TG to keep sending. Our excess CHANGEDs after the first were silently rejected by Kia.

### Fix — full §6.7.1 compliance via per-subscription gates

`y1-trampoline-state` grew from 16 B → 20 B. Bytes 13..19 each hold one subscription byte:

| Byte | Event | Arm site | Clear site |
|---|---|---|---|
| 13 | 0x05 PLAYBACK_POS_CHANGED | T8 INTERIM | T9 CHANGED |
| 14 | 0x01 PLAYBACK_STATUS_CHANGED | T8 INTERIM | T9 CHANGED |
| 15 | 0x08 PLAYER_APPLICATION_SETTING_CHANGED | T8 INTERIM | T9 CHANGED |
| 16 | 0x02 TRACK_CHANGED | T2 INTERIM | T5 CHANGED |
| 17 | 0x03 TRACK_REACHED_END | T8 INTERIM | T5 CHANGED |
| 18 | 0x04 TRACK_REACHED_START | T8 INTERIM | T5 CHANGED |
| 19 | 0x06 BATT_STATUS_CHANGED | T8 INTERIM | T9 CHANGED |

INTERIM emit sites write `0x01` to their byte via `_emit_subscription_write` helper (1-byte `strb_w` + `open + lseek + write + close`). CHANGED emit sites read the byte; if 0, skip emit; if 1, emit + clear. Edge-detection writes (state[9..12]) remain unconditional so we don't loop "edge detected, can't emit" forever while un-subscribed.

Schema migration: existing 16-B files on already-flashed devices grow naturally — T2/T8's `lseek(N >= 16) + write(1)` extends the file. Until then, new gate-bytes read as 0 = "not subscribed" via T5/T9's memset-then-read pattern. No manual remediation needed.

Stack-frame growth: T5_FRAME 816 → 820, T9_FRAME 832 → 836. T5_OFF_FILE 16 → 20, T9_OFF_FILE 24 → 28 (timespec offset shifts accordingly). T4 state write switched from `O_WRONLY|O_TRUNC` to `O_WRONLY` so it doesn't clobber bytes 16..19 that T2/T8 may have written. Some short-form branches in T5 needed promotion to wide-form (`blt_w` / `beq_w`) — extended body exceeded the 254-B short range.

OUTPUT_MD5 of `libextavrcp_jni.so` is now `c017b6ab5d66ccbd851c9399e0642262`.

### Other fixes shipped same session

| Commit | What |
|---|---|
| `1381d57` | Y1Bridge.MediaBridgeService.AvrcpBinder reads `y1-track-info` for synchronous IBTAvrcpMusic queries (codes 17/19/24/25/26/27/28/29/30/31). MtkBt's Java mirror now reflects real state instead of empty/default. |
| `7833cf0` | `getAlbumId` synthesizes a stable handle from album-name hash (CTs that group by album_id no longer conflate all tracks). `setPlayerApplicationSettingValue` (code 4) backstops to `y1-papp-set` so the apply path works whether T_papp 0x14 or the Java setter is the trigger. |
| `d535c7e` | AOSP-convention Intent extras (`id`, `track`, `artist`, `album`, `playing`) on `wakeTrackChanged` / `wakePlayStateChanged` broadcasts. MtkBt's `MMI_AVRCP` logs flipped from `playing:false id:-1` to real values, unblocking the cardinality-NOP wake path. |
| `dbdf5d0` | TrackInfoWriter.`mLastKnownDuration` preserves duration across prepare gaps (CTs no longer see `song_length=0` and hide the playhead). `markCompletion` freezes the anchor at `mLastKnownDuration` so post-EOS T9 emissions read `position == duration` (not `position > duration` which strict CTs reject). PlaybackStateBridge.`onCompletion` stops PositionTicker and fires one final wake. |
| `56ab3b7` | `PlayerService.setCurrentPosition(J)` (the music app's single seek funnel) prepended with `PlaybackStateBridge.onSeek(J)` → `TrackInfoWriter.onSeek(J)` refreshes the anchor + fires wakePlayStateChanged. Seek bar now propagates to CT immediately. |
| `44d376c` | Patch E `:cond_play_strict` — PASSTHROUGH PLAY (0x44) while `isPlaying()` is true now routes to `PlayerService.playOrPause()` (effectively pause-toggle). Spec-compliant CTs never send PLAY while playing so this only fires for non-spec CTs (Bolt) that map their Pause button to AVRCP PLAY. |
| `1947dd8` | `MusicPlayerActivity.refreshRepeatShuffleUi()` injected — re-renders just the Repeat/Shuffle ImageView icons from current SharedPreferences. `NowPlayingRefresher.run()` calls it (previously called `refreshUI()` which only updates track-name text labels and doesn't touch the icons). CT-driven Repeat/Shuffle changes now paint live on the Now Playing screen. |

### Hardware results (2026-05-13 captures)

**Kia EV6** (`dual-kia-20260513-1351`): play/pause toggles work, playhead updates continuously, Repeat/Shuffle UI flips in real-time on the Y1 Now Playing screen, metadata pane renders. **All previously-reported Kia issues resolved.**

**Bolt EV** (`dual-bolt-20260513-1355`):
- Audio actually pauses on Pause button press (was broken pre-`44d376c`). ✓
- Forward / Previous PASSTHROUGH actions work. ✓
- Metadata pane stays empty. **Root cause confirmed: Bolt's `GetElementAttributes` request (wire `size:45`) asks for 8 attributes including attr 8 (Default Cover Art handle, AVRCP 1.6 §5.13.4). We return 7. Bolt gates pane render on receiving a non-empty CoverArt entry.** Note: Default Cover Art is an AVRCP 1.6 feature (Dec 2015), NOT 1.4. AVRCP 1.4 added browsing + AbsoluteVolume; 1.5 added AddressedPlayer / AvailablePlayers; 1.6 added DCA via attribute 8 + BIP integration.
- Play/Pause icon stuck after first toggle. **Root cause: Bolt subscribes for event 0x01 once at connect and never re-registers. Our gate emits exactly one CHANGED per registration; Bolt only ever sees one. UI mirror frozen at first-CHANGED state.**
- Shuffle stuck on. Same root cause — Bolt subscribes for event 0x08 once.

Bolt's failure to re-register after CHANGED is a CT-side spec violation we cannot work around without violating §6.7.1 on the TG side (which would re-break Kia). The empty metadata pane is the actionable symptom — implementing AVRCP 1.6 Default Cover Art unblocks it.

### Scope change: AVRCP 1.6 Default Cover Art newly in-scope

User directive 2026-05-13 after seeing the §6.7.1 fixes work end-to-end on Kia: "I do not accept the Bolt's behavior as-is. I think the cover art thing might be worth a look."

Project policy amended: `feedback_avrcp13_only_scope.md` now lists two carve-outs:
1. MtkBt.odex F1 BlueAngel internal-flag spoof (existing).
2. AVRCP 1.6 Default Cover Art (BIP/OBEX) — new. Specifically: GetElementAttributes attribute id 8 (Default Cover Art handle per AVRCP 1.6 §5.13.4), BIP responder (UUID 0x111A Imaging Responder / 0x111B Imaging Reference), OBEX channel for image transfer.

Other 1.4+ / 1.5+ / 1.6+ features (SetAbsoluteVolume, browse channel for player switching, SetAddressedPlayer, NOW_PLAYING_CONTENT_CHANGED, etc.) remain out of scope.

### What needs investigation before implementing Default Cover Art

1. Does mtkbt include a BIP server? — `strings /work/v3.0.2/system.img.extracted/system/bin/mtkbt | grep -i "bip\|imaging\|cover.art\|0x111a\|0x111b"`. If yes, just wire it up. If no, implement BIP atop mtkbt's existing OBEX or in a parallel daemon.
2. What SDP record does Bolt expect for BIP? Capture an iPhone-or-Pixel-paired session's SDP advertisement and compare.
3. Where does the music app store/access cover art for local display? Likely `MediaMetadataRetriever.getEmbeddedPicture()` or similar.
4. How is attr 8 transferred on the wire? AVRCP 1.6 §5.13.4: handle is a 7-character ASCII hex string returned in the GetElementAttributes response payload. CT then opens an OBEX channel and issues GetImage / GetLinkedThumbnail to fetch the actual JPEG bytes by handle.
5. JPEG thumbnail constraints per AVRCP 1.6 §5.13.4: max 200×200 pixels, max 200 KB.

Implementation plan sketch in memory `project_y1_cover_art_direction.md`.

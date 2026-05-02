# Investigation Traces — Open Items

Diagnostic paths that are still tractable from the available toolset (no root, no btsnoop, no on-device debugger).

State as of 2026-05-02: every documented Y1-side patch site (B1-B3, C1-C3, A1, D1, E3-E5, E7a/E7b, C2a/b, C3a/b, C4, F1/F2) is flashed and verified on the wire (sdptool shows AVRCP 1.4 + AVCTP 1.3 + SupportedFeatures 0x0033). Despite this, no inbound AVRCP commands beyond connect/disconnect/activate ever reach the JNI dispatch socket for any of three known-good 1.4 controllers (car, Sonos Roam, Samsung TV). Each session ends in a clean `MSG_ID_BT_AVRCP_DISCONNECT_CNF` after ~25–30 seconds of silence. Music plays cleanly over A2DP throughout.

The remaining gate is between mtkbt's L2CAP/AVCTP receive path and its JNI dispatch socket. mtkbt's daemon-side `[AVCTP]`/`[AVRCP]` log strings exist in the binary but route through MediaTek's `__xlog_buf_printf` (separate from `logcat`), so daemon-level activity is opaque to standard log capture.

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

## Out of Scope (eliminated)

- HCI snoop / btsnoop — no root, eliminated in earlier passes.
- mtkbt instrumentation patches (insert log calls at choke points) — possible but very high effort, low marginal value over #1 + #2.
- boot.img init scripts — won't reveal anything about the AVRCP path.

# Patch Reference

Byte-level detail for every patch shipped (or attempted) by this repo. Patches are referenced by short IDs throughout `INVESTIGATION.md`, `CHANGELOG.md`, and the patcher source files.

## Patch ID Legend

| ID(s) | Binary | Site / effect |
|---|---|---|
| **B1, B2, B3** | `mtkbt` | AVCTP version `0x00 → 0x03` (1.0 → 1.3) in three SDP descriptor groups: Groups 1&2 TG ProtocolDescList (`0x0eba6d`), Group 3 CT ProtocolDescList (`0x0eba37`), Group 1 AdditionalProtocol/browsing (`0x0eba25`). AVRCP 1.4 requires AVCTP 1.3. |
| **C1, C2, C3** | `mtkbt` | AVRCP version → 1.4 in three ProfileDescList entries: `0x0eba4b` (entry[23], 1.0→1.4), `0x0eba58` (entry[18], 1.0→1.4), `0x0eba77` (entry[13], 1.3→1.4). |
| **A1** | `mtkbt` | Runtime SDP MOVW immediate at `0x38BFC`: `MOVW r7,#0x0301 → MOVW r7,#0x0401` — belt-and-suspenders against the static SDP template. |
| **D1** | `mtkbt` | NOP the registration guard at `0x38C6C` (`BNE → NOP`). Without this, the AVRCP TG SDP struct is built but never linked into mtkbt's live registry; mtkbt silently discards inbound GetCapabilities. |
| **E3, E4** | `mtkbt` | TG SupportedFeatures bitmask: Group 2 (served) `0x0001 → 0x0033` at `0x0eba5b`; Group 1 (defense-in-depth) `0x0021 → 0x0033` at `0x0eba4e`. `0x33` = Cat1 + Cat2 + PAS + GroupNav (AVRCP 1.4 baseline). |
| **E8** | `mtkbt` | NOP the `bge #0x30688` at `0x3065e` in fn `0x3060c` (op_code=4 dispatcher slot 0). Forces classification through the AVRCP 1.3/1.4 init path regardless of `[conn+0x149]`'s sign bit. **Empirically inert** for our peers (gate is upstream of the dispatcher table); kept as a verified-correct probe. |
| **E5, E7a, E7b** | `mtkbt` | **Removed 2026-05-02.** Tested across three known-good 1.4 controllers, no observable behavioural change — code paths not exercised at runtime for our peer state. |
| **C2a, C2b** | `libextavrcp_jni.so` | In `BluetoothAvrcpService_activateConfig_3req` at `0x375c`: hardcode `g_tg_feature = 0x0e` and `sdpfeature = 0x23`, bypassing the bitmask negotiation logic. |
| **C3a, C3b** | `libextavrcp_jni.so` | In `getCapabilitiesRspNative` (`FUN_005de8`) at `0x5e56`/`0x5e5c`: raise the GetCapabilities EventList cap from `13 → 14` so a 1.4-capable response can be served if the JNI ever receives an inbound GetCapabilities. |
| **C4** | `libextavrcp.so` | Single AVRCP version constant at `0x002e3b`: `0x0103 → 0x0104` (1.3 → 1.4). |
| **F1** | `MtkBt.odex` | At `0x3e0ea`: `getPreferVersion()` returns `14` (AVRCP 1.4) instead of `10` (BlueAngel internal code for AVRCP 1.3). |
| **F2** | `MtkBt.odex` | At `0x03f21a`: `BluetoothAvrcpService.disable()` resets `sPlayServiceInterface = false`. Fixes a BT-toggle bug where the service tears itself down prematurely on second activation because the flag is left stale across restarts. |
| **G1, G2** | `mtkbt` | **Attempted and reverted 2026-05-02 / 2026-05-03.** Diagnostic `__xlog_buf_printf → __android_log_print` redirect (Thumb thunk at `0x675c0`, ARM PLT at `0xb408`). Crashed mtkbt at NULL fmt; even with NULL guard, BT framework couldn't enable. Path closed without root or daemon-side tooling. |
| ~~**H1, H2, H3**~~ | `/sbin/adbd` (in `boot.img` ramdisk) | **Tried 2026-05-03; reverted (caused "device offline").** Both attempted approaches (NOP the three `blx setgroups/setgid/setuid` calls; change their argument values from 2000/11 to 0) caused adbd-at-uid-0 to start and enumerate over USB but fail the ADB protocol handshake. Static analysis didn't find a `getuid()`-based gate or a uid==2000 compare in adbd, so the failure mode is something we can't see without on-device visibility. `--root` removed from the bash in v1.7.0; superseded in v1.8.0 by the `su` install approach. |
| **su** | `/system/xbin/su` (new file) | **Reintroduced root path, v1.8.0.** Ship a minimal setuid-root `su` binary (06755, root:root) to obtain root via `adb shell /system/xbin/su` without touching `/sbin/adbd`. Built from `src/su/su.c` + `src/su/start.S` via `arm-linux-gnu-gcc`: ~900-byte direct-syscall ARM-EABI ELF, no libc, no manager APK. |

The "Final state" in [INVESTIGATION.md](../INVESTIGATION.md) summarises which IDs ship in the current build. The shipping mtkbt MD5 is `d47c904063e7d201f626cf2cc3ebd50b` (B1-B3, C1-C3, A1, D1, E3, E4, E8 = 11 patches).

---

## `patch_mtkbt.py`

Patches the stock `mtkbt` Bluetooth daemon binary for AVRCP 1.4. **Eleven patches applied:**

- **B1** `0x0eba6d`: `0x00` → `0x03` — AVCTP 1.0 → 1.3 LSB in Groups 1 & 2 shared ProtocolDescList (TG control channel — what `sdptool` sees)
- **B2** `0x0eba37`: `0x00` → `0x03` — AVCTP 1.0 → 1.3 LSB in Group 3 CT ProtocolDescList
- **B3** `0x0eba25`: `0x00` → `0x03` — AVCTP 1.0 → 1.3 LSB in Group 1 AdditionalProtocol (browsing channel descriptor)
- **C1** `0x0eba4b`: `0x00` → `0x04` — AVRCP 1.0 → 1.4 LSB in ProfileDescList entry[23]
- **C2** `0x0eba58`: `0x00` → `0x04` — AVRCP 1.0 → 1.4 LSB in ProfileDescList entry[18] (served by SDP last-wins)
- **C3** `0x0eba77`: `0x03` → `0x04` — AVRCP 1.3 → 1.4 LSB in ProfileDescList entry[13]
- **A1** `0x38BFC`: `40 f2 01 37` → `40 f2 01 47` — `MOVW r7,#0x0301` → `MOVW r7,#0x0401` (runtime SDP struct, belt-and-suspenders)
- **D1** `0x38C6C`: `03 d1` → `00 bf` — `BNE 0x38C76` → `NOP` — bypasses registration guard so the AVRCP TG SDP struct is always linked into mtkbt's live registry (see D1 note below)
- **E3** `0x0eba5b`: `0x01` → `0x33` — Group 2 TG SupportedFeatures (served): `0x0001` → `0x0033` (Cat1 + Cat2 + PAS + GroupNav — AVRCP 1.4 baseline matching AOSP Bluedroid)
- **E4** `0x0eba4e`: `0x21` → `0x33` — Group 1 TG SupportedFeatures (defense-in-depth): `0x0021` → `0x0033`
- **E8** `0x3065e`: `13 da` → `00 bf` — `BGE 0x30688` → `NOP` in fn `0x3060c` (op_code=4 dispatcher slot 0). Forces every classification through the AVRCP 1.3/1.4 init path (`b.w 0x2fd34`) regardless of the sign bit of `[conn+0x149]`. See E8 note below.

The descriptor table contains three service record groups. Groups 1 & 2 are TG (AV Remote Target 0x110c); Group 3 is CT (AV Remote 0x110e). All AVCTP version bytes were stock 1.0; AVRCP 1.4 requires AVCTP 1.3. All three ProfileDescList entries are patched to AVRCP 1.4 (last-wins semantics).

**D1 note:** The SDP init function at `0x38AB0` builds the TG struct, then gates the final `STR r3,[r1]` registration write behind `CMP r0,r5 / BNE` where r5=`0x111F`. r0 is never `0x111F`, so without D1 the registration never completes and mtkbt silently discards incoming GetCapabilities commands.

**E3/E4 note:** Wire-confirmed via `sdptool browse` after D1 was live: `AttrID=0x0311` IS served inside the AVRCP TG record (UUID 0x110c), but the served value is `0x0001` (Cat1 only — Group 2 wins the merge). 1.4 controllers see ProfileVersion=1.4 with a feature bitmask consistent with 1.0, treat the advertiser as inconsistent, and skip `REGISTER_NOTIFICATION` (which is why earlier builds had `cardinality:0` even with C3a/C3b applied). Browsing bit (6) is deliberately omitted because `AdditionalProtocolDescriptorList` (0x000d) is in Group 1 only and isn't on the wire after the merge — claiming Browsing without serving the descriptor would re-introduce the same inconsistency.

**E8 note:** Trace #1g resolved the indirect-call graph and identified three op_code=4 dispatchers reached via the 3-slot fn-ptr table at vaddr `0xf94b0..0xf94bc`: fn `0x3060c` (slot 0), fn `0x30708` (slot 1), fn `0x3096c` (slot 2). Of these only fn `0x3060c` has a clean single-instruction high-bit gate on `[conn+0x149]`: `ldrsb.w r0,[r4,#0x149]; cmp r0,#0; bge #0x30688`. The bge skips the 1.3/1.4 init path when the version byte's high bit is clear; NOPing it forces the init path unconditionally. Brute-forcing the analogous fix to the other two slots was considered and rejected: fn `0x30708` reads the byte unsigned and masks `&0x7f` (no high-bit gate exists; failure exits gate on a multi-byte state-machine on `[conn+0x5d0]`); fn `0x3096c`'s analogous BNE→B (the old E5 patch at `0x309ec`) was already empirically tested in earlier sessions and removed as inert. E8 ships as a low-risk single-instruction probe; tested 2026-05-02 and observed inert (cardinality:0 persists, no `op_code=4` GetCapabilities messages reach the dispatchers — the gate is upstream of the dispatcher table entirely).

**MD5s:** Stock `3af1d4ad8f955038186696950430ffda` → Output `d47c904063e7d201f626cf2cc3ebd50b`.

---

## `patch_mtkbt_minimal.py`

Research-probe patcher. **Four patches** against stock mtkbt: three SDP-shape patches that get Sonos to send AVRCP 1.3+ COMMANDs against the served TG record, plus one binary patch that routes the inbound frame to the JNI msg 519 emit path (which Y1MediaBridge is wired to consume).

- **V1** `0x0eba58`: `0x00` → `0x03` — AVRCP 1.0 → 1.3 LSB in served Group D ProfileDescList. Same offset as **C2** in `patch_mtkbt.py` but narrowed to 1.3 instead of 1.4.
- **V2** `0x0eba6d`: `0x00` → `0x02` — AVCTP 1.0 → 1.2 LSB in served Group D ProtocolDescList. Same offset as **B1** in `patch_mtkbt.py` but narrowed to 1.2 instead of 1.3.
- **S1** `0x0f97ec` (12 bytes): replace the `0x0311` SupportedFeatures attribute table entry with a `0x0100` ServiceName entry pointing at the existing "Advanced Audio" SDP-encoded string at file offset `0x0eb9ce` (re-used from mtkbt's A2DP record; peers don't validate ServiceName content, only its presence).
  - Before: `11 03 03 00 59 ba 0e 00 00 00 00 00` (attr=`0x0311`, len=3, ptr=`0x0eba59` → `uint16 0x0001`)
  - After:  `00 01 11 00 ce b9 0e 00 00 00 00 00` (attr=`0x0100`, len=`0x11`, ptr=`0x0eb9ce` → `25 0f "Advanced Audio\0"`)
- **P1** `0x144e8` (2 bytes): replace the first comparison in fn `0x144bc`'s op_code dispatch with an unconditional branch to the PASSTHROUGH-emit branch at `0x14528`. That branch ends with `bl 0x10404`, which is the function that emits msg 519 (CMD_FRAME_IND) to the JNI socket — empirically traced via gdbserver across PASSTHROUGH and VENDOR_DEPENDENT inbound frames. PASSTHROUGH frames already took this path; VENDOR_DEPENDENT frames previously took the `bcc 0x1454a` branch (which only logs via `bl 0x11374`). Patching the cmp to a 2-byte unconditional `b.n` makes all AV/C frames flow through the emit path.
  - Before: `30 2b` (Thumb `cmp r3, #0x30` = `0x2b30`)
  - After:  `1e e0` (Thumb `b.n 0x14528`, jumps +0x3c bytes from PC at `0x144ec`)

**Cost of S1:** the served record loses the `0x0311` SupportedFeatures attribute. Empirically Pixel-1.3 advertises features `0x0001` and Sonos engages — Sonos engages with our record without `0x0311` too, per the iter1 `--avrcp-min` capture (Sonos sent VENDOR_DEPENDENT GetCapabilities).

**Cost of P1:** the bl `0x10404` path was designed for PASSTHROUGH frames. VENDOR_DEPENDENT frame bytes will be interpreted in PASSTHROUGH-shaped fields, so the response mtkbt sends back to the peer may be malformed. Worst case is mtkbt emits a NOT_IMPLEMENTED reply (which is what currently happens already, so no regression). Best case is msg 519 fires with the inbound frame bytes preserved, JNI passes them to Y1MediaBridge via the `IBTAvrcpMusic` Binder, and Y1MediaBridge builds the appropriate AVRCP RESPONSE on its outbound path.

**Mutual exclusion with `patch_mtkbt.py`:** both patchers touch overlapping byte ranges (`0x0eba58`, `0x0eba6d`, the `0x0311` entry slot at `0x0f97ec`). `apply.bash` enforces this — `--avrcp` and `--avrcp-min` cannot both be specified.

**MD5s:** Stock `3af1d4ad8f955038186696950430ffda` → Output `a37d56c91beb00b021c55f7324f2cc09`.

---

## `patch_libextavrcp_jni_minimal.py`

Research-probe patcher complementing `patch_mtkbt_minimal.py`. Implements two trampolines, **T1 (GetCapabilities)** and **T2 (RegisterNotification(TRACK_CHANGED))**, that intercept inbound size!=3 dispatch in `_Z17saveRegEventSeqIdhh` (file body 0x5f0c) and call response-builder functions directly via PLT. Bypasses both the JNI's "unknow indication" default-reject (the original size!=8 branch) and the JNI->Java callback path (the size==8 branch).

**R1 — redirect** at `0x6538` (4 bytes):

| | bytes | mnemonic |
|---|---|---|
| before | `40 d1 09 25` | `bne.n 0x65bc` + `movs r5, #9` |
| after  | `00 f0 e6 fe` | `bl.w 0x7308` |

Destroys the size==8 path's leading `movs r5, #9`. Acceptable because mtkbt-as-1.0 never legitimately produces size==8 frames on this device, and size!=8 was already routed to "unknow indication" — the trampoline still routes it there via its `fall_through` arm.

**T1 — trampoline** at `0x7308` (40 bytes), overwriting the unused JNI debug method `_Z33BluetoothAvrcpService_testparmnumP7_JNIEnvP8_jobjectaaaaaaaaaaaa` (~44 byte slot):

```
0x7308: 9d f8 7e 01     ldrb.w r0, [sp, #382]    ; PDU byte (AV/C body+4)
0x730c: 10 28           cmp r0, #0x10            ; GetCapabilities?
0x730e: 0d d1           bne.n 0x732c             ; no -> fall_through
0x7310: 04 a3           adr r3, 0x7324           ; events_data ptr
0x7312: 05 f1 08 00     add.w r0, r5, #8         ; r0 = conn buffer (r5 from prologue)
0x7316: 00 21           movs r1, #0
0x7318: 05 22           movs r2, #5              ; events count
0x731a: fc f7 60 e9     blx 0x35dc               ; PLT->btmtk_avrcp_send_get_capabilities_rsp
0x731e: ff f7 04 bf     b.w 0x712a               ; mov r9,#1; canary; epilogue
0x7322: 00 bf           nop
0x7324: 01 02 09 0a 0b 00 00 00                  ; supported events:
                                                 ;   0x01 PLAYBACK_STATUS_CHANGED
                                                 ;   0x02 TRACK_CHANGED
                                                 ;   0x09 NOW_PLAYING_CONTENT_CHANGED
                                                 ;   0x0a AVAILABLE_PLAYERS_CHANGED
                                                 ;   0x0b ADDRESSED_PLAYER_CHANGED
0x732c: ff f7 46 b9     b.w 0x65bc               ; fall_through (original "unknow")
```

**T2 — classInitNative stub + RegisterNotification(TRACK_CHANGED) trampoline** at `0x72d0` (48 bytes), overwriting the JNI debug method `classInitNative` (which is purely two `__android_log_print` calls + `return 0` — safe to stub). T1's fall-through arm at 0x732c bridges here via `b.w 0x72d4`.

```
0x72d0: 00 20 70 47     classInitNative stub (movs r0, #0; bx lr)
                        — preserves the "return 0" contract; loses debug logs
; T2 stage 2 entry
0x72d4: 31 28           cmp r0, #0x31           ; PDU still in r0 from T1
0x72d6: 0d d1           bne.n 0x72f4            ; not RegisterNotification
0x72d8: 9d f8 82 01     ldrb.w r0, [sp, #386]   ; event_id (clobber PDU)
0x72dc: 02 28           cmp r0, #0x02           ; EVENT_TRACK_CHANGED?
0x72de: 09 d1           bne.n 0x72f4            ; not TRACK_CHANGED
0x72e0: 05 f1 08 00     add.w r0, r5, #8        ; conn buffer
0x72e4: 9d f8 70 11     ldrb.w r1, [sp, #368]   ; transId
0x72e8: 0f 22           movs r2, #0x0f          ; INTERIM reasonCode
0x72ea: 03 a3           adr r3, 0x72f8          ; track_id_data ptr
0x72ec: fc f7 4a e8     blx 0x3384              ; PLT → …reg_notievent_track_changed_rsp
0x72f0: ff f7 1b bf     b.w 0x712a              ; epilogue
0x72f4: ff f7 62 b9     b.w 0x65bc              ; unknown PDU/event → "unknow indication"
0x72f8: ff ff ff ff ff ff ff ff                ; track_id = 0xFFFFFFFFFFFFFFFF
                                                ;   ("Identifier not allocated, metadata not available")
```

PLT 0x3384 → GOT 0xcf0c → `btmtk_avrcp_send_reg_notievent_track_changed_rsp`. Argument shape (r0=conn, r1=transId, r2=reasonCode, r3=ptr_to_8byte_track_id) verified via cross-reference with `notificationTrackChangedNative` at 0x3bc0 — see `add.w r0, r8, #8 / uxtb.w r1, r9 / uxtb.w r2, sl / add r3, sp, #12 / blx 3384` at 0x3c3c.

EVENT_PLAYBACK_STATUS_CHANGED (0x01), NOW_PLAYING_CONTENT_CHANGED (0x09), AVAILABLE_PLAYERS_CHANGED (0x0a), and ADDRESSED_PLAYER_CHANGED (0x0b) all fall through to the unknown branch (b.w 0x65bc) → mtkbt sends NOT_IMPLEMENTED. Sonos retries each ~4× then gives up. Metadata handshake (TRACK_CHANGED is what gates GetElementAttributes) still proceeds with just T2 in place. T3 (playback) added later if needed.

**Why this works:**
- At the redirect site, the function has already executed `add.w r0, r5, #8` (at 0x6528), so `r0 = conn buffer` is on register exit. The trampoline reads PDU into r0 (clobbering it) for the cmp, then re-derives `r0 = r5+8` before the response-builder call.
- `r5` is preserved across the `bl.w` because it's callee-saved in AAPCS and the trampoline doesn't push/pop it.
- 0x712a sets `r9=1` (the function's return value), runs the stack-canary check at 0x712e, and falls into the function epilogue at 0x7154 (`pop {r4-r9, sl, fp, pc}`).

**Hardware verification:**
- T1 alone (iter5, 2026-05-05): elicited 30-byte msg=522 outbound (consistent with a real GetCapabilities response); Sonos progressed and sent 4 size:13 RegisterNotification frames at 2-second intervals — confirming T1 fires correctly.
- T2: pending hardware test.

**History:** J1 (cmp.w lr,#8 → cmp.w lr,#9 at 0x6526) was tried 2026-05-05 (iter4) and rolled back — it routed our size-9 frames into the size-8 PASSTHROUGH dispatch, calling `btmtk_avrcp_send_pass_through_rsp` with VENDOR_DEPENDENT-shaped data and dispatching as a fake `key=1 isPress=0` PASSTHROUGH event. See INVESTIGATION.md Trace #12.

**Mutual exclusion with `patch_libextavrcp_jni.py`:** both patchers target the same binary; the v2.0.0 set's C2a/b and C3a/b are at different offsets but cumulatively re-shape the JNI's behaviour, so combining them isn't supported.

**T4 — stub at extended-LOAD-segment vaddr 0xac54** (12 bytes). The original libextavrcp_jni.so has a LOAD segment ending at vaddr/file 0xac54, with 4276 zero-padding bytes before LOAD #2 starts at file 0xbc08. The patcher writes T4 code into this padding and bumps LOAD #1's `FileSiz`/`MemSiz` from 0xac54 to 0xac60 so the kernel maps the bytes as R+E at runtime. T2's "unknown" branch at 0x72f4 is rewritten to `b.w 0xac54` instead of `b.w 0x65bc`.

```
0xac54: bd f8 76 e1     ldrh.w lr, [sp, #374]   ; restore lr = SIZE
0xac58: 05 f1 08 00     add.w  r0, r5, #8        ; restore r0 = conn buffer
0xac5c: fb f7 ae bc     b.w    0x65bc            ; original "unknow indication"
```

**Why these two restores:** the original `bne 0x65bc` site at file 0x6538 was reached with two specific values that 0x65bc relies on:
- `r0 = r5+8` (set at 0x6528, two instructions before the bne)
- `lr = halfword at sp+374` (= SIZE; loaded at 0x644e, before all the cmp/bne dispatches)

The 0x65bc path then runs `str.w lr, [sp, #12]` to pass SIZE to `btmtk_avrcp_send_pass_through_rsp` as a stack arg. Our T1/T2 trampolines clobber r0 (with PDU/event_id) AND the `bl.w 0x7308` at 0x6538 clobbers lr (to 0x653c, the bl return address). Without both restores, pass_through_rsp gets bogus args and silently fails to emit msg=520 NOT_IMPLEMENTED.

Iter5/iter6 confirmed the symptom: Sonos retried unhandled size:13/size:45 frames forever because mtkbt was emitting nothing back. Iter7 (only the r0 restore, no lr restore) tested the ELF-extension infrastructure but still didn't generate msg=520 — the lr clobber was the second half of the bug. Iter8 (this version) restores both.

**Iter16 (current): state-tracked CHANGED with sentinel track_id + dynamic trampoline assembly.** The full T4 + extended_T2 trampoline pair lives in the 0xac54 padding region; T2's old in-place stub at 0x72d4 is now a single `b.w extended_T2`. Both trampolines read `/data/data/com.y1.mediabridge/files/y1-track-info` (776 B: 8-byte big-endian track_id + 3 × 256 B Title/Artist/Album) and `…/y1-trampoline-state` (16 B: last-synced track_id + last RegisterNotification transId), and T4 emits `track_changed_rsp CHANGED` whenever the file's track_id has diverged from the state file. **iter16-specific:** the wire-level `track_id` field in INTERIM and CHANGED is hardcoded to `0xFFFFFFFFFFFFFFFF` (AVRCP 1.4 §6.7.2 "not bound to a particular media element"), keeping Sonos in poll-on-each-event mode while still using CHANGED edges to invalidate Sonos's cache on real track changes. iter15's "real track_id in INTERIM" deadlocked Sonos (it stopped polling after the first INTERIM, waiting forever for a CHANGED that only fires inside our reactive trampolines). The trampoline blob is built dynamically from `_thumb2asm.py` + `_iter15_trampolines.py` rather than hand-encoded.

**Program-header surgery:** the patcher updates LOAD #1's program header at file 0x54:
- offset+16 (`p_filesz`): 0xac54 → 0xae98 (iter16)
- offset+20 (`p_memsz`):  0xac54 → 0xae98 (iter16)

No other section/segment offsets shift, so `.dynsym`/`.text`/`.rodata`/`.dynamic`/`.rel.plt` etc. all stay byte-identical. The dynamic linker just maps slightly more of the file into the R+E segment.

**MD5s:** Stock `fd2ce74db9389980b55bccf3d8f15660` → Output `5d74443293f663bcd3765721bb690479` (iter16).

**For the full architectural reference** (data path diagram, response builder calling conventions, ELF program-header surgery, code-cave inventory, msg-id taxonomy, Thumb-2 encoding gotchas), see [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## `patch_mtkbt_odex.py`

Patches `MtkBt.odex` with two fixes:

1. **F1** at `0x3e0ea`: `getPreferVersion()` returns 14 (AVRCP 1.4) instead of 10 (BlueAngel internal code for AVRCP 1.3).
2. **F2** at `0x03f21a`: `BluetoothAvrcpService.disable()` resets `sPlayServiceInterface = false` — fixes a BT-toggle bug where the service tears itself down prematurely on second activation because the flag is left stale across restarts.

Recomputes the DEX adler32 checksum embedded in the ODEX header.

**MD5s:** Stock `11566bc23001e78de64b5db355238175` → Output `acc578ada5e41e27475340f4df6afa59`.

---

## `patch_libextavrcp_jni.py`

Patches `libextavrcp_jni.so` to force `g_tg_feature=14` (AVRCP 1.4) and `sdpfeature=0x23`, and raises the GetCapabilities event-list cap in `getCapabilitiesRspNative` from 13 to 14 so a 1.4-capable response can be served if the JNI ever receives an inbound GetCapabilities request.

Four ARM Thumb-2 instruction overwrites:

- **C2a** at `0x3764` and **C2b** at `0x37a8` — in `BluetoothAvrcpService_activateConfig_3req` at `0x375c`: hardcode `g_tg_feature` and `sdpfeature`, bypassing bitmask logic.
- **C3a** at `0x5e56` and **C3b** at `0x5e5c` — in `getCapabilitiesRspNative` (`FUN_005de8`): raise the EventList cap from 13 to 14 (*not* the CONNECT_CNF handler, which lives at `0x62EA` and does not gate on tg_feature).

The bitmask bypass at `0x375c` complements (does not replace) the ODEX `getPreferVersion` patch — both are required for reliable 1.4 negotiation. Verified global addresses: `g_tg_feature` @ `0xD29C`, `g_ct_feature` @ `0xD004`.

**Empirical note:** in testing across three known-good 1.4 controllers (car, Sonos Roam, Samsung TV), `getCapabilitiesRspNative` is never observed firing — mtkbt does not dispatch inbound GetCapabilities to the JNI for any of them. C3a/C3b are correctly applied on-binary but their effect cannot be observed; the cardinality:0 gate is upstream in mtkbt's AVCTP receive path.

**MD5s:** Stock `fd2ce74db9389980b55bccf3d8f15660` → Output `6c348ed9b2da4bb9cc364c16d20e3527`.

---

## `patch_libextavrcp.py`

Patches `libextavrcp.so` to advertise AVRCP 1.4 instead of 1.3.

- **C4** at `0x002e3b`: version constant `0x0103` (1.3) → `0x0104` (1.4).

---

## `patch_adbd.py` *(unwired since v1.7.0; historical record)*

Patched stock `/sbin/adbd` (extracted from the boot.img ramdisk) to skip the privilege drop on startup. Three Thumb-2 patches at vaddr 0x94b8 (file_off 0x14b8) — the drop_privileges block. Each changes the **argument value** of the three calls from `2000` (AID_SHELL) / `11` (gid count) to `0`, so the syscalls execute (and all bionic bookkeeping runs) but the process ends up at uid=0/gid=0:

- **H1** at file_off `0x14b8`: `0b 20` → `00 20` — `movs r0, #0xb` → `movs r0, #0` (setgroups count 11 → 0; clears supplementary groups)
- **H2** at file_off `0x14c6`: `4f f4 fa 60` → `4f f0 00 00` — `mov.w r0, #0x7d0` → `mov.w r0, #0` (setgid arg 2000 → 0)
- **H3** at file_off `0x14d4`: `4f f4 fa 60` → `4f f0 00 00` — `mov.w r0, #0x7d0` → `mov.w r0, #0` (setuid arg 2000 → 0)

**Why patch the binary instead of relying on `default.prop`?** This OEM adbd has stripped the standard `should_drop_privileges()` gating: `strings adbd` returns ZERO references to `ro.secure`, the drop block at 0x94b8 has no preceding conditional, and the privilege drop runs unconditionally on every adbd startup. Setting `ro.secure=0`/`ro.debuggable=1`/`ro.adb.secure=0` in default.prop is therefore inert for the adbd-as-root question — confirmed empirically 2026-05-03 (`adb shell id` returned `uid=2000(shell)` with all three properties correctly set).

**`adb root` is also actively harmful on the un-patched binary.** adbd accepts the `root:` request (ro.debuggable=1 passes the permission check), sets `service.adb.root=1` and exits to be respawned by init. The respawned adbd hits the same unconditional drop_privileges path and ends up at uid 2000 again — but the self-restart cycle requires a USB rebind that stock MTK adbd handles poorly, and the host loses the device until reboot.

**Why arg-zero, not NOP-the-blx (history).** An earlier revision NOPed the three `blx` calls outright (each 4-byte BLX replaced with `movs r0, #0; nop`). On hardware that produced "device offline" — adbd starts and the USB endpoint comes up, but the protocol handshake never completes. The bionic setuid wrapper at `0x19418` does `bl 0x27b30` *before* reaching the actual `mov r7, #0xd5; svc 0` syscall stub at `0x31a70`, doing capability bounding-set and thread-credential bookkeeping that downstream adbd code depends on. Skipping that wrapper entirely produces a process that's technically uid 0 but with inconsistent capabilities/credentials. The arg-zero approach keeps every syscall and bionic wrapper intact — `setuid(0)` when EUID is already 0 is a no-op that runs all the same bookkeeping, just without changing the actual UID. Same for `setgid(0)`.

**Status:** Both revisions caused "device offline" on hardware — script kept as historical record only. Superseded in v1.8.0 by the `/system/xbin/su` install approach.

**MD5s:** Stock `9e7091f1699f89dc905dee3d9d5b23d8` (size 223,132) — Output `9eeb6b3bef1bef19b132936cc3b0b230` (same size).

---

## `patch_bootimg.py` *(unwired since v1.7.0; historical record)*

Patches stock `boot.img` ramdisk so `adb shell` returns a uid 0 shell after flashing. Two changes are applied to the ramdisk in-place inside the gzipped cpio (no extract/repack of device nodes):

1. **`/sbin/adbd`**: applies the H1/H2/H3 byte patches above (delegated to `patch_adbd.patch_bytes()`).
2. **`default.prop`**: edits as belt-and-suspenders for any other Android subsystem that honours these properties:
   - `ro.secure=0` (was 1)
   - `ro.debuggable=1` (was 0)
   - `ro.adb.secure=0` (appended)

**Format-aware:** parses the Android boot.img header, strips/repacks the MTK 512-byte `ROOTFS` ramdisk wrapper, and patches `default.prop` and `/sbin/adbd` *in-place* inside the gzipped cpio stream. Device nodes and entry order are preserved byte-for-byte (the adbd patch keeps the same file size, so cpio record offsets are unchanged).

Pure-Python; no `dd` / `cpio` / `mkbootimg` / `abootimg` shell dependency. The previous bash-based `--root` (removed in v1.2.0) drifted on MTK header byte counts; this implementation removes that failure mode.

**Status:** unwired since v1.7.0 because the H1/H2/H3 adbd byte patches caused "device offline" on hardware. Superseded in v1.8.0 by the `/system/xbin/su` install approach (see `src/su/` below), which leaves `/sbin/adbd` untouched.

---

## `src/su/` (root, v1.8.0+)

Source for a minimal setuid-root `su` binary installed at `/system/xbin/su` by the bash's `--root` flag. Replaces the H1/H2/H3 adbd byte patches that broke ADB protocol on hardware.

- **`src/su/su.c`** — direct ARM-EABI syscall implementation, no libc dependency. `setgid(0)` → `setuid(0)` → `execve("/system/bin/sh", ...)`. Three invocation forms: bare `su` (interactive root shell), `su -c "<cmd>"` (one-off), `su <prog> [args...]` (exec-passthrough).
- **`src/su/start.S`** — ~10-line ARM Thumb-2 entry stub; extracts argc/argv/envp from the ELF process-start stack layout, calls `main`, exits via `__NR_exit`.
- **`src/su/Makefile`** — cross-compile via `arm-linux-gnu-gcc`. `-nostdlib -ffreestanding -static -Os -mthumb -mfloat-abi=soft`; output ~900 bytes, statically linked, no `NEEDED` entries.

**No supply chain beyond GCC + this source.** No SuperSU/Magisk/phh-style binary imported; no manager APK; no whitelist. Trade-off: any process that can exec `/system/xbin/su` becomes root, which is acceptable for a single-user research device but not for a consumer ROM.

**Build:** `cd src/su && make` produces `src/su/build/su`. The bash references this prebuilt path; if missing, `--root` exits with a clear error pointing at `make`. Idempotent.

**Deploy:** the bash's `--root` flag does `install -m 06755 -o root -g root src/su/build/su /system/xbin/su` against the mounted system.img. Post-flash: `adb shell /system/xbin/su -c "id"` → `uid=0(root)`.

**Purpose:** unblock visibility into mtkbt's `__xlog_buf_printf` ring buffer, btsnoop, and live `gdbserver` attach — required to pin down which branch sets `result=0x1000` in `MSG_ID_BT_AVRCP_CONNECT_CNF`.

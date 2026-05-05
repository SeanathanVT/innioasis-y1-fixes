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

Research-probe patcher. **Three patches** against the served AVRCP TG record (Group D — the record that lands on the wire after mtkbt's last-wins merge). Targets the empirically-working **Pixel-1.3 SDP shape** plus the one structural attribute Pixel-1.3 has that Y1 lacks at every patch level: `0x0100` ServiceName.

- **V1** `0x0eba58`: `0x00` → `0x03` — AVRCP 1.0 → 1.3 LSB in served Group D ProfileDescList. Same offset as **C2** in `patch_mtkbt.py` but narrowed to 1.3 instead of 1.4.
- **V2** `0x0eba6d`: `0x00` → `0x02` — AVCTP 1.0 → 1.2 LSB in served Group D ProtocolDescList. Same offset as **B1** in `patch_mtkbt.py` but narrowed to 1.2 instead of 1.3.
- **S1** `0x0f97ec` (12 bytes): replace the `0x0311` SupportedFeatures attribute table entry with a `0x0100` ServiceName entry pointing at the existing "Advanced Audio" SDP-encoded string at file offset `0x0eb9ce` (re-used from mtkbt's A2DP record; peers don't validate ServiceName content, only its presence).
  - Before: `11 03 03 00 59 ba 0e 00 00 00 00 00` (attr=`0x0311`, len=3, ptr=`0x0eba59` → `uint16 0x0001`)
  - After:  `00 01 11 00 ce b9 0e 00 00 00 00 00` (attr=`0x0100`, len=`0x11`, ptr=`0x0eb9ce` → `25 0f "Advanced Audio\0"`)

**Cost of S1:** the served record loses the `0x0311` SupportedFeatures attribute. Empirically Pixel-1.3 advertises features `0x0001` and Sonos engages — but Sonos's behaviour with a record that has *no* `0x0311` attribute is the question this patcher exists to answer. If Sonos refuses to engage, S1 needs a different approach (e.g., add a 12-byte attribute slot elsewhere in the table without sacrificing `0x0311`).

**Mutual exclusion with `patch_mtkbt.py`:** both patchers touch overlapping byte ranges (`0x0eba58`, `0x0eba6d`, the `0x0311` entry slot at `0x0f97ec`). `apply.bash` enforces this — `--avrcp` and `--avrcp-min` cannot both be specified.

**MD5s:** Stock `3af1d4ad8f955038186696950430ffda` → Output `add9e702a275c8ef1faeee5a0d48df51`.

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

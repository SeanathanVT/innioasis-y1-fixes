"""
Trampoline assembly for libextavrcp_jni.so.

Builds the dynamically-assembled trampoline blob that ships at vaddr 0xac54
in the LOAD #1 page-padding area. Cumulative across iterations — currently
five trampolines (iter16 + iter17a + iter19a/b):

  T4 (GetElementAttributes, PDU 0x20):
    - Reads y1-track-info (776B: 8B track_id + 3 × 256B Title/Artist/Album)
    - Reads y1-trampoline-state (16B: last_synced track_id [0..7] + transId [8])
    - If state[0..7] != file[0..7] (the track has actually changed since the
      last CHANGED we emitted) → calls track_changed_rsp with reason=CHANGED,
      arg1=0 (success path), **track_id=&file[0..7] (real synthetic
      audioId, iter19b)**, then writes file[0..7] back into state[0..7] so
      we don't re-emit until Y1MediaBridge moves the track_id again.
    - Replies with 3× get_element_attributes_rsp (Title/Artist/Album)
      using iter13's multi-attribute calling convention (arg2=index 0..2,
      arg3=total 3) so the function accumulates calls 1+2 and only emits
      a single packed msg=540 frame on call 3. iter17b restored this from
      an iter15 regression that had passed arg2=transId/arg3=0 — taking
      the legacy single-shot path and emitting 3 separate frames per query.

  extended_T2 (RegisterNotification(TRACK_CHANGED), PDU 0x31, event 0x02):
    - Reads y1-track-info first 8 bytes (track_id) into a stack buffer.
    - Writes [file_track_id || transId || pad] to y1-trampoline-state so
      T4's later compare doesn't spuriously detect a change against a
      cold-boot zero state file.
    - Replies track_changed_rsp with reason=INTERIM, **track_id=&stack
      buf (real synthetic audioId from y1-track-info, iter19b)**.

  T2 stub at 0x72d4 is rewritten to a single `b.w extended_T2`.

  T_charset (PDU 0x17 InformDisplayableCharacterSet, iter19a — Phase A0):
    - Branched from T4's pre-check when PDU == 0x17.
    - Calls inform_charsetset_rsp via PLT 0x3588 with arg1=0 (success).
    - Tail-jumps to t4_to_epilogue. No state side-effects; the spec-defined
      response is a bare 8-byte ack frame.
    - Bolt EV /work/logs/dual-bolt-iter18d/ confirmed this PDU is sent at
      connection setup; we previously NACKed (msg=520 NOT_IMPLEMENTED) and
      Bolt subsequently degraded its metadata-fetch behavior. iter19a closes.

  T_battery (PDU 0x18 InformBatteryStatusOfCT, iter19a — Phase A0):
    - Branched from T4's pre-check when PDU == 0x18.
    - Calls battery_status_rsp via PLT 0x357c with arg1=0 (success).
    - Same shape as T_charset; the response builder is structurally identical
      apart from outbound msg_id (538 vs 536).

iter19a also: T2 (extended_T2) and T5 trampolines now pass `r1=0` to
track_changed_rsp (was `r1=transId`). Disassembly of the response builder at
libextavrcp.so:0x2458 (and confirmed across all reg_notievent_*_rsp builders
in the same family) shows the function dispatches on r1: r1==0 takes the
spec-correct path that writes reasonCode + event_id + track_id; r1!=0 takes
a reject-shape path that omits the event payload. We had been hitting the
reject path on every TRACK_CHANGED notification — Sonos polled regardless,
masking the bug; the Bolt depends on the CHANGED edge and didn't.

iter16 → iter19d history of the wire-level track_id field:
  - iter15: real track_id in INTERIM — flipped Sonos into "stable identity,
    only refresh on CHANGED" mode. T4 was reactive only (fires on inbound
    GetElementAttributes); Sonos waited for CHANGED that never came because
    Sonos wouldn't poll. 14-min deadlock confirmed on hardware.
  - iter16: 0xFF×8 sentinel (AVRCP §6.7.2 "not bound to a particular media
    element") — Sonos stayed in poll-on-each-event mode, T4 fired on each
    poll, T4's compare against state file detected real track changes and
    emitted CHANGED. Worked.
  - iter17a: T5 added — proactive CHANGED on every Y1 track change via the
    Java→native hook, regardless of CT polling.
  - iter19a: r1=0 fix on track_changed_rsp arg layout (was r1=transId,
    hitting the response builder's reject-shape path; spec-correct emission
    requires r1=0 = success path).
  - iter19b: real track_id (back to iter15's idea) — T5 preempts iter15's
    Sonos deadlock, AND Bolt-EV class strict CTs need real track_ids to
    re-fetch on CHANGED. Targeted Bolt's "ignored every CHANGED after the
    first" behavior.
  - iter19d: REVERTED iter19b. Hardware test against Samsung The Frame Pro
    (/work/logs/dual-tv-iter19c-playpause/) showed real track_ids triggered
    a tight RegisterNotification subscribe storm at ~90 Hz from connection
    setup forward — 3401 size:13 inbound in 38 seconds, sustained. The
    flood saturated AVCTP, causing PASSTHROUGH release frames to drop:
    user pressed Next on the TV remote, music app saw "key held down",
    fast-forwarded the track at ~32× speed (six seekTo() calls each
    +3280ms in track over 600ms wall clock); same shape as the Play/Pause
    "vibrate-loop" reported earlier. Bolt's UI-side block (the original
    motivation) wasn't actually fixed by iter19b anyway, so the revert
    loses nothing for Bolt. Sentinel restored. Bolt becomes an iter20+
    Phase A1+B problem (PLAYBACK_STATUS_CHANGED + GetPlayStatus per
    docs/AVRCP13-COMPLIANCE-PLAN.md).

Inputs at trampoline entry (preserved by saveRegEventSeqId's prologue):
  r5 = JNI instance pointer (conn buffer = r5+8)
  caller's sp+368 = transId    (1 byte)
  caller's sp+374 = lr canary   (2 bytes)
  caller's sp+382 = PDU         (1 byte)
  caller's sp+386 = event_id    (1 byte)
  caller's sp+394 = num_attrs   (1 byte)

PLT entries used (objdump -R + cross-ref against existing patcher comments):
  open                         = 0x363c
  close                        = 0x33d8
  strlen                       = 0x34d4
  memset                       = 0x33fc
  write                        = 0x3630
  get_element_attributes_rsp   = 0x3570
  track_changed_rsp            = 0x3384

read(2) is not in the PLT — we issue the syscall directly via SVC #0
with r7=3 (NR_read on Linux ARM EABI).
"""

from _thumb2asm import Asm

# ---------------------------------------------------------------- constants
T4_VADDR = 0xac54

PLT_open                       = 0x363c
PLT_close                      = 0x33d8
PLT_strlen                     = 0x34d4
PLT_memset                     = 0x33fc
PLT_write                      = 0x3630
PLT_get_element_attributes_rsp = 0x3570
PLT_track_changed_rsp          = 0x3384
# iter19a — Phase A0
PLT_inform_charsetset_rsp      = 0x3588
PLT_battery_status_rsp         = 0x357c
# iter20a — Phase B
PLT_get_playstatus_rsp         = 0x3564
# iter20b — Phase A1 (notification expansion)
PLT_reg_notievent_playback_rsp        = 0x339c
PLT_reg_notievent_reached_end_rsp     = 0x3378
PLT_reg_notievent_reached_start_rsp   = 0x336c
PLT_reg_notievent_pos_changed_rsp     = 0x3360
PLT_reg_notievent_battery_status_rsp  = 0x3354
PLT_reg_notievent_system_status_rsp   = 0x3348

# Function-internal landmarks in saveRegEventSeqId.
EPILOGUE          = 0x712a   # mov r9,#1; canary check; pop {r4-r9, sl, fp, pc}
UNKNOW_INDICATION = 0x65bc   # original "unknow indication" path

# iter17a — JNI helper that returns the BluetoothAvrcpService's per-conn struct.
# Same helper called by the original notificationTrackChangedNative at file
# offset 0x3bda; we re-use it from T5 to obtain the conn buffer (which lives
# at +8 inside the returned struct).
JNI_GET_AVRCP_STATE = 0x36c0

# Stack frame layout inside T4 (after sub.w sp, sp, #FRAME_LEN):
#   sp+0   .. +15   = outgoing stack args for get_element_attributes_rsp (16 B)
#   sp+16  .. +31   = state buffer (16 B; bytes 0..7 = last_seen track_id,
#                                         byte 8     = last RegisterNotification transId,
#                                         bytes 9..15 = padding)
#   sp+32  .. +807  = file buffer (776 B; track_info file image)
T4_FRAME           = 808
T4_OFF_ARGS        = 0
T4_OFF_STATE       = 16
T4_OFF_FILE        = 32
T4_OFF_FILE_TID    = T4_OFF_FILE          # file_buf[0..7] = current track_id
T4_OFF_FILE_TITLE  = T4_OFF_FILE + 8      # file_buf[8..263]
T4_OFF_FILE_ARTIST = T4_OFF_FILE + 264    # file_buf[264..519]
T4_OFF_FILE_ALBUM  = T4_OFF_FILE + 520    # file_buf[520..775]

# Caller-relative offsets shift by T4_FRAME after our SUB SP.
T4_TRANSID_OFF = 368 + T4_FRAME           # 1176
T4_PDU_OFF_ENTRY  = 382                   # before SUB SP (entry pre-check)
T4_LR_CANARY_OFF_ENTRY = 374              # before SUB SP (epilogue restore)

# extended_T2 frame: 16 B for [track_id (8) || transId (1) || pad (7)].
T2_FRAME = 16
T2_OFF_TID = 0
T2_OFF_TRANSID = 8
T2_TRANSID_CALLER_OFF = 368 + T2_FRAME    # 384
T2_EVENT_ID_OFF_ENTRY = 386               # before SUB SP

# T6 (GetPlayStatus, iter20a) frame: 16 B outgoing args + 800 B file_buf.
# The y1-track-info schema extension (Y1MediaBridge versionCode 15+) writes
# four BE u32 fields starting at file offset 776:
#   776..779: duration_ms          BE u32
#   780..783: pos_at_state_change  BE u32
#   784..787: state_change_time    BE u32 (sec since boot; reserved for future
#                                          live-extrapolation, currently unused)
#   792:      playing_flag         u8 (0=STOPPED, 1=PLAYING, 2=PAUSED — direct
#                                      mapping to AVRCP §5.4.3.4 play_status)
T6_FRAME           = 816
T6_OFF_ARGS        = 0
T6_OFF_FILE        = 16
T6_OFF_FILE_DURATION   = T6_OFF_FILE + 776   # 792 - duration_ms
T6_OFF_FILE_POS        = T6_OFF_FILE + 780   # 796 - position_at_state_change
T6_OFF_FILE_STATE_TIME = T6_OFF_FILE + 784   # 800 - state_change_time_sec u32 BE
T6_OFF_FILE_PLAYFLAG   = T6_OFF_FILE + 792   # 808 - playing_flag

# iter22d: stash struct timespec in unused outgoing-args slack so we can call
# clock_gettime(CLOCK_BOOTTIME, &timespec) from inside T6 to live-extrapolate
# the playback position. The outgoing-args region (sp+0..15) is reserved for
# the response builder's stack args, but only sp[0] (1-byte play_status) is
# actually consumed; sp+8..15 is unused and can hold the 8-byte timespec
# without growing T6_FRAME.
T6_OFF_TIMESPEC      = 8
T6_OFF_TIMESPEC_SEC  = T6_OFF_TIMESPEC + 0   # 8 - tv_sec u32
T6_OFF_TIMESPEC_NSEC = T6_OFF_TIMESPEC + 4   # 12 - tv_nsec u32 (we don't use it)

# T8 (RegisterNotification dispatch for events ≠ 0x02, iter20b — Phase A1)
# frame: 800 B file_buf at sp+0. None of the reg_notievent_*_rsp calls T8
# makes need stack args (all 4 ARM args fit in r0/r1/r2/r3), so no outgoing
# args region is reserved. Caller's event_id slot is at sp+T8_EVENT_ID_OFF
# after our SUB SP.
T8_FRAME           = 800
T8_OFF_FILE        = 0
T8_OFF_FILE_POS      = T8_OFF_FILE + 780   # 780 - pos_at_state_change_ms
T8_OFF_FILE_PLAYFLAG = T8_OFF_FILE + 792   # 792 - playing_flag (= AVRCP play_status)
T8_EVENT_ID_OFF    = 386 + T8_FRAME        # caller-frame event_id, post-SUB-SP

# T9 (proactive PLAYBACK_STATUS_CHANGED, iter22b) frame: 16 B state buf at
# sp+0..15 + 800 B y1-track-info file buf at sp+16..815. The state buf reuses
# the existing y1-trampoline-state file's previously-unused byte [9] as
# `last_play_status` (T5 still consumes [0..7] for last_seen track_id and [8]
# for tc_transId; bytes [9..15] were pad before iter22b). Edge detection:
# read y1-track-info[792] (iter20a playing_flag), compare against state[9],
# emit CHANGED on inequality, update state[9], write 16 B back.
#
# T5/T9 race acknowledgment: both read+modify+write the full 16 B state file,
# so a concurrent T5+T9 firing can lose one of the updates. In practice T5
# fires on `metachanged` broadcasts and T9 fires on `playstatechanged`
# broadcasts -- they overlap rarely, and worst case is a single missed
# CHANGED edge which recovers on the next event.
T9_FRAME              = 816
T9_OFF_STATE          = 0
T9_OFF_FILE           = 16
T9_OFF_FILE_PLAYFLAG  = T9_OFF_FILE + 792    # 808 - playing_flag inside file_buf
T9_STATE_LAST_PS_OFF  = T9_OFF_STATE + 9     # 9 - last_play_status inside state_buf

# AVRCP §6.7.2 canned values for events we don't have a Y1 data source for.
# - BATT_STATUS_CHANGED: 0x00 NORMAL is the safe default when we don't have
#   visibility into the device's battery state. (Spec values: 0=NORMAL,
#   1=WARNING, 2=CRITICAL, 3=EXTERNAL.) The Y1 reads its own battery via
#   sysfs but Y1MediaBridge doesn't currently bridge that to the trampoline;
#   could be wired up in a future iter if a CT cares.
# - SYSTEM_STATUS_CHANGED: 0x00 POWERED_ON — we run only when the device is
#   on, so this is always correct. (Spec: 0=POWERED_ON, 1=POWERED_OFF,
#   2=UNPLUGGED.)
BATT_STATUS_NORMAL    = 0x00
SYSTEM_STATUS_POWERED = 0x00

# AVRCP TRACK_CHANGED reason codes
REASON_INTERIM = 0x0F
REASON_CHANGED = 0x0D

# open(2) flags & modes (bionic / Linux generic).
O_RDONLY = 0x0000
O_WRONLY = 0x0001
O_CREAT  = 0x0040
O_TRUNC  = 0x0200
MODE_0666 = 0o666

# Linux ARM EABI syscall numbers.
NR_read = 3
NR_clock_gettime = 263

# Linux clock IDs. CLOCK_BOOTTIME mirrors Android's SystemClock.elapsedRealtime
# (monotonic, includes time spent in suspend) — same source we use on the
# Y1MediaBridge side when stamping mStateChangeTime, so subtracting the two
# yields the wall-clock seconds elapsed since the last play/pause edge.
CLOCK_BOOTTIME = 7

# ---------------------------------------------------------------- builder

def _emit_t4(a: Asm) -> None:
    """T4: GetElementAttributes handler at 0xac54.

    Entry conditions:
      - r5 holds JNI instance struct (conn buffer at r5+8)
      - r0 may be PDU or trashed (we re-read from sp+382)
      - lr canary still at caller's sp+374
    """
    a.label("T4")

    # ---- pre-check: dispatch on PDU ----
    # PDU 0x20 → GetElementAttributes (T4 main body)
    # PDU 0x17 → InformDisplayableCharacterSet (T_charset, iter19a)
    # PDU 0x18 → InformBatteryStatusOfCT (T_battery, iter19a)
    # PDU 0x30 → GetPlayStatus (T6, iter20a — Phase B)
    # else     → restore lr canary + r0 and fall through to "unknow indication"
    a.ldrb_w(0, 13, T4_PDU_OFF_ENTRY)         # r0 = PDU
    a.cmp_imm8(0, 0x20)
    a.beq("t4_main")
    # PDU 0x17 / 0x18 / 0x30 dispatch via bne+b.w because T_charset / T_battery
    # / T6 live past the end of the T4 body (~600+ B forward), beyond beq's
    # ±256 B range.
    a.cmp_imm8(0, 0x17)
    a.bne("t4_after_charset")
    a.b_w("T_charset")
    a.label("t4_after_charset")
    a.cmp_imm8(0, 0x18)
    a.bne("t4_after_battery")
    a.b_w("T_battery")
    a.label("t4_after_battery")
    a.cmp_imm8(0, 0x30)
    a.bne("t4_after_playstatus")
    a.b_w("T6")
    a.label("t4_after_playstatus")
    # Anything else: restore lr canary and fall through to original
    # "unknow indication" path (which expects r0 = conn).
    a.ldrh_w(14, 13, T4_LR_CANARY_OFF_ENTRY)  # ldrh.w lr, [sp, #374]
    a.add_imm_t3(0, 5, 8)                     # add.w r0, r5, #8 (= conn)
    a.b_w("t4_to_unknown")

    a.label("t4_main")
    # ---- allocate stack frame ----
    a.subw(13, 13, T4_FRAME)                  # sub.w sp, sp, #808

    # ---- zero-init state buffer (16 B) ----
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, T4_OFF_STATE + 0)
    a.str_sp_imm(0, T4_OFF_STATE + 4)
    a.str_sp_imm(0, T4_OFF_STATE + 8)
    a.str_sp_imm(0, T4_OFF_STATE + 12)

    # ---- memset(file_buf, 0, 776) ----
    a.add_sp_imm(0, T4_OFF_FILE)              # r0 = sp+32
    a.movs_imm8(1, 0)                         # r1 = 0
    a.movw(2, 776)                            # r2 = 776
    a.blx_imm(PLT_memset)

    # ---- open + syscall_read + close on y1-track-info ----
    a.adr_w(0, "path_track_info")             # r0 = path
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)                       # r0 = fd or -errno
    a.cmp_imm8(0, 0)
    a.blt("t4_skip_track_read")
    a.mov_lo_lo(4, 0)                         # r4 = fd

    a.mov_lo_lo(0, 4)                         # syscall args: r0=fd
    a.add_sp_imm(1, T4_OFF_FILE)              # r1 = file_buf
    a.movw(2, 776)                            # r2 = count
    a.movs_imm8(7, NR_read)                   # r7 = SYS_read
    a.svc(0)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    a.label("t4_skip_track_read")

    # ---- open + syscall_read + close on y1-trampoline-state ----
    a.adr_w(0, "path_state")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t4_skip_state_read")
    a.mov_lo_lo(4, 0)

    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, T4_OFF_STATE)
    a.movs_imm8(2, 16)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    a.label("t4_skip_state_read")

    # ---- compare track_id (file[0..7] vs state[0..7]) ----
    a.ldr_sp_imm(0, T4_OFF_FILE_TID + 0)
    a.ldr_sp_imm(1, T4_OFF_STATE   + 0)
    a.cmp_w(0, 1)
    a.bne("t4_track_changed")
    a.ldr_sp_imm(0, T4_OFF_FILE_TID + 4)
    a.ldr_sp_imm(1, T4_OFF_STATE   + 4)
    a.cmp_w(0, 1)
    a.beq("t4_no_change")

    a.label("t4_track_changed")
    # track_changed_rsp(conn, 0, REASON_CHANGED, &SENTINEL_FFx8)
    # iter19a: r1=0 (was state[8] transId). r1 is the response builder's
    # reject_code arg, not transId — see extended_T2's matching comment.
    # iter19d: revert iter19b — back to the iter16 0xFF×8 sentinel. iter19b
    # had switched to the real track_id from y1-track-info[0..7] to fix the
    # Bolt's "ignored every CHANGED after the first" behavior, but it
    # destabilized the Samsung TV: ~90 Hz RegisterNotification subscribe
    # storm from the moment of pairing, AVCTP saturation, and PASSTHROUGH
    # release frames being dropped (held-key fast-forward on every Next/Prev
    # press, vibrate-loop on Play/Pause). Bolt's UI-side block was never
    # actually fixed by iter19b anyway. Sentinel restored; Bolt becomes an
    # iter20+ Phase A1+B problem.
    a.add_imm_t3(0, 5, 8)                     # r0 = conn
    a.movs_imm8(1, 0)                         # r1 = 0 (success)
    a.movs_imm8(2, REASON_CHANGED)
    a.adr_w(3, "sentinel_ffx8")               # r3 = &(8 bytes 0xFF) — see top-of-file
    a.blx_imm(PLT_track_changed_rsp)

    # Update state in-memory: state[0..7] = file[0..7]
    a.ldr_sp_imm(0, T4_OFF_FILE_TID + 0)
    a.str_sp_imm(0, T4_OFF_STATE   + 0)
    a.ldr_sp_imm(0, T4_OFF_FILE_TID + 4)
    a.str_sp_imm(0, T4_OFF_STATE   + 4)

    # Write 16-byte state file. We use O_WRONLY|O_TRUNC (no O_CREAT) — file is
    # pre-created by Y1MediaBridge.prepareTrackInfoDir(). If it's somehow gone,
    # we silently skip the write rather than create a wrongly-permissioned file.
    a.adr_w(0, "path_state")
    a.movw(1, O_WRONLY | O_TRUNC)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t4_no_change")                     # open failed → skip write
    a.mov_lo_lo(4, 0)

    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, T4_OFF_STATE)
    a.movs_imm8(2, 16)
    a.blx_imm(PLT_write)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    a.label("t4_no_change")

    # ---- 3× get_element_attributes_rsp(conn, 0, idx, 3,
    #                                    [attr_id, charset=0x6a, len, ptr]) ----
    # Per iter13 disassembly of the rsp function (libextavrcp.so:0x2188):
    #   arg2 = attribute INDEX in the response (0..N-1)
    #   arg3 = TOTAL number of attributes
    #   EMIT trigger: (arg2+1 == arg3) AND (arg3 != 0)
    # transId is read by the function itself from conn[17]; we don't pass it.
    # iter15/16/17 regression: arg2=transId, arg3=0 took the legacy "arg3==0
    # → EMIT every call" path, producing 3 separate msg=540 frames per query.
    # Sonos rendered each one in turn → flashing/iterative metadata updates.
    for idx, (label_suffix, attr_id, str_offset) in enumerate((
        ("title",  0x01, T4_OFF_FILE_TITLE),
        ("artist", 0x02, T4_OFF_FILE_ARTIST),
        ("album",  0x03, T4_OFF_FILE_ALBUM),
    )):
        a.label(f"t4_reply_{label_suffix}")
        a.add_sp_imm(0, str_offset)           # r0 = string ptr
        a.blx_imm(PLT_strlen)                 # r0 = strlen
        a.mov_lo_lo(6, 0)                     # r6 = strlen

        a.add_imm_t3(0, 5, 8)                 # r0 = conn
        a.movs_imm8(1, 0)                     # r1 = 0 (with-string flag)
        a.movs_imm8(2, idx)                   # r2 = attribute index (0,1,2)
        a.movs_imm8(3, 3)                     # r3 = total attributes (3)
        a.movs_imm8(4, attr_id)
        a.str_sp_imm(4, T4_OFF_ARGS + 0)      # sp[0]  = attr_id
        a.movs_imm8(4, 0x6A)
        a.str_sp_imm(4, T4_OFF_ARGS + 4)      # sp[4]  = charset (UTF-8)
        a.str_sp_imm(6, T4_OFF_ARGS + 8)      # sp[8]  = strlen
        a.add_sp_imm(4, str_offset)
        a.str_sp_imm(4, T4_OFF_ARGS + 12)     # sp[12] = ptr
        a.blx_imm(PLT_get_element_attributes_rsp)

    # ---- restore stack and tail-call the function epilogue ----
    a.addw(13, 13, T4_FRAME)                  # add.w sp, sp, #808
    a.ldrh_w(14, 13, T4_LR_CANARY_OFF_ENTRY)  # restore lr canary
    a.b_w("t4_to_epilogue")


def _emit_extended_t2(a: Asm) -> None:
    """extended_T2: RegisterNotification(TRACK_CHANGED) handler.

    T2 stub at 0x72d4 jumps here unconditionally (b.w extended_T2). We dispatch
    PDU/event-id internally and fall through to T4 if it's a GetElementAttributes
    that somehow reached us, or to UNKNOW_INDICATION otherwise.
    """
    a.label("extended_T2")

    # r0 contains PDU at entry (set by T1's bridge, which loads PDU and dispatches)
    a.cmp_imm8(0, 0x31)
    a.bne("ext2_check_get_attrs")             # not RegisterNotification → maybe T4

    a.ldrb_w(0, 13, T2_EVENT_ID_OFF_ENTRY)    # r0 = event_id
    a.cmp_imm8(0, 0x02)                       # TRACK_CHANGED?
    a.beq("ext2_track_changed")

    # PDU 0x31 but event ≠ 0x02 → T8 (iter20b) handles events 0x01/0x03/0x04/
    # 0x05/0x06/0x07. T8 returns NOT_IMPLEMENTED for any other event_id.
    a.b_w("T8")

    a.label("ext2_check_get_attrs")
    # PDU != 0x31. If it's 0x20 (GetElementAttributes), let T4 handle it; the
    # T4 entry re-reads PDU from sp+382 so it doesn't matter that r0 is stale.
    a.b_w("T4")

    a.label("ext2_track_changed")
    # ---- allocate small frame: stack scratch for state-file write ----
    # sp+0..7  : will hold file's track_id (read below; persisted to state[0..7]
    #            so T4's next compare sees no change unless Y1MediaBridge moves
    #            the track_id again)
    # sp+8     : transId (set below)
    # sp+9..15 : padding (zeroed below)
    a.subw(13, 13, T2_FRAME)                  # sub.w sp, sp, #16

    # Default track_id = 0×8 (in case file read fails — keeps state file in a
    # well-defined "no synced track" state rather than 0xFF×8 which would later
    # cause T4 to spuriously detect "changed" against a real-id file).
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, T2_OFF_TID + 0)
    a.str_sp_imm(0, T2_OFF_TID + 4)

    # Open + read 8 B + close from y1-track-info. On failure, leave the
    # default 0×8 in place.
    a.adr_w(0, "path_track_info")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("ext2_after_track_read")
    a.mov_lo_lo(4, 0)

    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, T2_OFF_TID)               # r1 = track_id buf
    a.movs_imm8(2, 8)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    a.label("ext2_after_track_read")

    # ---- save state file: [track_id (8) || transId (1) || pad (7)] ----
    # Zero out bytes 8..15 (transId slot + padding) first, then strb transId.
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, T2_OFF_TRANSID + 0)
    a.str_sp_imm(0, T2_OFF_TRANSID + 4)
    a.ldrb_w(0, 13, T2_TRANSID_CALLER_OFF)    # r0 = caller's transId
    a.strb_w(0, 13, T2_OFF_TRANSID)

    # open(path_state, O_WRONLY|O_TRUNC, 0)
    # No O_CREAT — Y1MediaBridge pre-creates the state file at startup. If the
    # open fails, skip the write rather than risk creating a wrongly-permed
    # file as the BT-process uid (which Y1MediaBridge couldn't then re-chmod).
    a.adr_w(0, "path_state")
    a.movw(1, O_WRONLY | O_TRUNC)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("ext2_after_state_write")
    a.mov_lo_lo(4, 0)

    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, T2_OFF_TID)               # source = the 16 B we just built
    a.movs_imm8(2, 16)
    a.blx_imm(PLT_write)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    a.label("ext2_after_state_write")

    # ---- reply track_changed_rsp INTERIM ----
    # iter19a: r1=0 (was r1=transId). Disassembly of the response builder at
    # libextavrcp.so:0x2458 shows `cbnz r5, reject_path` on r1; r1==0 is the
    # spec-correct path that emits reasonCode + event_id + track_id; r1!=0
    # writes a reject-shape frame that omits the event payload. transId is
    # auto-extracted from conn[17] regardless.
    # iter19d: revert iter19b — back to the iter16 0xFF×8 sentinel. The
    # Samsung TV reacted to real track_ids in INTERIM by entering a tight
    # ~90 Hz RegisterNotification subscribe loop from connection setup
    # forward. The flood saturated AVCTP and dropped PASSTHROUGH release
    # frames, so any user button press from the TV remote became a held-key
    # event — Next/Prev fast-forwarded the track at ~32× speed, Play/Pause
    # produced a vibrate-loop. Bolt's UI-side metadata block (the original
    # motivation for iter19b) wasn't actually fixed by switching to real
    # track_ids, so reverting loses nothing for Bolt; Bolt becomes an
    # iter20+ Phase A1+B problem (PLAYBACK_STATUS_CHANGED + GetPlayStatus).
    # See top-of-file "Wire-level track_id history" for the full path.
    a.add_imm_t3(0, 5, 8)                     # r0 = conn
    a.movs_imm8(1, 0)                         # r1 = 0 (success)
    a.movs_imm8(2, REASON_INTERIM)
    a.adr_w(3, "sentinel_ffx8")               # r3 = &(8 bytes 0xFF) — see top-of-file
    a.blx_imm(PLT_track_changed_rsp)

    # Restore stack and branch to epilogue.
    a.addw(13, 13, T2_FRAME)
    a.b_w("t4_to_epilogue")


def _emit_t5(a: Asm) -> None:
    """T5: proactive CHANGED-emit trampoline for iter17a.

    Entered via `b.w T5` from the patched libextavrcp_jni.so::
    notificationTrackChangedNative stub at file offset 0x3bc0. Java's
    handleKeyMessage path (with the cardinality if-eqz NOPed in MtkBt.odex)
    invokes the native method on every track-change broadcast from
    Y1MediaBridge — this lands here, asynchronously to any inbound AVRCP
    command from Sonos.

    On entry:
      - r0 = JNIEnv*  (Java native arg 0)
      - r1 = jobject this  (BluetoothAvrcpService instance)
      - r2..r3 = jbyte arg1, arg2  (ignored — Java passes 0, 0)
      - sp[0..7] = jlong arg3  (ignored — Java passes sMusicId, but we read
                                 the canonical track_id from y1-track-info)
      - lr = caller's return address (Java framework / interpreter)

    Returns: jboolean in r0 (always 1 — JNI return value, but the caller
    ignores it per the smali at sswitch_1a3).

    Logic:
      1. Call the same JNI helper at 0x36c0 the original native used to
         obtain the BluetoothAvrcpService's per-conn struct (used for the
         conn buffer at +8).
      2. Read y1-track-info first 8 bytes (= current track_id).
      3. Read y1-trampoline-state 16 bytes (state[0..7] = last-synced
         track_id, state[8] = last RegisterNotification transId).
      4. If state[0..7] != file[0..7], emit a track_changed_rsp with
         reason=CHANGED, transId=state[8], track_id=&sentinel_ffx8 (the
         iter16 sentinel — same wire-level identity as INTERIM so Sonos
         stays in poll-on-each-event mode), then write file[0..7] back to
         state[0..7] in y1-trampoline-state so we don't re-emit until the
         track moves again.

    The 16-byte state buf and 8-byte file_tid buf live in T5's own stack
    frame — no shared memory with the reactive T4 trampoline (they read
    the same files independently).
    """
    a.label("T5")

    # ---- prologue: save callee-saves we'll trash ----
    # Thumb T1 push: encoding 0xB400 | (LR<<8) | regs[r0..r7]
    # We need r4 + r5 + lr saved.  push {r4, r5, lr} = 0xB430.
    a.raw(bytes([0x30, 0xB5]))                # push {r4, r5, lr}

    # ---- get the BluetoothAvrcpService internal struct ----
    # The helper at JNI_GET_AVRCP_STATE expects r0=env, r1=this — both still
    # set up from the Java native ABI when we entered.
    a.bl_w("jni_get_avrcp_state")             # r0 = struct ptr
    a.mov_lo_lo(4, 0)                         # r4 = struct ptr (preserved)

    # ---- allocate locals: 16 B state buf @ sp+0..15 + 8 B file_tid buf @ sp+16..23 ----
    a.subw(13, 13, 24)                        # sub.w sp, sp, #24

    # ---- read y1-track-info first 8 bytes into file_tid buf (sp+16..23) ----
    # Default 0×8 (in case open/read fails).
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, 16)
    a.str_sp_imm(0, 20)

    a.adr_w(0, "path_track_info")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t5_skip_track_read")
    a.mov_lo_lo(5, 0)                         # r5 = fd

    a.mov_lo_lo(0, 5)
    a.add_sp_imm(1, 16)                       # r1 = file_tid buf
    a.movs_imm8(2, 8)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t5_skip_track_read")

    # ---- read y1-trampoline-state 16 bytes into state buf (sp+0..15) ----
    # Default 0×16.
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, 0)
    a.str_sp_imm(0, 4)
    a.str_sp_imm(0, 8)
    a.str_sp_imm(0, 12)

    a.adr_w(0, "path_state")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t5_skip_state_read")
    a.mov_lo_lo(5, 0)

    a.mov_lo_lo(0, 5)
    a.add_sp_imm(1, 0)                        # r1 = state buf
    a.movs_imm8(2, 16)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t5_skip_state_read")

    # ---- compare state[0..7] vs file_tid[0..7] ----
    a.ldr_sp_imm(0, 0)                        # state[0..3]
    a.ldr_sp_imm(1, 16)                       # file_tid[0..3]
    a.cmp_w(0, 1)
    a.bne("t5_changed")
    a.ldr_sp_imm(0, 4)                        # state[4..7]
    a.ldr_sp_imm(1, 20)                       # file_tid[4..7]
    a.cmp_w(0, 1)
    a.beq("t5_no_change")

    a.label("t5_changed")
    # ---- emit CHANGED via track_changed_rsp ----
    # r0 = conn buffer (= struct + 8); r1 = 0 (success — see top-of-file
    # iter19a note about the response builder's r1 dispatch); r2 = REASON_CHANGED;
    # r3 = &sentinel_ffx8 (iter19d revert; iter19b had pointed at the on-stack
    # file_tid_buf at sp+16, but the Samsung TV reacted to real track_ids
    # with a ~90 Hz RegisterNotification subscribe storm that saturated AVCTP
    # and dropped PASSTHROUGH release frames — see extended_T2 INTERIM's
    # matching comment for the full diagnosis).
    a.add_imm_t3(0, 4, 8)                     # r0 = r4 + 8
    a.movs_imm8(1, 0)                         # r1 = 0 (success)
    a.movs_imm8(2, REASON_CHANGED)
    a.adr_w(3, "sentinel_ffx8")               # r3 = &(8 bytes 0xFF)
    a.blx_imm(PLT_track_changed_rsp)

    # ---- update state in-memory: state[0..7] = file_tid[0..7] ----
    a.ldr_sp_imm(0, 16)
    a.str_sp_imm(0, 0)
    a.ldr_sp_imm(0, 20)
    a.str_sp_imm(0, 4)

    # ---- write 16-byte state buf back to y1-trampoline-state ----
    # O_WRONLY|O_TRUNC, no O_CREAT — Y1MediaBridge.prepareTrackInfoDir()
    # creates the file at startup with the right permissions.
    a.adr_w(0, "path_state")
    a.movw(1, O_WRONLY | O_TRUNC)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t5_no_change")                     # open failed → skip write, still return success
    a.mov_lo_lo(5, 0)

    a.mov_lo_lo(0, 5)
    a.add_sp_imm(1, 0)                        # r1 = state buf
    a.movs_imm8(2, 16)
    a.blx_imm(PLT_write)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t5_no_change")
    # ---- epilogue: return jboolean true ----
    a.movs_imm8(0, 1)
    a.addw(13, 13, 24)                        # add.w sp, sp, #24
    # pop {r4, r5, pc} — Thumb T1 pop: 0xBC00 | (PC<<8) | regs[r0..r7]
    # PC bit is bit 8.  pop {r4, r5, pc} = 0xBD30.
    a.raw(bytes([0x30, 0xBD]))


def _emit_t_charset(a: Asm) -> None:
    """T_charset (iter19a — Phase A0): PDU 0x17 InformDisplayableCharacterSet.

    Branched from T4's pre-check when the inbound PDU byte is 0x17. The CT is
    declaring its accepted charsets to us; we ack with success and continue
    sending UTF-8 (which we already do — there's no spec requirement that we
    actually honor the CT's charset preference, just that we ack the
    declaration).

    Response builder layout (libextavrcp.so:0x2138 — disassembly 2026-05-06):
      void btmtk_avrcp_send_inform_charsetset_rsp(
          void* conn,         // r0
          uint8_t reject,     // r1 = 0 for success
          void* unused        // r2 — pushed but never read
      );
      // Outbound msg_id=536, 8-byte ack frame (transId from conn[17] at
      //  offset 5; rest zeroed).
    """
    a.label("T_charset")
    a.add_imm_t3(0, 5, 8)                     # r0 = conn (= r5+8)
    a.movs_imm8(1, 0)                         # r1 = 0 (success)
    a.blx_imm(PLT_inform_charsetset_rsp)
    a.b_w("t4_to_epilogue")


def _emit_t_battery(a: Asm) -> None:
    """T_battery (iter19a — Phase A0): PDU 0x18 InformBatteryStatusOfCT.

    Branched from T4's pre-check when the inbound PDU byte is 0x18. The CT is
    notifying us of its current battery state; we ack. We don't surface this
    state anywhere — Y1 doesn't have a CT-battery API to feed.

    Response builder at libextavrcp.so:0x2160 is structurally identical to
    inform_charsetset_rsp (same 8-byte ack frame, same r1 dispatch on success
    vs reject); only the outbound msg_id differs (538 vs 536).
    """
    a.label("T_battery")
    a.add_imm_t3(0, 5, 8)                     # r0 = conn
    a.movs_imm8(1, 0)                         # r1 = 0 (success)
    a.blx_imm(PLT_battery_status_rsp)
    a.b_w("t4_to_epilogue")


def _emit_t6(a: Asm) -> None:
    """T6 (iter20a — Phase B): PDU 0x30 GetPlayStatus.

    Branched from T4's pre-check when the inbound PDU byte is 0x30. Returns
    the current track's duration / playback position / play_status in a
    spec-conformant `GetPlayStatus` response per AVRCP 1.4 §5.4.3.4.

    Response builder layout (libextavrcp.so:0x2354 — disassembly 2026-05-06):
      btmtk_avrcp_send_get_playstatus_rsp(
          void* conn,             // r0 = r5+8
          uint8_t reject_code,    // r1 = 0 for success
          uint32_t song_length,   // r2 = duration ms
          uint32_t song_position, // r3 = position ms
          uint8_t  play_status    // sp[0]: 0=STOPPED, 1=PLAYING, 2=PAUSED,
                                  //        3=FWD_SEEK, 4=REV_SEEK, 0xFF=ERROR
      );
      // Outbound msg_id=542, 20 B IPC frame.

    Stack frame: T6_FRAME B (16 outgoing args + 800 file_buf). Read the
    full y1-track-info into file_buf so the existing 776-byte schema fields
    (track_id + title/artist/album) stay intact for any concurrent reader,
    even though T6 itself only consumes the iter20a fields at offsets 776+.

    iter20a returns the position-at-last-state-change directly without live
    extrapolation. CTs poll GetPlayStatus periodically anyway; the position
    "jumps" by the inter-poll interval rather than ticking continuously.
    Skipping live extrapolation avoids a clock_gettime syscall + multiply
    in the hot path, and avoids needing CLOCK_BOOTTIME parity between the
    Java side (SystemClock.elapsedRealtime) and the trampoline. Future
    iters can add live extrapolation if a CT needs continuously-ticking
    position (none have so far).

    The y1-track-info schema fields T6 reads (iter20a Y1MediaBridge writes
    these as big-endian; T6 byte-swaps to host-LE via REV before passing to
    the response builder, which expects register-native order):
      file[776..779]: duration_ms u32 BE
      file[780..783]: position_at_state_change_ms u32 BE
      file[792]:      playing_flag u8 (0=stopped / 1=playing / 2=paused;
                                       maps directly to AVRCP play_status)
    """
    a.label("T6")

    # ---- allocate stack frame ----
    a.subw(13, 13, T6_FRAME)                  # sub.w sp, sp, #816

    # ---- memset(file_buf, 0, 800) ----
    # Default everything to 0 so a partial read (file shorter than 800 B,
    # e.g. an old Y1MediaBridge that hasn't been rebuilt yet for the iter20a
    # schema) gives play_status=0 (STOPPED) and duration/position=0 rather
    # than uninitialized stack garbage.
    a.add_sp_imm(0, T6_OFF_FILE)              # r0 = sp+16
    a.movs_imm8(1, 0)
    a.movw(2, 800)
    a.blx_imm(PLT_memset)

    # ---- open + read y1-track-info ----
    a.adr_w(0, "path_track_info")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t6_skip_track_read")
    a.mov_lo_lo(4, 0)                         # r4 = fd

    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, T6_OFF_FILE)              # r1 = file_buf
    a.movw(2, 800)                            # count = 800
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    a.label("t6_skip_track_read")

    # ---- assemble args for get_playstatus_rsp(conn, 0, dur, pos, play_status) ----
    # sp[0] (caller stack arg slot 0) = play_status byte.
    a.ldrb_w(0, 13, T6_OFF_FILE_PLAYFLAG)     # r0 = playing_flag (0/1/2)
    a.strb_w(0, 13, T6_OFF_ARGS)              # sp[0] = play_status

    # iter22d — live position extrapolation.
    # If playing_flag == 1 (PLAYING):
    #   live_pos = saved_pos + (now_sec - state_change_sec) * 1000
    # Else (STOPPED / PAUSED):
    #   live_pos = saved_pos  (the position field IS the freeze point for
    #                          paused/stopped, which is what CTs expect)
    # Kia EV6 wants a continuously-incrementing position for it to render
    # the progress bar during playback; iter20a's static-position approach
    # (pos == position_at_last_state_change forever) made Kia hide the
    # display entirely while playing.
    a.cmp_imm8(0, 1)                          # r0 still = playing_flag
    a.bne("t6_position_static")

    # ---- clock_gettime(CLOCK_BOOTTIME, &timespec) ----
    # Default the timespec to zero so a syscall failure (extremely unlikely
    # — clock_gettime can't really fail with valid args) gives us a sane
    # fallback (delta_sec computed against now_sec=0 will yield a negative
    # number which when multiplied by 1000 produces a position behind
    # state_change_sec — still bounded, just useless. Kia would just see
    # the same value on each poll and stop animating).
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, T6_OFF_TIMESPEC_SEC)
    a.str_sp_imm(0, T6_OFF_TIMESPEC_NSEC)

    a.movs_imm8(0, CLOCK_BOOTTIME)            # r0 = clk_id = 7
    a.add_sp_imm(1, T6_OFF_TIMESPEC)          # r1 = &timespec
    a.movw(7, NR_clock_gettime)               # r7 = 263
    a.svc(0)

    # ---- delta_sec = now_sec - state_change_sec ----
    a.ldr_sp_imm(0, T6_OFF_FILE_STATE_TIME)   # r0 = state_change_sec (BE)
    a.rev_lo_lo(0, 0)                         # → host order
    a.ldr_sp_imm(1, T6_OFF_TIMESPEC_SEC)      # r1 = now_sec
    a.subs_lo_lo(2, 1, 0)                     # r2 = now_sec - state_change_sec

    # ---- delta_ms = delta_sec * 1000 ----
    a.movw(0, 1000)                           # r0 = 1000
    a.muls_lo_lo(2, 0)                        # r2 = r2 * r0 (= delta_ms)

    # ---- live_pos = saved_pos + delta_ms ----
    a.ldr_sp_imm(3, T6_OFF_FILE_POS)          # r3 = saved_pos (BE)
    a.rev_lo_lo(3, 3)                         # → host order
    a.adds_lo_lo(3, 3, 2)                     # r3 = saved_pos + delta_ms

    a.b_w("t6_emit_response")

    a.label("t6_position_static")
    a.ldr_sp_imm(3, T6_OFF_FILE_POS)          # r3 = saved_pos (BE)
    a.rev_lo_lo(3, 3)                         # → host order

    a.label("t6_emit_response")

    # r2 = duration_ms (BE in file → REV → host order)
    a.ldr_sp_imm(2, T6_OFF_FILE_DURATION)
    a.rev_lo_lo(2, 2)

    # r0 = conn buffer (r5+8); r1 = 0 (success); r3 = position (already set)
    a.add_imm_t3(0, 5, 8)
    a.movs_imm8(1, 0)
    a.blx_imm(PLT_get_playstatus_rsp)

    # ---- restore stack and tail-call epilogue ----
    a.addw(13, 13, T6_FRAME)
    a.b_w("t4_to_epilogue")


def _emit_t8(a: Asm) -> None:
    """T8 (iter20b — Phase A1): RegisterNotification dispatch for events
    other than TRACK_CHANGED (0x02, handled by extended_T2).

    Branched from extended_T2's "PDU 0x31 + non-0x02 event" arm. Reads
    y1-track-info into a stack buffer (for events 0x01 and 0x05 which
    need play_status / position from the schema), then dispatches on
    event_id and emits an INTERIM via the appropriate
    `reg_notievent_*_rsp` PLT entry. All these response builders share
    the same calling convention as their TRACK_CHANGED sibling: r0=conn,
    r1=0 (success), r2=reasonCode, r3=event-specific payload (or unused).
    transId is auto-extracted from conn[17] inside each builder.

    Events handled (per AVRCP 1.4 §6.7.2):
      0x01 PLAYBACK_STATUS_CHANGED  — INTERIM with 1-byte play_status
                                      (from y1-track-info[792], iter20a)
      0x03 TRACK_REACHED_END        — INTERIM, no payload
      0x04 TRACK_REACHED_START      — INTERIM, no payload
      0x05 PLAYBACK_POS_CHANGED     — INTERIM with 4-byte position_ms
                                      (BE in file → REV → host order)
      0x06 BATT_STATUS_CHANGED      — INTERIM with 1-byte canned NORMAL
      0x07 SYSTEM_STATUS_CHANGED    — INTERIM with 1-byte canned POWERED_ON

    Unknown event_id falls through to "unknow indication" (0x65bc) for the
    spec-correct NOT_IMPLEMENTED reject.

    iter20b ships INTERIM-only; no proactive CHANGED for the new events.
    CTs that subscribe will receive the immediate INTERIM (= current
    state) and can re-subscribe periodically to refresh. Proactive CHANGED
    for event 0x01 (PLAYBACK_STATUS) is a candidate for a future iter,
    paired with another smali NOP in MtkBt.odex similar to iter17a's
    cardinality bypass for TRACK_CHANGED.

    Frame: 800 B file_buf at sp+0. None of the response builders need
    stack args (all 4 args fit in r0/r1/r2/r3). Caller's event_id is
    accessed via T8_EVENT_ID_OFF (= 386 + frame).
    """
    a.label("T8")

    # ---- allocate stack frame ----
    a.subw(13, 13, T8_FRAME)                  # sub.w sp, sp, #800

    # ---- memset(file_buf, 0, 800) ----
    # Default everything to 0 so a partial read (file shorter than 800 B
    # — e.g. an old Y1MediaBridge from before the iter20a schema bump)
    # gives play_status=0 (STOPPED) and position=0 rather than uninit
    # stack garbage.
    a.add_sp_imm(0, T8_OFF_FILE)              # r0 = sp+0
    a.movs_imm8(1, 0)
    a.movw(2, 800)
    a.blx_imm(PLT_memset)

    # ---- open + read y1-track-info ----
    a.adr_w(0, "path_track_info")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t8_skip_track_read")
    a.mov_lo_lo(4, 0)                         # r4 = fd

    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, T8_OFF_FILE)              # r1 = file_buf
    a.movw(2, 800)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    a.label("t8_skip_track_read")

    # ---- dispatch on event_id (caller's sp+386, post-SUB-SP at T8_EVENT_ID_OFF) ----
    a.ldrb_w(0, 13, T8_EVENT_ID_OFF)          # r0 = event_id
    a.cmp_imm8(0, 0x01)
    a.bne("t8_check_3")

    # 0x01 PLAYBACK_STATUS_CHANGED
    # reg_notievent_playback_rsp(conn, 0, REASON_INTERIM, play_status)
    a.ldrb_w(3, 13, T8_OFF_FILE_PLAYFLAG)     # r3 = play_status (1=PLAYING / 2=PAUSED / 0=STOPPED)
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(1, 0)                         # success
    a.add_imm_t3(0, 5, 8)                     # r0 = conn
    a.blx_imm(PLT_reg_notievent_playback_rsp)
    a.b_w("t8_done")

    a.label("t8_check_3")
    a.cmp_imm8(0, 0x03)
    a.bne("t8_check_4")
    # 0x03 TRACK_REACHED_END
    # reg_notievent_reached_end_rsp(conn, 0, REASON_INTERIM)
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(1, 0)
    a.add_imm_t3(0, 5, 8)
    a.blx_imm(PLT_reg_notievent_reached_end_rsp)
    a.b_w("t8_done")

    a.label("t8_check_4")
    a.cmp_imm8(0, 0x04)
    a.bne("t8_check_5")
    # 0x04 TRACK_REACHED_START
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(1, 0)
    a.add_imm_t3(0, 5, 8)
    a.blx_imm(PLT_reg_notievent_reached_start_rsp)
    a.b_w("t8_done")

    a.label("t8_check_5")
    a.cmp_imm8(0, 0x05)
    a.bne("t8_check_6")
    # 0x05 PLAYBACK_POS_CHANGED
    # reg_notievent_pos_changed_rsp(conn, 0, REASON_INTERIM, position_ms_u32)
    a.ldr_sp_imm(3, T8_OFF_FILE_POS)          # r3 = pos_at_state_change_ms (BE)
    a.rev_lo_lo(3, 3)                         # → host order
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(1, 0)
    a.add_imm_t3(0, 5, 8)
    a.blx_imm(PLT_reg_notievent_pos_changed_rsp)
    a.b_w("t8_done")

    a.label("t8_check_6")
    a.cmp_imm8(0, 0x06)
    a.bne("t8_check_7")
    # 0x06 BATT_STATUS_CHANGED
    # reg_notievent_battery_status_changed_rsp(conn, 0, REASON_INTERIM, batt_status_u8)
    a.movs_imm8(3, BATT_STATUS_NORMAL)
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(1, 0)
    a.add_imm_t3(0, 5, 8)
    a.blx_imm(PLT_reg_notievent_battery_status_rsp)
    a.b_w("t8_done")

    a.label("t8_check_7")
    a.cmp_imm8(0, 0x07)
    a.bne("t8_unknown_event")
    # 0x07 SYSTEM_STATUS_CHANGED
    # reg_notievent_system_status_changed_rsp(conn, 0, REASON_INTERIM, system_status_u8)
    a.movs_imm8(3, SYSTEM_STATUS_POWERED)
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(1, 0)
    a.add_imm_t3(0, 5, 8)
    a.blx_imm(PLT_reg_notievent_system_status_rsp)
    a.b_w("t8_done")

    a.label("t8_unknown_event")
    # event_id we don't handle (0x08 PLAYER_APPLICATION_SETTING_CHANGED, etc.)
    # → spec-correct NOT_IMPLEMENTED reject via the original "unknow
    # indication" path. Restore stack first so the reject-path's stack-
    # canary check sees the correct sp.
    a.addw(13, 13, T8_FRAME)
    a.ldrh_w(14, 13, T4_LR_CANARY_OFF_ENTRY)  # restore lr canary = SIZE
    a.add_imm_t3(0, 5, 8)                     # restore r0 = conn
    a.b_w("t4_to_unknown")

    a.label("t8_done")
    # ---- restore stack and tail-call epilogue ----
    a.addw(13, 13, T8_FRAME)
    a.b_w("t4_to_epilogue")


def _emit_t9(a: Asm) -> None:
    """T9 (iter22b — Phase A1 follow-up): proactive PLAYBACK_STATUS_CHANGED.

    Entered via `b.w T9` from the patched libextavrcp_jni.so::
    notificationPlayStatusChangedNative stub at file offset 0x3c88. MtkBt's
    handleKeyMessage path -- with the iter22b cardinality if-eqz NOPed at
    sswitch_18a (file offset 0x3c4fe in MtkBt.odex; mirrors iter17a's NOP at
    0x3c530 for sswitch_1a3 / TRACK_CHANGED) -- invokes the native method on
    every Y1MediaBridge `playstatechanged` broadcast, asynchronously to any
    inbound AVRCP RegisterNotification.

    Closes the AVRCP §6.7.1 spec gap left by iter20b: T8 handles event 0x01
    INTERIM-only, never fires the spec-mandated CHANGED frame when the
    play_status actually flips. Symptom: Kia EV6 head unit subscribes to
    event 0x01, gets the immediate INTERIM, then never sees CHANGED, so the
    car-side play/pause icon stays stuck on its initial value even though
    Y1's audio toggles correctly via the PASSTHROUGH path.

    On entry (Java native ABI for `notificationPlayStatusChangedNative(byte,
    byte, byte)`):
      - r0 = JNIEnv*  (Java native arg 0)
      - r1 = jobject this  (BluetoothAvrcpService instance)
      - r2 = jbyte arg1  (ignored — Java passes 0)
      - r3 = jbyte arg2  (ignored — Java passes 0)
      - sp[0] = jbyte arg3 = current play_status from MtkBt's mPlayStatus
                              (we ignore this and read from y1-track-info[792]
                               for consistency with T8's INTERIM data source)
      - lr = caller's return address

    Returns: jboolean in r0 (always 1; the caller ignores it per the smali
    at sswitch_18a).

    Logic:
      1. Call JNI helper at 0x36c0 to obtain the BluetoothAvrcpService's
         per-conn struct (same helper T5 uses; conn buffer at struct + 8).
      2. Read y1-track-info into file_buf @ sp+16..815. file[792] = current
         playing_flag (0=STOPPED, 1=PLAYING, 2=PAUSED — direct AVRCP
         play_status enum per AVRCP 1.4 §5.4.3.4).
      3. Read y1-trampoline-state (16 B) into state_buf @ sp+0..15.
         state[9] = last_play_status (previously pad).
      4. If file[792] != state[9]: emit CHANGED via
         reg_notievent_playback_rsp(conn, 0, REASON_CHANGED, file[792]).
         transId is auto-extracted from conn[17] by the response builder
         (same pattern T5 uses for track_changed_rsp). Update state[9] =
         file[792] and write 16 B back.

    Race with T5: both read+modify+write the full 16 B state file. Concurrent
    firings can lose one update. In practice T5 fires on `metachanged` and
    T9 fires on `playstatechanged` -- they overlap rarely, and the worst
    case is a single missed CHANGED that the next event recovers.
    """
    a.label("T9")

    # ---- prologue: save callee-saves we'll trash ----
    # push {r4, r5, lr} = 0xB430.
    a.raw(bytes([0x30, 0xB5]))

    # ---- get the BluetoothAvrcpService internal struct ----
    a.bl_w("jni_get_avrcp_state")             # r0 = struct ptr
    a.mov_lo_lo(4, 0)                         # r4 = struct ptr (preserved)

    # ---- allocate locals: 16 B state buf @ sp+0 + 800 B file buf @ sp+16 ----
    a.subw(13, 13, T9_FRAME)                  # sub.w sp, sp, #816

    # ---- memset(file_buf, 0, 800) ----
    # Default everything to 0 so a partial read (file shorter than 800 B
    # — old Y1MediaBridge from before the iter20a schema bump) gives
    # play_status=0 (STOPPED) rather than uninit stack garbage.
    a.add_sp_imm(0, T9_OFF_FILE)
    a.movs_imm8(1, 0)
    a.movw(2, 800)
    a.blx_imm(PLT_memset)

    # ---- memset(state_buf, 0, 16) ----
    a.add_sp_imm(0, T9_OFF_STATE)
    a.movs_imm8(1, 0)
    a.movs_imm8(2, 16)
    a.blx_imm(PLT_memset)

    # ---- open + read y1-track-info into file_buf ----
    a.adr_w(0, "path_track_info")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t9_skip_track_read")
    a.mov_lo_lo(5, 0)                         # r5 = fd

    a.mov_lo_lo(0, 5)
    a.add_sp_imm(1, T9_OFF_FILE)              # r1 = file_buf
    a.movw(2, 800)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t9_skip_track_read")

    # ---- open + read y1-trampoline-state into state_buf ----
    a.adr_w(0, "path_state")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t9_skip_state_read")
    a.mov_lo_lo(5, 0)

    a.mov_lo_lo(0, 5)
    a.add_sp_imm(1, T9_OFF_STATE)             # r1 = state_buf
    a.movs_imm8(2, 16)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t9_skip_state_read")

    # ---- compare file[792] (current play_status) vs state[9] (last) ----
    a.ldrb_w(0, 13, T9_OFF_FILE_PLAYFLAG)     # r0 = current play_status
    a.ldrb_w(1, 13, T9_STATE_LAST_PS_OFF)     # r1 = last_play_status
    a.cmp_w(0, 1)
    a.beq("t9_no_change")

    # ---- emit CHANGED via reg_notievent_playback_rsp ----
    # r0 = conn (= struct + 8); r1 = 0 success; r2 = REASON_CHANGED;
    # r3 = play_status (from file_buf[792]). transId is auto-extracted from
    # conn[17] by the response builder (same convention as T5).
    a.add_imm_t3(0, 4, 8)                     # r0 = r4 + 8 (conn)
    a.movs_imm8(1, 0)                         # success
    a.movs_imm8(2, REASON_CHANGED)
    a.ldrb_w(3, 13, T9_OFF_FILE_PLAYFLAG)     # r3 = play_status
    a.blx_imm(PLT_reg_notievent_playback_rsp)

    # ---- update state[9] = file[792] in-memory ----
    a.ldrb_w(0, 13, T9_OFF_FILE_PLAYFLAG)
    a.strb_w(0, 13, T9_STATE_LAST_PS_OFF)

    # ---- write 16-byte state buf back to y1-trampoline-state ----
    a.adr_w(0, "path_state")
    a.movw(1, O_WRONLY | O_TRUNC)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t9_no_change")                     # open failed → skip write, still return success
    a.mov_lo_lo(5, 0)

    a.mov_lo_lo(0, 5)
    a.add_sp_imm(1, T9_OFF_STATE)             # r1 = state_buf
    a.movs_imm8(2, 16)
    a.blx_imm(PLT_write)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t9_no_change")
    # ---- epilogue: return jboolean true ----
    a.movs_imm8(0, 1)
    a.addw(13, 13, T9_FRAME)
    # pop {r4, r5, pc} = 0xBD30.
    a.raw(bytes([0x30, 0xBD]))


def build() -> tuple[bytes, dict[str, int]]:
    """Build the LOAD-#1-padding trampoline code blob.

    Returns:
        (bytes, label_addresses)
        - bytes: the full assembled blob to splice in at vaddr T4_VADDR
        - label_addresses: dict of name → vaddr (so the patcher can wire the
          T2 stub at 0x72d4 to extended_T2)
    """
    a = Asm(T4_VADDR)

    # External landmarks — pre-register so b_w / bl_w resolve to absolute targets.
    a.labels["t4_to_unknown"] = UNKNOW_INDICATION
    a.labels["t4_to_epilogue"] = EPILOGUE
    a.labels["jni_get_avrcp_state"] = JNI_GET_AVRCP_STATE

    _emit_t4(a)
    _emit_extended_t2(a)
    _emit_t5(a)
    _emit_t_charset(a)                        # iter19a — Phase A0
    _emit_t_battery(a)                        # iter19a — Phase A0
    _emit_t6(a)                               # iter20a — Phase B
    _emit_t8(a)                               # iter20b — Phase A1
    _emit_t9(a)                               # iter22b — proactive PLAYBACK_STATUS_CHANGED

    # Path strings, 4-byte-aligned for clean ADR offsets.
    a.align(4)
    a.label("path_track_info")
    a.asciiz("/data/data/com.y1.mediabridge/files/y1-track-info")
    a.align(4)
    a.label("path_state")
    a.asciiz("/data/data/com.y1.mediabridge/files/y1-trampoline-state")
    a.align(4)

    # iter16 sentinel: 8 bytes of 0xFF passed as the track_id pointer to
    # btmtk_avrcp_send_reg_notievent_track_changed_rsp for both INTERIM and
    # CHANGED responses. AVRCP 1.4 spec §6.7.2 — track_id 0xFFFFFFFFFFFFFFFF
    # means "this information is not bound to a particular media element",
    # which keeps the CT in poll-on-each-event mode.
    a.label("sentinel_ffx8")
    a.raw(b"\xFF" * 8)

    blob = a.resolve()
    addrs = {k: v for k, v in a.labels.items()
             if k not in ("t4_to_unknown", "t4_to_epilogue",
                          "jni_get_avrcp_state")}
    return blob, addrs


if __name__ == "__main__":
    blob, addrs = build()
    print(f"blob length: {len(blob)} bytes  (LOAD #1 padding budget: 3712 bytes)")
    print(f"final vaddr: 0x{T4_VADDR + len(blob):x}")
    print()
    print("labels:")
    for name, addr in sorted(addrs.items(), key=lambda kv: kv[1]):
        print(f"  0x{addr:06x}  {name}")

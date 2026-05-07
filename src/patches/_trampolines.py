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
T6_OFF_FILE_DURATION = T6_OFF_FILE + 776   # 792 - duration_ms
T6_OFF_FILE_POS      = T6_OFF_FILE + 780   # 796 - position_at_state_change
T6_OFF_FILE_PLAYFLAG = T6_OFF_FILE + 792   # 808 - playing_flag

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

    # PDU 0x31 but unknown event → fall through to original NOT_IMPLEMENTED.
    a.b_w("t4_to_unknown")

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

    # r2 = duration_ms (BE in file → REV → host order)
    a.ldr_sp_imm(2, T6_OFF_FILE_DURATION)
    a.rev_lo_lo(2, 2)

    # r3 = position_at_state_change_ms (BE → REV → host order)
    a.ldr_sp_imm(3, T6_OFF_FILE_POS)
    a.rev_lo_lo(3, 3)

    # r0 = conn buffer (r5+8); r1 = 0 (success)
    a.add_imm_t3(0, 5, 8)
    a.movs_imm8(1, 0)
    a.blx_imm(PLT_get_playstatus_rsp)

    # ---- restore stack and tail-call epilogue ----
    a.addw(13, 13, T6_FRAME)
    a.b_w("t4_to_epilogue")


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

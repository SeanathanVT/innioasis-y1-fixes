"""
Trampoline assembly for libextavrcp_jni.so.

Builds the dynamically-assembled trampoline blob that ships at vaddr 0xac54
in the LOAD #1 page-padding area. Per-trampoline behavior, entry conditions,
and design rationale (including the wire-level `track_id` sentinel choice
and the `r1=0` calling convention shared by all `reg_notievent_*_rsp`
builders): see `docs/PATCHES.md` `## patch_libextavrcp_jni.py`. Stack-frame
+ `saveRegEventSeqId` calling convention: `docs/ARCHITECTURE.md`. Schema
of the on-disk file the trampolines read: `docs/BT-COMPLIANCE.md` §4.
PLT inventory: `PLT_*` constants below; full table in COMPLIANCE.md §3.

Trampolines emitted by this module (T1 + T2 stub are written separately by
patch_libextavrcp_jni.py at other code-cave addresses):

  extended_T2  PDU 0x31 event 0x02 RegisterNotification(TRACK_CHANGED)
  T4           PDU 0x20 GetElementAttributes — also routes PDU 0x17 / 0x18 /
               0x30 / 0x40 / 0x41 to T_charset / T_battery / T6 / T_continuation
  T5           proactive AVRCP §5.4.2 track-edge 3-tuple (REACHED_END
               gated on natural-end + TRACK_CHANGED + REACHED_START)
  T_charset    PDU 0x17 InformDisplayableCharacterSet ack
  T_battery    PDU 0x18 InformBatteryStatusOfCT ack
  T_continuation PDU 0x40 / 0x41 → AV/C NOT_IMPLEMENTED reject
  T6           PDU 0x30 GetPlayStatus, w/ clock_gettime live position
  T8           PDU 0x31 events ≠ 0x02 INTERIM dispatcher
  T9           proactive PLAYBACK_STATUS / BATT_STATUS / PLAYBACK_POS /
               PLAYER_APPLICATION_SETTING CHANGED, entered from
               notificationPlayStatusChangedNative

Implementation note. read(2) is not in the PLT — we issue the syscall
directly via SVC #0 with r7=3 (NR_read on Linux ARM EABI). clock_gettime(2)
is via SVC #0 with r7=263 (NR_clock_gettime).
"""

import os

from _thumb2asm import Asm

# Build-time debug toggle. `apply.bash --debug` exports KOENSAYR_DEBUG=1.
# Placeholder — when set, future trampoline edits could call
# `__android_log_print` (via a new PLT entry) at trampoline entry / exit so
# native-side traces show up under `adb logcat -s Y1Patch:*`. Currently
# no trampoline emits Log calls; the flag is wired so future edits can
# hook it without re-plumbing.
DEBUG_LOGGING = os.environ.get("KOENSAYR_DEBUG", "") == "1"

# ---------------------------------------------------------------- constants
T4_VADDR = 0xac54

PLT_open                       = 0x363c
PLT_close                      = 0x33d8
PLT_strlen                     = 0x34d4
PLT_memset                     = 0x33fc
PLT_write                      = 0x3630
PLT_get_element_attributes_rsp = 0x3570
PLT_track_changed_rsp          = 0x3384
# Inform PDUs (CT→TG informational acks).
PLT_inform_charsetset_rsp      = 0x3588
PLT_battery_status_rsp         = 0x357c
# GetPlayStatus.
PLT_get_playstatus_rsp         = 0x3564
# RegisterNotification dispatcher (events ≠ 0x02).
PLT_reg_notievent_playback_rsp        = 0x339c
PLT_reg_notievent_reached_end_rsp     = 0x3378
PLT_reg_notievent_reached_start_rsp   = 0x336c
PLT_reg_notievent_pos_changed_rsp     = 0x3360
PLT_reg_notievent_battery_status_rsp  = 0x3354
PLT_reg_notievent_system_status_rsp   = 0x3348
PLT_reg_notievent_player_appsettings_rsp = 0x345c

# PlayerApplicationSettings PDUs 0x11-0x16 (T_papp).
PLT_list_player_attrs_rsp        = 0x35d0
PLT_list_player_values_rsp       = 0x35c4
PLT_get_curplayer_value_rsp      = 0x35b8
PLT_set_player_value_rsp         = 0x3594
PLT_get_player_attr_text_rsp     = 0x35ac
PLT_get_player_value_text_rsp    = 0x35a0

# Function-internal landmarks in saveRegEventSeqId.
EPILOGUE          = 0x712a   # mov r9,#1; canary check; pop {r4-r9, sl, fp, pc}
UNKNOW_INDICATION = 0x65bc   # original "unknow indication" path

# JNI helper that returns the BluetoothAvrcpService's per-conn struct.
# Same helper called by the original notificationTrackChangedNative at file
# offset 0x3bda; we re-use it from T5 to obtain the conn buffer (which lives
# at +8 inside the returned struct).
JNI_GET_AVRCP_STATE = 0x36c0

# Stack frame layout inside T4 (after sub.w sp, sp, #FRAME_LEN):
#   sp+0   .. +15    = outgoing stack args for get_element_attributes_rsp (16 B)
#   sp+16  .. +31    = state buffer (16 B; mirrors y1-trampoline-state schema:
#                                          bytes 0..7 = last_seen track_id,
#                                          byte 8     = last RegisterNotification transId,
#                                          byte 9     = last_play_status (T9 edge),
#                                          byte 10    = last_battery_status (T9 edge),
#                                          byte 11    = last_repeat_avrcp (T9 papp edge),
#                                          byte 12    = last_shuffle_avrcp (T9 papp edge),
#                                          bytes 13..15 = padding)
#   sp+32  .. +1135  = file buffer (1104 B; full y1-track-info image)
#                      0..7      track_id
#                      8..263    Title  (256 B UTF-8, null-padded)
#                      264..519  Artist
#                      520..775  Album
#                      776..779  duration_ms                BE u32 (T6)
#                      780..783  pos_at_state_change_ms     BE u32 (T6, T8 event 0x05)
#                      784..787  state_change_time_ms       BE u32 (CLOCK_BOOTTIME
#                                                                    ms-since-boot, full ms
#                                                                    precision; T6 / T9
#                                                                    live-position extrapolation)
#                      788..791  pad
#                      792       playing_flag               u8 (AVRCP §5.4.1 Tbl 5.26 enum;
#                                                                T6, T8 event 0x01, T9)
#                      793       previous_track_natural_end u8 (T5 gate for §5.4.2 Tbl 5.31
#                                                                TRACK_REACHED_END CHANGED)
#                      794       battery_status             u8 (AVRCP §5.4.2 Tbl 5.35 enum;
#                                                                T8 event 0x06, T9)
#                      795       repeat_avrcp               u8 (AVRCP §5.2.4 Tbl 5.20 enum;
#                                                                T_papp 0x13, T8 event 0x08, T9)
#                      796       shuffle_avrcp              u8 (AVRCP §5.2.4 Tbl 5.21 enum;
#                                                                T_papp 0x13, T8 event 0x08, T9)
#                      797..799  pad
#                      800..815  TrackNumber                UTF-8 ASCII decimal (16 B)
#                      816..831  TotalNumberOfTracks        UTF-8 ASCII decimal (16 B)
#                      832..847  PlayingTime                UTF-8 ASCII decimal ms (16 B)
#                      848..1103 Genre                      UTF-8 (256 B)
T4_FRAME           = 1136
T4_FILE_SIZE       = 1104
T4_OFF_ARGS        = 0
T4_OFF_STATE       = 16
T4_OFF_FILE        = 32
T4_OFF_FILE_TID    = T4_OFF_FILE          # file_buf[0..7] = current track_id
T4_OFF_FILE_TITLE  = T4_OFF_FILE + 8      # file_buf[8..263]
T4_OFF_FILE_ARTIST = T4_OFF_FILE + 264    # file_buf[264..519]
T4_OFF_FILE_ALBUM  = T4_OFF_FILE + 520    # file_buf[520..775]
T4_OFF_FILE_TRACK_NUM   = T4_OFF_FILE + 800  # file_buf[800..815]
T4_OFF_FILE_TOTAL_NUM   = T4_OFF_FILE + 816  # file_buf[816..831]
T4_OFF_FILE_PLAY_TIME   = T4_OFF_FILE + 832  # file_buf[832..847]
T4_OFF_FILE_GENRE       = T4_OFF_FILE + 848  # file_buf[848..1103]

# Caller-relative offsets shift by T4_FRAME after our SUB SP.
T4_TRANSID_OFF = 368 + T4_FRAME           # 1176
T4_PDU_OFF_ENTRY  = 382                   # before SUB SP (entry pre-check)
T4_LR_CANARY_OFF_ENTRY = 374              # before SUB SP (epilogue restore)
# Inbound GetElementAttributes request body (AVRCP wire layout):
#   caller_sp + 382 = PDU (0x20), 383 = PT, 384..385 = ParamLen BE u16,
#   386..393 = Identifier (8 B, 0x0=PLAYING),
#   394 = NumAttributes (1 B), 395+ = AttributeID[N] (4 B BE each).
# Post-SUB-SP, these slots are at sp + offset + T4_FRAME.
T4_NUMATTR_OFF = 394 + T4_FRAME           # 1530 - inbound NumAttributes byte
T4_ATTRIDS_OFF = 395 + T4_FRAME           # 1531 - inbound AttributeID[0] base

# extended_T2 frame: 16 B for [track_id (8) || transId (1) || pad (7)].
T2_FRAME = 16
T2_OFF_TID = 0
T2_OFF_TRANSID = 8
T2_OFF_SUB_SCRATCH = 12   # 4 B scratch for subscription-write byte source
                          #   (after T2_OFF_TID + T2_OFF_TRANSID, within frame)
T2_TRANSID_CALLER_OFF = 368 + T2_FRAME    # 384
T2_EVENT_ID_OFF_ENTRY = 386               # before SUB SP

# T6 (GetPlayStatus) frame: 16 B outgoing args + 800 B file_buf.
# The y1-track-info schema's GetPlayStatus block at offset 776 carries
# four BE u32 fields:
#   776..779: duration_ms          BE u32
#   780..783: pos_at_state_change  BE u32
#   784..787: state_change_time_ms BE u32 (ms since CLOCK_BOOTTIME boot — paired with
#                                          clock_gettime in T6/T9 for ms-precise
#                                          live-position extrapolation)
#   792:      playing_flag         u8 (0=STOPPED, 1=PLAYING, 2=PAUSED — direct
#                                      mapping to AVRCP 1.3 §5.4.1 Table 5.26
#                                      `PlayStatus` field allowed-values enum)
T6_FRAME           = 816
T6_OFF_ARGS        = 0
T6_OFF_FILE        = 16
T6_OFF_FILE_DURATION   = T6_OFF_FILE + 776   # 792 - duration_ms
T6_OFF_FILE_POS        = T6_OFF_FILE + 780   # 796 - position_at_state_change
T6_OFF_FILE_STATE_TIME = T6_OFF_FILE + 784   # 800 - state_change_time_ms u32 BE
T6_OFF_FILE_PLAYFLAG   = T6_OFF_FILE + 792   # 808 - playing_flag

# Stash struct timespec in unused outgoing-args slack so we can call
# clock_gettime(CLOCK_BOOTTIME, &timespec) from inside T6 to live-extrapolate
# the playback position. The outgoing-args region (sp+0..15) is reserved for
# the response builder's stack args, but only sp[0] (1-byte play_status) is
# actually consumed; sp+8..15 is unused and can hold the 8-byte timespec
# without growing T6_FRAME.
T6_OFF_TIMESPEC      = 8
T6_OFF_TIMESPEC_SEC  = T6_OFF_TIMESPEC + 0   # 8 - tv_sec u32
T6_OFF_TIMESPEC_NSEC = T6_OFF_TIMESPEC + 4   # 12 - tv_nsec u32 (we don't use it)

# T8 (RegisterNotification INTERIM dispatch for events ≠ 0x02) frame:
# 800 B file_buf at sp+0. None of the reg_notievent_*_rsp calls T8 makes
# need stack args (all 4 ARM args fit in r0 / r1 / r2 / r3), so no outgoing args
# region is reserved. Caller's event_id slot is at sp+T8_EVENT_ID_OFF
# after our SUB SP.
T8_FRAME           = 808                   # 800 file_buf + 8 timespec
T8_OFF_FILE        = 0
T8_OFF_FILE_POS      = T8_OFF_FILE + 780   # 780 - pos_at_state_change_ms
T8_OFF_FILE_STATE_TIME = T8_OFF_FILE + 784 # 784 - state_change_time_ms (BE u32)
T8_OFF_FILE_PLAYFLAG = T8_OFF_FILE + 792   # 792 - playing_flag (= AVRCP play_status)
T8_OFF_FILE_BATTERY  = T8_OFF_FILE + 794   # 794 - battery_status u8 (AVRCP §5.4.2
                                            #       Tbl 5.34/5.35 enum: 0=NORMAL,
                                            #       1=WARNING, 2=CRITICAL, 3=EXTERNAL,
                                            #       4=FULL_CHARGE)
T8_OFF_FILE_REPEAT   = T8_OFF_FILE + 795   # 795 - repeat_avrcp (AVRCP §5.2.4 Tbl 5.20)
T8_OFF_FILE_SHUFFLE  = T8_OFF_FILE + 796   # 796 - shuffle_avrcp (AVRCP §5.2.4 Tbl 5.21)
# Timespec for clock_gettime(CLOCK_BOOTTIME) — used by event 0x05 INTERIM to
# live-extrapolate position so a fresh CT subscribe doesn't see stale
# pos_at_state_change_ms. Same magic-multiply nsec-to-ms math T6/T9 use.
T8_OFF_TIMESPEC      = T8_OFF_FILE + 800   # 800 - struct timespec
T8_OFF_TIMESPEC_SEC  = T8_OFF_TIMESPEC + 0
T8_OFF_TIMESPEC_NSEC = T8_OFF_TIMESPEC + 4
T8_EVENT_ID_OFF    = 386 + T8_FRAME        # caller-frame event_id, post-SUB-SP

# T9 (proactive PLAYBACK_STATUS_CHANGED + BATT_STATUS_CHANGED + PLAYBACK_POS
# + PLAYER_APPLICATION_SETTING_CHANGED) frame:
#   sp+0..7    = outgoing-args region (only reg_notievent_player_appsettings_
#                changed_rsp uses stack args — its 5th + 6th are at sp[0]/sp[4])
#   sp+8..23   = state buf (16 B; mirrors y1-trampoline-state schema)
#   sp+24..823 = y1-track-info file buf (800 B)
#   sp+824..831 = struct timespec for clock_gettime(CLOCK_BOOTTIME)
#
# State byte usage:
#   [0..7]  last_seen track_id (T5)
#   [8]     last RegisterNotification transId (T5)
#   [9]     last_play_status (T9 edge)
#   [10]    last_battery_status (T9 edge)
#   [11]    last_repeat_avrcp (T9 papp edge)
#   [12]    last_shuffle_avrcp (T9 papp edge)
#   [13..15] padding
#
# T5 / T9 race acknowledgment: both read+modify+write the full 16 B state file,
# so a concurrent T5+T9 firing can lose one of the updates. In practice T5
# fires on `metachanged` broadcasts and T9 fires on `playstatechanged`
# broadcasts -- they overlap rarely, and worst case is a single missed
# CHANGED edge which recovers on the next event.
T9_FRAME              = 836        # 8 args + 20 state + 800 file_buf + 8 timespec
T9_OFF_ARGS           = 0
T9_OFF_STATE          = 8
T9_OFF_FILE           = 28          # state grew 16→20 for sub_* gates
T9_OFF_FILE_DURATION   = T9_OFF_FILE + 776   # duration_ms (BE u32, T6 reads same)
T9_OFF_FILE_POS        = T9_OFF_FILE + 780   # pos_at_state_change_ms (BE u32)
T9_OFF_FILE_STATE_TIME = T9_OFF_FILE + 784   # state_change_time_ms (BE u32)
T9_OFF_FILE_PLAYFLAG   = T9_OFF_FILE + 792   # playing_flag inside file_buf
T9_OFF_FILE_BATTERY    = T9_OFF_FILE + 794   # battery_status inside file_buf
T9_OFF_FILE_REPEAT     = T9_OFF_FILE + 795   # repeat_avrcp (AVRCP §5.2.4 Tbl 5.20)
T9_OFF_FILE_SHUFFLE    = T9_OFF_FILE + 796   # shuffle_avrcp (AVRCP §5.2.4 Tbl 5.21)
T9_STATE_LAST_PS_OFF      = T9_OFF_STATE + 9   # last_play_status
T9_STATE_LAST_BATT_OFF    = T9_OFF_STATE + 10  # last_battery_status
T9_STATE_LAST_REPEAT_OFF  = T9_OFF_STATE + 11  # last_repeat_avrcp (papp edge)
T9_STATE_LAST_SHUFFLE_OFF = T9_OFF_STATE + 12  # last_shuffle_avrcp (papp edge)
# Per-subscription gates for AVRCP §6.7.1's "TG shall notify only once"
# semantics. T2 / T8 INTERIM emit for a given event sets the matching byte
# = 1; T5 / T9 CHANGED emit reads + clears the byte. Without these, strict
# CTs (Bolt / Kia) reject CHANGEDs after the first one and freeze their UI
# mirrors. y1-trampoline-state is 20 bytes; bytes 13..19 hold one byte per
# event we emit CHANGED for. Bytes 16..19 added 2026-05-13; older 16-byte
# files degrade gracefully (read returns zero-fill on the new bytes, so
# the gate evaluates as "not subscribed" and the CT just misses
# notifications until the file is rebuilt).
T9_STATE_SUB_POS_OFF      = T9_OFF_STATE + 13  # sub_pos_changed (event 0x05)
T9_STATE_SUB_PLAY_OFF     = T9_OFF_STATE + 14  # sub_play_status (event 0x01)
T9_STATE_SUB_PAPP_OFF     = T9_OFF_STATE + 15  # sub_papp (event 0x08)
T9_STATE_SUB_TRACK_OFF    = T9_OFF_STATE + 16  # sub_track_changed (event 0x02)
T9_STATE_SUB_REND_OFF     = T9_OFF_STATE + 17  # sub_track_reached_end (event 0x03)
T9_STATE_SUB_RSTART_OFF   = T9_OFF_STATE + 18  # sub_track_reached_start (event 0x04)
T9_STATE_SUB_BATT_OFF     = T9_OFF_STATE + 19  # sub_battery (event 0x06)
# T9's position-emit block needs a struct timespec for clock_gettime(CLOCK_BOOTTIME)
# to live-extrapolate the playback position (same arithmetic T6 does for
# GetPlayStatus). Place the 8 B timespec immediately after the file buf.
T9_OFF_TIMESPEC      = T9_OFF_FILE + 800     # struct timespec
T9_OFF_TIMESPEC_SEC  = T9_OFF_TIMESPEC + 0
T9_OFF_TIMESPEC_NSEC = T9_OFF_TIMESPEC + 4

# T5 (proactive TRACK_CHANGED + TRACK_REACHED_END / START 3-tuple) frame:
# 16 B state buf at sp+0..15 + 800 B y1-track-info file buf at sp+16..815.
# Same shape as T9. T5 reads enough of y1-track-info to see the natural-end
# flag at offset 793 (= sp + T5_OFF_FILE_NATURAL_END).
T5_FRAME              = 820                  # +4 vs original to fit 20-B state
T5_OFF_STATE          = 0
T5_OFF_FILE           = 20                   # state grew 16→20 for sub_* gates
T5_OFF_FILE_TID       = T5_OFF_FILE          # 20 - track_id (8 B) at file[0..7]
T5_OFF_FILE_NATURAL_END = T5_OFF_FILE + 793  # 813 - previous_track_natural_end u8
                                              #       at file[793] (set by the
                                              #       music app before the
                                              #       metachanged broadcast that
                                              #       lands here).

# T_papp (PApp Settings PDUs 0x11-0x16) frame:
#   sp+0..23  : outgoing args region (24 B; max-of-needs is 5 stack args =
#               20 B for get_player_value_text_rsp, rounded to 24 for alignment)
# Caller's inbound AVRCP param body sits at sp+386+ (= entry-relative;
# post-SUB-SP offset is +PAPP_FRAME).
PAPP_FRAME            = 24
PAPP_OFF_ARGS         = 0
PAPP_PARAM_OFF_ENTRY  = 386                # caller-relative; first byte of param body
                                            # (PDU=sp+382, pkt_type=sp+383,
                                            # param_length BE=sp+384..385)
PAPP_PARAM_OFF        = PAPP_PARAM_OFF_ENTRY + PAPP_FRAME

# AVRCP 1.3 §5.2 PlayerApplicationSettings:
#   §5.2.1 attribute IDs (Tbl 5.18):
#     0x01 Equalizer ON/OFF
#     0x02 Repeat Mode Status
#     0x03 Shuffle ON/OFF
#     0x04 Scan ON/OFF
#   §5.2.4 Repeat-mode values (Tbl 5.20):
#     0x01 OFF, 0x02 SINGLE TRACK, 0x03 ALL TRACK, 0x04 GROUP
#   §5.2.4 Shuffle values (Tbl 5.21):
#     0x01 OFF, 0x02 ALL TRACK, 0x03 GROUP
# We expose Repeat (id=2) + Shuffle (id=3) — the universal pair (Equalizer
# and Scan are out of scope on Y1 hardware).
PAPP_ATTR_REPEAT      = 0x02
PAPP_ATTR_SHUFFLE     = 0x03
PAPP_REPEAT_OFF       = 0x01
PAPP_SHUFFLE_OFF      = 0x01


# AVRCP 1.3 §5.4.2 (RegisterNotification, Tables 5.34 + 5.36) canned-value
# defaults.
# - BATT_STATUS_CHANGED: real data wired through y1-track-info[794]
#   (battery_status u8). T8 INTERIM reads byte 794; T9 emits CHANGED-on-edge
#   when file[794] differs from y1-trampoline-state[10] (last_battery_status).
#   The music app's BatteryReceiver maps Android `Intent.ACTION_BATTERY_CHANGED`
#   (level + plugged-state) to the AVRCP enum on every bucket transition and
#   fires `playstatechanged` so T9 picks up the change. Spec values:
#   0=NORMAL, 1=WARNING, 2=CRITICAL, 3=EXTERNAL, 4=FULL_CHARGE.
#   BATT_STATUS_NORMAL is retained as the default value when y1-track-info
#   is shorter than 800 B — T8 / T9 memset to zero before the read, so a
#   short read leaves byte 794 = 0 = NORMAL, a benign default.
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
NR_lseek = 19
NR_clock_gettime = 263
SEEK_SET = 0

# Linux clock IDs. CLOCK_BOOTTIME mirrors Android's SystemClock.elapsedRealtime
# (monotonic, includes time spent in suspend) — same source the music app's
# TrackInfoWriter uses when stamping mStateChangeTime, so subtracting the two
# yields the wall-clock seconds elapsed since the last play / pause edge.
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
    # PDU 0x17 → InformDisplayableCharacterSet (T_charset)
    # PDU 0x18 → InformBatteryStatusOfCT (T_battery)
    # PDU 0x30 → GetPlayStatus (T6)
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
    # PDU 0x40 RequestContinuingResponse / 0x41 AbortContinuingResponse.
    # Routed through an explicit T_continuation handler that emits the spec-
    # acceptable NOT_IMPLEMENTED reject via the same UNKNOW_INDICATION path.
    # See _emit_t_continuation for rationale.
    a.cmp_imm8(0, 0x40)
    a.bne("t4_after_continuation_40")
    a.b_w("T_continuation")
    a.label("t4_after_continuation_40")
    a.cmp_imm8(0, 0x41)
    a.bne("t4_after_continuation_41")
    a.b_w("T_continuation")
    a.label("t4_after_continuation_41")
    # PDUs 0x11..0x16 (PlayerApplicationSettings) all route through T_papp.
    # Per AVRCP 1.3 ICS Table 7 C.14, supporting any one PApp PDU makes all
    # of 0x11..0x16 + event 0x08 Mandatory — handled together in T_papp.
    a.cmp_imm8(0, 0x11)
    a.blt("t4_after_papp")
    a.cmp_imm8(0, 0x17)                       # >= 0x17: not in our range
    a.bge("t4_after_papp")
    a.b_w("T_papp")
    a.label("t4_after_papp")
    # Anything else: restore lr canary and fall through to original
    # "unknow indication" path (which expects r0 = conn).
    a.ldrh_w(14, 13, T4_LR_CANARY_OFF_ENTRY)  # ldrh.w lr, [sp, #374]
    a.add_imm_t3(0, 5, 8)                     # add.w r0, r5, #8 (= conn)
    a.b_w("t4_to_unknown")

    a.label("t4_main")
    # ---- allocate stack frame ----
    a.subw(13, 13, T4_FRAME)                  # sub.w sp, sp, #1136

    # ---- zero-init state buffer (16 B) ----
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, T4_OFF_STATE + 0)
    a.str_sp_imm(0, T4_OFF_STATE + 4)
    a.str_sp_imm(0, T4_OFF_STATE + 8)
    a.str_sp_imm(0, T4_OFF_STATE + 12)

    # ---- memset(file_buf, 0, FILE_SIZE) ----
    a.add_sp_imm(0, T4_OFF_FILE)              # r0 = sp+32
    a.movs_imm8(1, 0)                         # r1 = 0
    a.movw(2, T4_FILE_SIZE)                   # r2 = 1104
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
    a.movw(2, T4_FILE_SIZE)                   # r2 = count
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
    # r1=0 takes the response builder's spec-correct path; r1!=0 hits the
    # reject-shape path that omits the event payload (see extended_T2's
    # matching comment). The track_id is the 0xFF×8 sentinel rather than
    # a real synthetic id — see the wire-level track_id discussion in the
    # module docstring for why.
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
    # pre-created by TrackInfoWriter.prepareFiles() in the music app. If it's
    # somehow gone, we silently skip the write rather than create a
    # wrongly-permissioned file.
    a.adr_w(0, "path_state")
    # No O_TRUNC: file is 20 B (T8 owns subscription bytes at offset 13..19);
    # truncating would clobber them and the per-event subscription gates would
    # all reset to "not subscribed" on every track edge, breaking AVRCP §6.7.1
    # semantics. T4 writes the first 16 bytes (its read scope); bytes 16..19
    # stay untouched on disk.
    a.movw(1, O_WRONLY)
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

    # ---- N× get_element_attributes_rsp(conn, 0, idx, total,
    #                                    [attr_id, charset=0x6a, len, ptr]) ----
    # Per disassembly of the rsp function (libextavrcp.so:0x2188):
    #   arg2 = attribute INDEX in the response (0..N-1)
    #   arg3 = TOTAL number of attributes
    #   EMIT trigger: (arg2+1 == arg3) AND (arg3 != 0)
    # transId is read by the function itself from conn[17]; we don't pass it.
    # We pack all emitted attributes into a single msg=540 frame by accumulating
    # with arg3=N and emitting on the final call.
    #
    # AVRCP 1.3 §6.6.1 Table 6.26 mandates the response shape:
    #   "If NumAttributes is set to zero, all attribute information shall be
    #    returned, else attribute information for the specified attribute IDs
    #    shall be returned by the TG."
    # Together with §5.3.4 ("For attributes not supported by the TG, this
    # field shall be sent with 0 length data"), this means T4 must:
    #   - Read the CT's inbound NumAttributes byte (caller_sp + 394).
    #   - If N == 0: emit all 7 supported attrs (1..7) in canonical order.
    #   - If N > 0: emit each requested AttributeID in the CT-specified order;
    #     for any IDs outside {0x01..0x07} (= our supported set), emit with
    #     AttributeValueLength = 0. Per-attr-id offset lookup via the inline
    #     `t4_attr_offset_table` data block below.
    # The empty-attribute emit relies on patch_libextavrcp.py E1 landing —
    # without it, libextavrcp.so's response builder drops zero-length attrs.
    #
    # AVRCP 1.3 §5.3.4 attribute IDs:
    #   0x01 Title              0x05 TotalNumberOfTracks
    #   0x02 Artist             0x06 Genre
    #   0x03 Album              0x07 PlayingTime (ms, ASCII decimal)
    #   0x04 TrackNumber
    # All values are UTF-8 (charset 0x006A).

    # ---- read NumAttributes from inbound request ----
    a.ldrb_w(7, 13, T4_NUMATTR_OFF)           # r7 = N (CT-requested count)
    a.cmp_imm8(7, 0)
    a.beq_w("t4_emit_all")                    # N==0 -> §6.6.1 "return all"

    # ---- Phase 1: request-driven emit loop ----
    # Register conventions in this loop:
    #   r5  = JNI base (preserved by caller's stmdb prologue, untouched here)
    #   r6  = i  (loop counter; preserved across strlen/rsp calls — low reg
    #             happens to be caller-saved, but we re-emit it through the
    #             call args anyway, so no extra save needed)
    #   r7  = N  (loop bound; ditto)
    #   r9  = attr_id (saved across strlen/rsp; r9 is callee-saved per AAPCS)
    #   r10 = str_offset (saved across strlen/rsp; r10 callee-saved)
    a.movs_imm8(6, 0)                         # r6 = i = 0

    a.label("t4_req_loop")
    # Compute pointer to AttributeID[i]: r4 = sp + T4_ATTRIDS_OFF + 4*i
    a.addw(4, 13, T4_ATTRIDS_OFF)             # r4 = sp + T4_ATTRIDS_OFF
    a.mov_lo_lo(0, 6)                         # r0 = i
    a.lsls_imm5(0, 0, 2)                      # r0 = i * 4
    a.add_reg(4, 0)                           # r4 += i*4 (now r4 = &AttrIDs[i])

    # Load BE u32 attr_id, byte-reverse to LE for compare.
    a.ldr_w(0, 4, 0)                          # r0 = BE u32 attr_id
    a.rev_lo_lo(0, 0)                         # r0 = LE attr_id

    # Save attr_id to r9 (preserved across strlen + rsp calls).
    a.mov_lo_lo(9, 0)

    # If attr_id is 0 or >= 8: unsupported. AVRCP 1.3 §26 Table 26.1 marks 0
    # as "Not Used" and 0x8-0xFFFFFFFF as Reserved.
    a.cmp_imm8(0, 0)
    a.beq("t4_req_unsup")
    a.cmp_imm8(0, 8)
    a.bhs("t4_req_unsup")                     # attr_id >= 8 → unsupported

    # Look up table[attr_id] → r4 (= sp-relative file_buf offset).
    a.adr_w(4, "t4_attr_offset_table")        # r4 = table base
    a.lsls_imm5(0, 0, 2)                      # r0 = attr_id * 4
    a.add_reg(4, 0)                           # r4 = &table[attr_id]
    a.ldr_w(4, 4, 0)                          # r4 = table[attr_id]
    a.b_w("t4_req_have_off")

    a.label("t4_req_unsup")
    # Unsupported attr: emit with length=0. r4 = 0 → sp+0 = T4_OFF_ARGS region,
    # which we initialize each iteration via the str writes below; the args
    # region's leading byte is whatever we wrote last (overwritten before
    # strlen call sees it), so use a pre-known-zero sentinel.
    # Simpler: just emit length=0 directly. Use 0 as ptr (the response
    # builder won't deref past length, but we set it for shape).
    a.movs_imm8(4, 0)                         # r4 = 0 sentinel

    a.label("t4_req_have_off")
    # Save str_offset to r10 (preserved across strlen + rsp calls).
    a.mov_lo_lo(10, 4)

    # strlen(sp + str_offset). For unsupported (str_offset = 0), this points
    # at the args region. Its current contents are whatever we set last —
    # but since the FIRST byte at sp+0 is the previous iteration's attr_id
    # write (a non-zero value 1..7) or the initial pre-loop zero-fill, the
    # strlen result is unpredictable. Override: short-circuit unsupported
    # by setting r0 to 0 directly.
    a.cmp_imm8(4, 0)                          # str_offset == 0?
    a.beq("t4_req_skip_strlen")
    a.mov_lo_lo(0, 13)                        # r0 = sp
    a.add_reg(0, 4)                           # r0 = sp + str_offset
    a.blx_imm(PLT_strlen)                     # r0 = strlen
    a.b_w("t4_req_have_strlen")

    a.label("t4_req_skip_strlen")
    a.movs_imm8(0, 0)                         # r0 = 0 (no value to measure)

    a.label("t4_req_have_strlen")
    # Pack response args: sp[0]=attr_id, sp[4]=charset, sp[8]=strlen, sp[12]=ptr
    a.str_sp_imm(0, T4_OFF_ARGS + 8)          # sp[8]  = strlen

    a.mov_lo_lo(0, 9)                         # r0 = attr_id
    a.str_sp_imm(0, T4_OFF_ARGS + 0)          # sp[0]  = attr_id

    a.movs_imm8(0, 0x6A)
    a.str_sp_imm(0, T4_OFF_ARGS + 4)          # sp[4]  = charset (UTF-8)

    a.mov_lo_lo(0, 13)                        # r0 = sp
    a.add_reg(0, 10)                          # r0 = sp + str_offset
    a.str_sp_imm(0, T4_OFF_ARGS + 12)         # sp[12] = ptr

    # Call get_element_attributes_rsp(conn, 0, i, N).
    a.add_imm_t3(0, 5, 8)                     # r0 = conn (= r5+8)
    a.movs_imm8(1, 0)                         # r1 = 0
    a.mov_lo_lo(2, 6)                         # r2 = i
    a.mov_lo_lo(3, 7)                         # r3 = N
    a.blx_imm(PLT_get_element_attributes_rsp)

    # i++; if i < N: loop.
    a.add_imm_t3(6, 6, 1)
    a.cmp_w(6, 7)
    a.blt_w("t4_req_loop")
    a.b_w("t4_req_done")

    # ---- N==0 fallback: emit all 7 supported attrs per §6.6.1 ----
    a.label("t4_emit_all")
    attr_table = (
        ("title",       0x01, T4_OFF_FILE_TITLE),
        ("artist",      0x02, T4_OFF_FILE_ARTIST),
        ("album",       0x03, T4_OFF_FILE_ALBUM),
        ("track_num",   0x04, T4_OFF_FILE_TRACK_NUM),
        ("total_num",   0x05, T4_OFF_FILE_TOTAL_NUM),
        ("genre",       0x06, T4_OFF_FILE_GENRE),
        ("play_time",   0x07, T4_OFF_FILE_PLAY_TIME),
    )
    total_attrs = len(attr_table)
    for idx, (label_suffix, attr_id, str_offset) in enumerate(attr_table):
        a.label(f"t4_reply_{label_suffix}")
        a.add_sp_imm(0, str_offset)           # r0 = sp + str_offset
        a.blx_imm(PLT_strlen)                 # r0 = strlen
        a.mov_lo_lo(6, 0)                     # r6 = strlen

        a.add_imm_t3(0, 5, 8)                 # r0 = conn
        a.movs_imm8(1, 0)
        a.movs_imm8(2, idx)
        a.movs_imm8(3, total_attrs)
        a.movs_imm8(4, attr_id)
        a.str_sp_imm(4, T4_OFF_ARGS + 0)      # sp[0]  = attr_id
        a.movs_imm8(4, 0x6A)
        a.str_sp_imm(4, T4_OFF_ARGS + 4)      # sp[4]  = charset
        a.str_sp_imm(6, T4_OFF_ARGS + 8)      # sp[8]  = strlen
        a.add_sp_imm(4, str_offset)
        a.str_sp_imm(4, T4_OFF_ARGS + 12)     # sp[12] = ptr
        a.blx_imm(PLT_get_element_attributes_rsp)

    # ---- restore stack and tail-call the function epilogue ----
    a.label("t4_req_done")
    a.addw(13, 13, T4_FRAME)
    a.ldrh_w(14, 13, T4_LR_CANARY_OFF_ENTRY)
    a.b_w("t4_to_epilogue")

    # ---- Inline data: attr_id → file_buf-relative offset lookup ----
    # Indexed by AVRCP 1.3 §26 Table 26.1 attribute ID (1..7).
    # Index 0 is unused (attr_id 0 = "Not Used"; bounds check above redirects
    # to the unsupported path before reaching this table).
    a.align(4)
    a.label("t4_attr_offset_table")
    a._word(0)                                # attr_id 0 (Not Used)
    a._word(T4_OFF_FILE_TITLE)                # attr_id 1
    a._word(T4_OFF_FILE_ARTIST)               # attr_id 2
    a._word(T4_OFF_FILE_ALBUM)                # attr_id 3
    a._word(T4_OFF_FILE_TRACK_NUM)            # attr_id 4
    a._word(T4_OFF_FILE_TOTAL_NUM)            # attr_id 5
    a._word(T4_OFF_FILE_GENRE)                # attr_id 6
    a._word(T4_OFF_FILE_PLAY_TIME)            # attr_id 7


def _emit_extended_t2(a: Asm) -> None:
    """extended_T2: RegisterNotification(TRACK_CHANGED) handler.

    T2 stub at 0x72d4 jumps here unconditionally (b.w extended_T2). We dispatch
    PDU / event-id internally and fall through to T4 if it's a GetElementAttributes
    that somehow reached us, or to UNKNOW_INDICATION otherwise.
    """
    a.label("extended_T2")

    # r0 contains PDU at entry (set by T1's bridge, which loads PDU and dispatches)
    a.cmp_imm8(0, 0x31)
    a.bne("ext2_check_get_attrs")             # not RegisterNotification → maybe T4

    a.ldrb_w(0, 13, T2_EVENT_ID_OFF_ENTRY)    # r0 = event_id
    a.cmp_imm8(0, 0x02)                       # TRACK_CHANGED?
    a.beq("ext2_track_changed")

    # PDU 0x31 but event ≠ 0x02 → T8 handles events 0x01/0x03/0x04/0x05/
    # 0x06/0x07. T8 returns NOT_IMPLEMENTED for any other event_id.
    a.b_w("T8")

    a.label("ext2_check_get_attrs")
    # PDU != 0x31. If it's 0x20 (GetElementAttributes), let T4 handle it; the
    # T4 entry re-reads PDU from sp+382 so it doesn't matter that r0 is stale.
    a.b_w("T4")

    a.label("ext2_track_changed")
    # ---- allocate small frame: stack scratch for state-file write ----
    # sp+0..7  : track_id (read from y1-track-info)
    # sp+8     : transId (caller-supplied)
    # sp+9..15 : unused (we lseek+write only bytes 0..8 — see below)
    a.subw(13, 13, T2_FRAME)                  # sub.w sp, sp, #16

    # Default sp+0..7 to zero (defensive — track-info read might fail).
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, T2_OFF_TID + 0)
    a.str_sp_imm(0, T2_OFF_TID + 4)

    # Open + read 8 B from y1-track-info into sp+0..7.
    a.adr_w(0, "path_track_info")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("ext2_after_track_read")
    a.mov_lo_lo(4, 0)

    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, T2_OFF_TID)               # r1 = sp+0 (track_id slot)
    a.movs_imm8(2, 8)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    a.label("ext2_after_track_read")

    # ---- store caller's transId at sp+8 ----
    a.ldrb_w(0, 13, T2_TRANSID_CALLER_OFF)
    a.strb_w(0, 13, T2_OFF_TRANSID)

    # open(path_state, O_WRONLY, 0) — no O_TRUNC, no O_CREAT. The music app's
    # TrackInfoWriter.prepareFiles() pre-creates it. We open without truncating because we only
    # write OUR 9 bytes (track_id 0..7 + transId at 8); T9's bytes 9..12 must
    # stay intact. With O_TRUNC we'd zero them and cause spurious CHANGED
    # frames on the next T9 fire.
    a.adr_w(0, "path_state")
    a.movw(1, O_WRONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("ext2_after_state_write")
    a.mov_lo_lo(4, 0)

    # write 9 B from sp+0..8 (track_id + transId). No lseek needed since
    # the fd's offset starts at 0 after open; we write at the head of the
    # file and stop after 9 bytes. T9's bytes 9..12 are untouched.
    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, T2_OFF_TID)               # source = sp+0
    a.movs_imm8(2, 9)                         # 9 bytes (0..8 inclusive)
    a.blx_imm(PLT_write)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    a.label("ext2_after_state_write")

    # ---- reply track_changed_rsp INTERIM ----
    # r1=0 takes the spec-correct path. Disassembly of the response builder
    # at libextavrcp.so:0x2458 shows `cbnz r5, reject_path` on r1; r1==0 is
    # the spec-correct path that emits reasonCode + event_id + track_id;
    # r1!=0 writes a reject-shape frame that omits the event payload.
    # transId is auto-extracted from conn[17] regardless.
    # See module docstring for why we use the 0xFF×8 sentinel here rather
    # than a real synthetic track_id.
    a.add_imm_t3(0, 5, 8)                     # r0 = conn
    a.movs_imm8(1, 0)                         # r1 = 0 (success)
    a.movs_imm8(2, REASON_INTERIM)
    a.adr_w(3, "sentinel_ffx8")               # r3 = &(8 bytes 0xFF) — see top-of-file
    a.blx_imm(PLT_track_changed_rsp)

    # Arm sub_track_changed (event 0x02) per AVRCP §6.7.1. T5 emits CHANGED
    # for events 0x02 / 0x03 / 0x04 on track edges; we gate each separately
    # so strict CTs that subscribe to event 0x02 alone get exactly one
    # INTERIM + one CHANGED per registration.
    _emit_subscription_write(a, 1, 16, T2_OFF_SUB_SCRATCH, "ext2_epilogue")

    a.label("ext2_epilogue")
    # Restore stack and branch to epilogue.
    a.addw(13, 13, T2_FRAME)
    a.b_w("t4_to_epilogue")


def _emit_t5(a: Asm) -> None:
    """T5: proactive CHANGED-emit trampoline.

    Entered via `b.w T5` from the patched libextavrcp_jni.so::
    notificationTrackChangedNative stub at file offset 0x3bc0. Java's
    handleKeyMessage path (with the cardinality if-eqz NOPed in MtkBt.odex)
    invokes the native method on every track-change broadcast from the music
    app — this lands here, asynchronously to any inbound AVRCP command from
    permissive CTs.

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
      2. Read 800 B of y1-track-info into file_buf @ sp+16..815. file[0..7]
         is the current track_id; file[793] is the
         `previous_track_natural_end` flag set by the music app before the
         metachanged broadcast that landed us here.
      3. Read y1-trampoline-state 16 bytes into state_buf @ sp+0..15
         (state[0..7] = last-synced track_id; state[8] = last
         RegisterNotification transId).
      4. If state[0..7] != file[0..7] (track edge detected), emit the
         AVRCP 1.3 §5.4.2 track-edge 3-tuple in spec-defined order:
           - event 0x03 TRACK_REACHED_END (Table 5.31) — only when
             file[793] == 1 (previous track ended naturally rather than
             being skipped). Strict spec semantic: §5.4.2 Tbl 5.31 is
             "Notify when reached the end of the track of the playing
             element" — natural-end-only, not skip-driven.
           - event 0x02 TRACK_CHANGED (Table 5.30) — always on edge,
             with track_id=&sentinel_ffx8 per the wire-level design
             choice in the module docstring.
           - event 0x04 TRACK_REACHED_START (Table 5.32) — always on
             edge ("Notify when start of a track is reached"; every
             track edge crosses both an end-of-previous and a
             start-of-new boundary).
         Then write file[0..7] back to state[0..7] in y1-trampoline-state
         so we don't re-emit until the track moves again.

    Closes ICS Table 7 rows 25 (TRACK_REACHED_END) and 26
    (TRACK_REACHED_START) with real-data CHANGED-on-edge alongside T8's
    INTERIM coverage — both Optional rows. Pairs with the music app's
    natural-end detection in PlaybackStateBridge.onCompletion(): the music
    app marks the previous track's completion via TrackInfoWriter and writes
    file[793] before the metachanged broadcast.

    The 16-byte state buf and 800-byte file buf live in T5's own stack
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

    # ---- allocate locals: 16 B state buf @ sp+0..15 + 800 B file buf @ sp+16..815 ----
    a.subw(13, 13, T5_FRAME)                  # sub.w sp, sp, #816

    # ---- memset(file_buf, 0, 800) ----
    # Default everything to 0 so a partial read (file shorter than 800 B —
    # e.g. an older writer where file[793] is just a zero pad byte) gives
    # natural_end=0, which means T5 only emits 0x02 + 0x04 (no spurious 0x03
    # emission). Same shape T9 uses for safe defaults.
    a.add_sp_imm(0, T5_OFF_FILE)              # r0 = sp+16
    a.movs_imm8(1, 0)
    a.movw(2, 800)
    a.blx_imm(PLT_memset)

    # ---- open + read 800 B of y1-track-info into file_buf ----
    a.adr_w(0, "path_track_info")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t5_skip_track_read")
    a.mov_lo_lo(5, 0)                         # r5 = fd

    a.mov_lo_lo(0, 5)
    a.add_sp_imm(1, T5_OFF_FILE)              # r1 = file_buf
    a.movw(2, 800)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t5_skip_track_read")

    # ---- read y1-trampoline-state 20 bytes into state buf (sp+0..19) ----
    # Default 0×20 (state bytes 16..19 hold subscription gates for events
    # 0x02/0x03/0x04/0x06; zero-fill = "not subscribed").
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, T5_OFF_STATE + 0)
    a.str_sp_imm(0, T5_OFF_STATE + 4)
    a.str_sp_imm(0, T5_OFF_STATE + 8)
    a.str_sp_imm(0, T5_OFF_STATE + 12)
    a.str_sp_imm(0, T5_OFF_STATE + 16)

    a.adr_w(0, "path_state")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t5_skip_state_read")
    a.mov_lo_lo(5, 0)

    a.mov_lo_lo(0, 5)
    a.add_sp_imm(1, T5_OFF_STATE)             # r1 = state buf
    a.movs_imm8(2, 20)                        # 20 B: 16 legacy + 4 sub_* bytes
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t5_skip_state_read")

    # ---- compare state[0..7] vs file[0..7] ----
    a.ldr_sp_imm(0, T5_OFF_STATE + 0)         # state[0..3]
    a.ldr_sp_imm(1, T5_OFF_FILE_TID + 0)      # file[0..3]
    a.cmp_w(0, 1)
    a.bne("t5_changed")
    a.ldr_sp_imm(0, T5_OFF_STATE + 4)         # state[4..7]
    a.ldr_sp_imm(1, T5_OFF_FILE_TID + 4)      # file[4..7]
    a.cmp_w(0, 1)
    a.beq_w("t5_no_change")                   # wide-form: extended T5 body
                                              #   exceeds 254 B branch range

    a.label("t5_changed")

    # ---- emit TRACK_REACHED_END (event 0x03) only if natural AND subscribed ----
    # AVRCP 1.3 §5.4.2 Table 5.31. ICS Table 7 row 25 (Optional). Two gates:
    #   1. previous-track-natural-end flag at file[793] (set by music app
    #      before metachanged broadcast)
    #   2. sub_track_reached_end bit at state[17] (set by T8 INTERIM emit,
    #      cleared here per §6.7.1)
    a.ldrb_w(0, 13, T5_OFF_FILE_NATURAL_END)
    a.cmp_imm8(0, 0)
    a.beq("t5_skip_reached_end")
    a.ldrb_w(0, 13, T5_OFF_STATE + 17)        # state[17] sub_track_reached_end
    a.cmp_imm8(0, 0)
    a.beq("t5_skip_reached_end")

    # reg_notievent_reached_end_rsp(conn, 0, REASON_CHANGED)
    a.add_imm_t3(0, 4, 8)                     # r0 = r4 + 8 (conn)
    a.movs_imm8(1, 0)                         # r1 = 0 (success)
    a.movs_imm8(2, REASON_CHANGED)
    a.blx_imm(PLT_reg_notievent_reached_end_rsp)

    # Clear sub_track_reached_end.
    # Scratch == target offset: we're clearing the byte we're writing to,
    # so the 1-B stack scratch can land on the same byte we read 2
    # instructions ago (r0 already holds the read value if needed elsewhere
    # — it isn't, here).
    _emit_subscription_write(a, 0, 17, T5_OFF_STATE + 17, "t5_skip_reached_end")

    a.label("t5_skip_reached_end")

    # ---- emit TRACK_CHANGED (event 0x02) — gated on subscription ----
    # AVRCP 1.3 §5.4.2 Table 5.30. ICS Table 7 row 24 (Mandatory wire-level).
    # See module docstring for r1=0 / sentinel_ffx8 design rationale.
    # sub_track_changed bit at state[16].
    a.ldrb_w(0, 13, T5_OFF_STATE + 16)
    a.cmp_imm8(0, 0)
    a.beq("t5_skip_track_changed")

    a.add_imm_t3(0, 4, 8)                     # r0 = r4 + 8 (conn)
    a.movs_imm8(1, 0)                         # r1 = 0 (success)
    a.movs_imm8(2, REASON_CHANGED)
    a.adr_w(3, "sentinel_ffx8")
    a.blx_imm(PLT_track_changed_rsp)

    # Clear sub_track_changed.
    _emit_subscription_write(a, 0, 16, T5_OFF_STATE + 16, "t5_skip_track_changed")

    a.label("t5_skip_track_changed")

    # ---- emit TRACK_REACHED_START (event 0x04) — gated on subscription ----
    # AVRCP 1.3 §5.4.2 Table 5.32. ICS Table 7 row 26 (Optional).
    # sub_track_reached_start bit at state[18].
    a.ldrb_w(0, 13, T5_OFF_STATE + 18)
    a.cmp_imm8(0, 0)
    a.beq("t5_skip_reached_start")

    a.add_imm_t3(0, 4, 8)                     # r0 = r4 + 8 (conn)
    a.movs_imm8(1, 0)                         # r1 = 0 (success)
    a.movs_imm8(2, REASON_CHANGED)
    a.blx_imm(PLT_reg_notievent_reached_start_rsp)

    # Clear sub_track_reached_start.
    _emit_subscription_write(a, 0, 18, T5_OFF_STATE + 18, "t5_skip_reached_start")

    a.label("t5_skip_reached_start")

    # ---- update state in-memory: state[0..7] = file[0..7] ----
    a.ldr_sp_imm(0, T5_OFF_FILE_TID + 0)
    a.str_sp_imm(0, T5_OFF_STATE + 0)
    a.ldr_sp_imm(0, T5_OFF_FILE_TID + 4)
    a.str_sp_imm(0, T5_OFF_STATE + 4)

    # ---- write only T5's bytes (0..7 track_id + 8 transId = 9 B) ----
    # No O_TRUNC: T9 owns bytes 9..12 (last_play / last_battery / last_repeat
    # / last_shuffle). Truncating would clobber T9's edge-tracking state and
    # cause spurious CHANGED emits on the next play_state edge. Without
    # O_TRUNC, the existing 16 B file shape is preserved and we overwrite
    # only the leading 9 bytes.
    a.adr_w(0, "path_state")
    a.movw(1, O_WRONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt_w("t5_no_change")                   # open failed → skip write, still return success
    a.mov_lo_lo(5, 0)

    a.mov_lo_lo(0, 5)
    a.add_sp_imm(1, T5_OFF_STATE)             # r1 = state buf
    a.movs_imm8(2, 9)                         # 9 bytes: track_id (8) + transId (1)
    a.blx_imm(PLT_write)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t5_no_change")
    # ---- epilogue: return jboolean true ----
    a.movs_imm8(0, 1)
    a.addw(13, 13, T5_FRAME)
    # pop {r4, r5, pc} — Thumb T1 pop: 0xBC00 | (PC<<8) | regs[r0..r7]
    # PC bit is bit 8.  pop {r4, r5, pc} = 0xBD30.
    a.raw(bytes([0x30, 0xBD]))


def _emit_t_charset(a: Asm) -> None:
    """T_charset: PDU 0x17 InformDisplayableCharacterSet.

    Branched from T4's pre-check when the inbound PDU byte is 0x17. The CT is
    declaring its accepted charsets to us; we ack with success and continue
    sending UTF-8 (which we already do — there's no spec requirement that we
    actually honor the CT's charset preference, just that we ack the
    declaration).

    Response builder layout (libextavrcp.so:0x2138):
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
    """T_battery: PDU 0x18 InformBatteryStatusOfCT.

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


def _emit_t_continuation(a: Asm) -> None:
    """T_continuation: PDU 0x40 RequestContinuingResponse /
    0x41 AbortContinuingResponse explicit reject.

    Per AVRCP 1.3 §4.7.7 / §5.5 + ICS Table 7 rows 31-32 (M C.2: M IF
    GetElementAttributes Response). Continuation is initiated by TG
    setting `Packet Type=01` (start) in a response — the CT only sends
    0x40 in reply to a previously-fragmented response. T4 ships responses
    as a single non-fragmented AVRCP packet (mtkbt fragments below at
    the AVCTP layer transparently), so a spec-conforming CT never sends
    0x40 against us.

    Reject shape: AVRCP 1.3 §6.15.2 specifies AV/C `INVALID PARAMETER`
    (status 0x05) as the spec-correct response when receiving 0x40
    without having previously sent packet_type=01. The pre-existing
    UNKNOW_INDICATION path emits AV/C `NOT_IMPLEMENTED` (msg=520) — a
    different but spec-acceptable reject for an unsupported PDU,
    functionally indistinguishable to the CT (both are reject frames;
    the CT abandons continuation either way).

    Branched from T4's pre-check when PDU == 0x40 or 0x41. Restores the
    same lr canary + r0=conn entry state UNKNOW_INDICATION expects, then
    tail-jumps.
    """
    a.label("T_continuation")
    # Restore lr canary + r0 to match UNKNOW_INDICATION's expected entry state.
    a.ldrh_w(14, 13, T4_LR_CANARY_OFF_ENTRY)  # ldrh.w lr, [sp, #374]
    a.add_imm_t3(0, 5, 8)                     # add.w r0, r5, #8 (= conn)
    a.b_w("t4_to_unknown")


def _emit_t6(a: Asm) -> None:
    """T6: PDU 0x30 GetPlayStatus.

    Branched from T4's pre-check when the inbound PDU byte is 0x30. Returns
    the current track's duration / playback position / play_status in a
    spec-conformant `GetPlayStatus` response per AVRCP 1.3 §5.4.1
    (Tables 5.25/5.26).

    Response builder layout (libextavrcp.so:0x2354):
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
    (track_id + title / artist / album) stay intact for any concurrent reader,
    even though T6 itself only consumes the GetPlayStatus block at
    offsets 776+.

    Live position extrapolation: when playing_flag == 1 (PLAYING), T6
    calls clock_gettime(CLOCK_BOOTTIME, &timespec) and computes
    `live_pos = saved_pos_ms + (now_ms - state_change_ms)`. now_ms =
    tv_sec * 1000 + tv_nsec / 1e6, computed in-trampoline. When stopped
    / paused the position field stays at the saved freeze point.
    CLOCK_BOOTTIME parity with TrackInfoWriter's SystemClock.elapsedRealtime
    is what makes this arithmetic correct, and full ms precision on both
    endpoints means the wire position is bit-exact, no ±1 s lurch on
    state edges.

    tv_nsec / 1_000_000 is computed via magic-multiply: the high half of
    (tv_nsec * 0x431BDE83) right-shifted 18 yields floor(tv_nsec / 1e6)
    bit-exact for tv_nsec in [0, 1e9). Standard GCC reciprocal — see
    Hacker's Delight ch.10 for the derivation.

    The y1-track-info schema fields T6 reads (the music app's TrackInfoWriter
    writes these as big-endian; T6 byte-swaps to host-LE via REV before passing to the
    response builder, which expects register-native order):
      file[776..779]: duration_ms u32 BE
      file[780..783]: position_at_state_change_ms u32 BE
      file[784..787]: state_change_time_ms u32 BE
      file[792]:      playing_flag u8 (0=stopped / 1=playing / 2=paused;
                                       maps directly to AVRCP play_status)
    """
    a.label("T6")

    # ---- allocate stack frame ----
    a.subw(13, 13, T6_FRAME)                  # sub.w sp, sp, #816

    # ---- memset(file_buf, 0, 800) ----
    # Default everything to 0 so a partial read (file shorter than 800 B,
    # e.g. an older writer that hasn't been rebuilt for the current schema)
    # gives play_status=0 (STOPPED) and duration / position = 0 rather than
    # uninitialized stack garbage.
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

    # Live position extrapolation.
    # If playing_flag == 1 (PLAYING):
    #   live_pos = saved_pos_ms + (now_ms - state_change_ms)
    #   now_ms   = tv_sec * 1000 + tv_nsec / 1e6
    # Else (STOPPED / PAUSED):
    #   live_pos = saved_pos  (the position field IS the freeze point for
    #                          paused / stopped, which is what CTs expect)
    # AVRCP 1.3 §5.4.1 Table 5.26 specifies SongPosition as "the current
    # position of the playing in milliseconds elapsed". A static position
    # that doesn't advance during playback violates that semantic — CTs
    # that visualize playback progress expect the value to advance with
    # playback, and some interpret a stuck-across-polls position as "no
    # position info" and hide the playback-progress display.
    a.cmp_imm8(0, 1)                          # r0 still = playing_flag
    a.bne("t6_position_static")

    # ---- clock_gettime(CLOCK_BOOTTIME, &timespec) ----
    # Default the timespec to zero so a syscall failure (extremely unlikely
    # — clock_gettime can't really fail with valid args) yields a bounded
    # fallback: now_ms collapses to 0, delta_ms wraps to (-state_change_ms)
    # mod 2^32, position lurches once. Better than uninit garbage.
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, T6_OFF_TIMESPEC_SEC)
    a.str_sp_imm(0, T6_OFF_TIMESPEC_NSEC)

    a.movs_imm8(0, CLOCK_BOOTTIME)            # r0 = clk_id = 7
    a.add_sp_imm(1, T6_OFF_TIMESPEC)          # r1 = &timespec
    a.movw(7, NR_clock_gettime)               # r7 = 263
    a.svc(0)

    # ---- now_ms = tv_sec * 1000 + tv_nsec / 1_000_000 ----
    # tv_nsec/1e6 via magic-multiply: result = (tv_nsec * 0x431BDE83) >> 50,
    # equivalent to taking the high half of the 64-bit product then >>18.
    # 0x431BDE83 is GCC's standard reciprocal for unsigned div-by-1e6 on
    # a u32 input bounded by 1e9 (tv_nsec < 1_000_000_000). Verified
    # bit-exact for the full input range — see trampoline header comment.
    a.ldr_sp_imm(2, T6_OFF_TIMESPEC_SEC)      # r2 = tv_sec
    a.movw(0, 1000)
    a.muls_lo_lo(2, 0)                        # r2 = tv_sec * 1000
    a.ldr_sp_imm(0, T6_OFF_TIMESPEC_NSEC)     # r0 = tv_nsec
    a.movw(1, 0xDE83)
    a.movt(1, 0x431B)                         # r1 = 0x431BDE83 (magic)
    a.umull(4, 3, 0, 1)                       # r3:r4 = tv_nsec * magic; r3 = high half
    a.lsrs_imm5(3, 3, 18)                     # r3 = high >> 18 = tv_nsec / 1e6
    a.adds_lo_lo(2, 2, 3)                     # r2 = now_ms (= tv_sec*1000 + tv_nsec/1e6)

    # ---- delta_ms = now_ms - state_change_ms ----
    # state_change_time field at file[784..787] is now u32 ms-since-boot
    # (was sec-since-boot). u32 modular subtraction; correct under wrap
    # provided both endpoints are in the same domain. u32 ms wraps after
    # ~49.7 days uptime, well past any Y1 reboot cycle.
    a.ldr_sp_imm(0, T6_OFF_FILE_STATE_TIME)   # r0 = state_change_ms (BE)
    a.rev_lo_lo(0, 0)                         # → host order
    a.subs_lo_lo(2, 2, 0)                     # r2 = delta_ms

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


def _emit_t_papp(a: Asm) -> None:
    """T_papp: PlayerApplicationSettings PDUs 0x11..0x16.

    Branched from T4's pre-check when the inbound PDU byte is in [0x11..0x16].
    Per AVRCP 1.3 ICS Table 7 C.14, supporting any single PApp PDU makes the
    full 7-row group (PDUs 0x11..0x16 + event 0x08) Mandatory — they all
    ship together.

    Y1 supports Repeat (id=2, three values OFF/SINGLE/ALL) + Shuffle (id=3,
    two values OFF/ALL_TRACK). Live values come from y1-track-info[795..796]
    written by the music app's PappStateBroadcaster on every
    musicRepeatMode/musicIsShuffle SharedPreferences edge. Set PDU (0x14)
    writes 2 bytes (attr_id, value) to y1-papp-set; the music app's
    PappSetFileObserver picks the write up and applies it via
    SharedPreferencesUtils so settings round-trip to the Android media
    session.

    Inbound AVRCP frame layout (caller's stack, post-T_papp SUB SP shifts
    by PAPP_FRAME):
      sp + 382  PDU
      sp + 383  packet_type
      sp + 384  param_length BE u16
      sp + 386  param body (PDU-specific):
        0x11 ListAttrs        : 0 bytes
        0x12 ListValues       : 1 byte attr_id
        0x13 GetCurrent       : 1 byte n + n attr_ids
        0x14 Set              : 1 byte n + n×{attr_id, value}
        0x15 AttrText         : 1 byte n + n attr_ids
        0x16 ValueText        : 1 byte attr_id + 1 byte n + n value_ids

    Builder calling conventions (see ARCHITECTURE.md "PlayerApplicationSettings
    response builders" + INVESTIGATION.md Trace #17 for the disassembly).
    """
    a.label("T_papp")

    # ---- allocate stack frame for outgoing args ----
    a.subw(13, 13, PAPP_FRAME)

    # ---- dispatch on PDU ----
    a.ldrb_w(0, 13, T4_PDU_OFF_ENTRY + PAPP_FRAME)   # r0 = PDU
    a.cmp_imm8(0, 0x11)
    a.beq("papp_list_attrs")
    a.cmp_imm8(0, 0x12)
    a.beq("papp_list_values")
    a.cmp_imm8(0, 0x13)
    a.beq("papp_get_current")
    a.cmp_imm8(0, 0x14)
    a.beq_w("papp_set")
    a.cmp_imm8(0, 0x15)
    a.beq_w("papp_attr_text")
    # The only remaining PDU in the dispatch range is 0x16; fall through.

    # ---- 0x16 GetPlayerApplicationSettingValueText ----
    # btmtk_avrcp_send_get_player_value_text_value_rsp(
    #     conn, reject, idx, total, attr_id, value_id, charset, length, *str)
    # Accumulator: emits AVRCP_SendMessage on (idx+1==total).
    #
    # Param layout: sp+386 = attr_id (1 B), sp+387 = n (1 B),
    # sp+388..387+n = value_ids.
    #
    # Switch on (attr_id, first value_id) and emit the matching label.
    # We only handle the FIRST requested value (single-emit, idx=0/total=1)
    # — adequate for the CTs in our test matrix; multi-emit AttrText is
    # the spec-compliant extension and could be added if a future CT
    # requires it. Unsupported (attr_id, value_id) pairs jump to
    # papp_done with no emission (AVRCP layer sees no response, peer
    # times out / falls back).
    a.label("papp_value_text")
    a.ldrb_w(6, 13, PAPP_PARAM_OFF + 0)   # r6 = attr_id
    a.ldrb_w(7, 13, PAPP_PARAM_OFF + 2)   # r7 = first requested value_id

    a.cmp_imm8(6, PAPP_ATTR_REPEAT)
    a.beq("papp_vt_repeat")
    a.cmp_imm8(6, PAPP_ATTR_SHUFFLE)
    a.beq("papp_vt_shuffle")
    a.b_w("papp_done")

    a.label("papp_vt_repeat")
    # Repeat values: 0x01 OFF, 0x02 SINGLE, 0x03 ALL.
    a.cmp_imm8(7, 0x01)
    a.beq("papp_vt_emit_off")
    a.cmp_imm8(7, 0x02)
    a.beq("papp_vt_emit_single")
    a.cmp_imm8(7, 0x03)
    a.beq("papp_vt_emit_all")
    a.b_w("papp_done")

    a.label("papp_vt_shuffle")
    # Shuffle values: 0x01 OFF, 0x02 ALL_TRACK.
    a.cmp_imm8(7, 0x01)
    a.beq("papp_vt_emit_off")
    a.cmp_imm8(7, 0x02)
    a.beq("papp_vt_emit_all")
    a.b_w("papp_done")

    # Each emit block builds the 5 stack args + 4 reg args and tail-jumps
    # to papp_done. Common shape:
    #   r0 = conn, r1 = 0 (success), r2 = idx 0, r3 = total 1
    #   sp[0] = attr_id (r6), sp[4] = value_id (r7), sp[8] = charset 0x6A,
    #   sp[12] = length, sp[16] = &str
    a.label("papp_vt_emit_off")
    a.movs_imm8(2, 3)                     # "Off" length
    a.str_sp_imm(2, PAPP_OFF_ARGS + 12)
    a.adr_w(2, "papp_text_off")
    a.str_sp_imm(2, PAPP_OFF_ARGS + 16)
    a.b_w("papp_vt_emit_common")

    a.label("papp_vt_emit_single")
    a.movs_imm8(2, 12)                    # "Single Track" length
    a.str_sp_imm(2, PAPP_OFF_ARGS + 12)
    a.adr_w(2, "papp_text_single")
    a.str_sp_imm(2, PAPP_OFF_ARGS + 16)
    a.b_w("papp_vt_emit_common")

    a.label("papp_vt_emit_all")
    a.movs_imm8(2, 10)                    # "All Tracks" length
    a.str_sp_imm(2, PAPP_OFF_ARGS + 12)
    a.adr_w(2, "papp_text_all")
    a.str_sp_imm(2, PAPP_OFF_ARGS + 16)
    # fall through

    a.label("papp_vt_emit_common")
    a.str_sp_imm(6, PAPP_OFF_ARGS + 0)    # sp[0] = attr_id
    a.str_sp_imm(7, PAPP_OFF_ARGS + 4)    # sp[4] = value_id
    a.movs_imm8(2, 0x6A)
    a.str_sp_imm(2, PAPP_OFF_ARGS + 8)    # sp[8] = charset UTF-8
    a.add_imm_t3(0, 5, 8)                 # r0 = conn
    a.movs_imm8(1, 0)                     # r1 = success
    a.movs_imm8(2, 0)                     # r2 = idx 0
    a.movs_imm8(3, 1)                     # r3 = total 1
    a.blx_imm(PLT_get_player_value_text_rsp)
    a.b_w("papp_done")

    # ---- 0x11 ListPlayerApplicationSettingAttributes ----
    # Returns: [Repeat=0x02, Shuffle=0x03], n=2.
    a.label("papp_list_attrs")
    a.add_imm_t3(0, 5, 8)                 # r0 = conn
    a.movs_imm8(1, 0)                     # r1 = success
    a.movs_imm8(2, 2)                     # r2 = n_attrs
    a.adr_w(3, "papp_attr_ids")           # r3 = &[2, 3]
    a.blx_imm(PLT_list_player_attrs_rsp)
    a.b_w("papp_done")

    # ---- 0x12 ListPlayerApplicationSettingValues ----
    # Inbound: 1 byte attr_id at sp+386. Switch on attr_id:
    #   2 → [1,2,3]   (Repeat: OFF / SINGLE / ALL — Y1 has no GROUP)
    #   3 → [1,2]     (Shuffle: OFF / ALL_TRACK — Y1 has no GROUP)
    #   else → reject
    # Honest advertisement: only the values Y1 can actually honor. Stock
    # advertised the full Tbl 5.20 / 5.21 sets including GROUP, so a CT
    # could Set 0x04 (Repeat GROUP) or 0x03 (Shuffle GROUP); T_papp 0x14
    # ACKed success but the music app's enum mapper rejected → CT-side
    # state diverged from Y1-side state.
    a.label("papp_list_values")
    a.ldrb_w(6, 13, PAPP_PARAM_OFF + 0)   # r6 = attr_id
    a.cmp_imm8(6, PAPP_ATTR_REPEAT)
    a.beq("papp_lv_repeat")
    a.cmp_imm8(6, PAPP_ATTR_SHUFFLE)
    a.beq("papp_lv_shuffle")
    # Unsupported attr_id → reject. arg5 still has to be passed (function
    # loads from stack regardless), so set sp[0] = 0.
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, PAPP_OFF_ARGS + 0)
    a.add_imm_t3(0, 5, 8)
    a.movs_imm8(1, 1)                     # r1 != 0 → reject path
    a.movs_imm8(2, 0)
    a.movs_imm8(3, 0)
    a.blx_imm(PLT_list_player_values_rsp)
    a.b_w("papp_done")

    a.label("papp_lv_repeat")
    a.adr_w(0, "papp_repeat_values")
    a.str_sp_imm(0, PAPP_OFF_ARGS + 0)    # sp[0] = &[1,2,3]
    a.add_imm_t3(0, 5, 8)
    a.movs_imm8(1, 0)                     # success
    a.movs_imm8(2, PAPP_ATTR_REPEAT)
    a.movs_imm8(3, 3)                     # n_values (OFF / SINGLE / ALL)
    a.blx_imm(PLT_list_player_values_rsp)
    a.b_w("papp_done")

    a.label("papp_lv_shuffle")
    a.adr_w(0, "papp_shuffle_values")
    a.str_sp_imm(0, PAPP_OFF_ARGS + 0)    # sp[0] = &[1,2]
    a.add_imm_t3(0, 5, 8)
    a.movs_imm8(1, 0)
    a.movs_imm8(2, PAPP_ATTR_SHUFFLE)
    a.movs_imm8(3, 2)                     # n_values (OFF / ALL_TRACK)
    a.blx_imm(PLT_list_player_values_rsp)
    a.b_w("papp_done")

    # ---- 0x13 GetCurrentPlayerApplicationSettingValue ----
    # Inbound: 1 byte n + n attr_ids. Per AVRCP V13 §6.12, "The TG returns
    # the current value(s) of the player application setting(s) requested by
    # the CT" — strict CTs reject a response whose n field doesn't match the
    # request and close the AVCTP channel. Honor the spec by branching on
    # the inbound n: n==1 → return only the requested attr; otherwise fall
    # through to the existing two-attr response (kept for the n==2 case +
    # permissive CTs that send n==0 to mean "all").
    #
    # Live values: open y1-track-info, lseek to byte 795, read 2 bytes
    # ([repeat_avrcp, shuffle_avrcp]) into the outgoing-args region, pass
    # the live pointer as the values pointer. On I/O failure fall back to
    # the static OFF/OFF table at papp_current_values (n==2) or to the
    # single-byte 0x01 OFF default (n==1).
    a.label("papp_get_current")

    # Honor V13 §6.12 — branch to n==1 handler if inbound n is 1.
    a.ldrb_w(6, 13, PAPP_PARAM_OFF + 0)   # r6 = inbound n (caller's sp+386)
    a.cmp_imm8(6, 1)
    a.beq_w("papp_gc_n1")

    # ---- n != 1: existing two-attr path ----
    # Open y1-track-info
    a.adr_w(0, "path_track_info")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("papp_gc_static_fallback")
    a.mov_lo_lo(4, 0)                     # r4 = fd

    # lseek(fd, 795, SEEK_SET)
    a.mov_lo_lo(0, 4)
    a.movw(1, 795)
    a.movs_imm8(2, SEEK_SET)
    a.movs_imm8(7, NR_lseek)
    a.svc(0)

    # read(fd, sp+8, 2) — sp+8 is in the outgoing-args region (we pass
    # sp+0 as the values pointer; sp+8..9 is unused by the response
    # builder's stack args, so it's safe scratch).
    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, PAPP_OFF_ARGS + 8)
    a.movs_imm8(2, 2)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    # Pass sp+8 as the live values pointer
    a.add_sp_imm(0, PAPP_OFF_ARGS + 8)
    a.str_sp_imm(0, PAPP_OFF_ARGS + 0)
    a.b_w("papp_gc_emit")

    a.label("papp_gc_static_fallback")
    a.adr_w(0, "papp_current_values")
    a.str_sp_imm(0, PAPP_OFF_ARGS + 0)

    a.label("papp_gc_emit")
    a.add_imm_t3(0, 5, 8)
    a.movs_imm8(1, 0)
    a.movs_imm8(2, 2)                     # n_pairs
    a.adr_w(3, "papp_attr_ids")           # &[2, 3]
    a.blx_imm(PLT_get_curplayer_value_rsp)
    a.b_w("papp_done")

    # ---- n == 1: single-attr response ----
    # Outgoing-args layout for this path (PAPP_OFF_ARGS == 0). All buffer
    # base addresses are 4-byte aligned so add_sp_imm can reach them; the
    # 1-byte payloads live at the start of each 4-byte slot:
    #   sp+ 0..3  = stack arg slot for values_ptr (= sp+12)
    #   sp+ 8..11 = attr_ids buffer (sp+8 = requested attr_id byte)
    #   sp+12..15 = values buffer   (sp+12 = picked value byte)
    #   sp+16..19 = read scratch    (sp+16 = repeat byte, sp+17 = shuffle byte)
    a.label("papp_gc_n1")

    # Read inbound attr_id at caller's sp+387; validate as Repeat (0x02) or
    # Shuffle (0x03). Anything else → reject with V13 §6.15.2 status 0x05
    # INVALID_PARAMETER.
    a.ldrb_w(6, 13, PAPP_PARAM_OFF + 1)   # r6 = requested attr_id
    a.cmp_imm8(6, PAPP_ATTR_REPEAT)
    a.beq("papp_gc_n1_open")
    a.cmp_imm8(6, PAPP_ATTR_SHUFFLE)
    a.bne_w("papp_gc_n1_reject")

    a.label("papp_gc_n1_open")
    # Open y1-track-info
    a.adr_w(0, "path_track_info")
    a.movs_imm8(1, O_RDONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("papp_gc_n1_static")
    a.mov_lo_lo(4, 0)                     # r4 = fd

    # lseek(fd, 795, SEEK_SET)
    a.mov_lo_lo(0, 4)
    a.movw(1, 795)
    a.movs_imm8(2, SEEK_SET)
    a.movs_imm8(7, NR_lseek)
    a.svc(0)

    # read(fd, sp+16, 2) — aligned scratch (sp+16 = repeat, sp+17 = shuffle).
    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, PAPP_OFF_ARGS + 16)
    a.movs_imm8(2, 2)
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    # Pick the byte matching the requested attr_id: Repeat at +16, Shuffle at +17.
    a.cmp_imm8(6, PAPP_ATTR_SHUFFLE)
    a.beq("papp_gc_n1_use_shuffle")
    a.ldrb_w(7, 13, PAPP_OFF_ARGS + 16)   # r7 = repeat byte
    a.b_w("papp_gc_n1_emit")

    a.label("papp_gc_n1_use_shuffle")
    a.ldrb_w(7, 13, PAPP_OFF_ARGS + 17)   # r7 = shuffle byte
    a.b_w("papp_gc_n1_emit")

    a.label("papp_gc_n1_static")
    # File I/O failed. PAPP_REPEAT_OFF and PAPP_SHUFFLE_OFF are both 0x01 per
    # V13 Tbl 5.20 / 5.21, so a single OFF default covers either attr_id.
    a.movs_imm8(7, PAPP_REPEAT_OFF)

    a.label("papp_gc_n1_emit")
    # Pack response: attr_ids[0] = r6 at sp+8, values[0] = r7 at sp+12.
    a.strb_w(6, 13, PAPP_OFF_ARGS + 8)
    a.strb_w(7, 13, PAPP_OFF_ARGS + 12)

    # Stack arg sp[0] = values_ptr (= sp + PAPP_OFF_ARGS + 12).
    a.add_sp_imm(0, PAPP_OFF_ARGS + 12)
    a.str_sp_imm(0, PAPP_OFF_ARGS + 0)

    # get_curplayer_value_rsp(conn, 0 success, n=1, &attr_ids).
    a.add_imm_t3(0, 5, 8)                 # r0 = conn
    a.movs_imm8(1, 0)
    a.movs_imm8(2, 1)                     # n_pairs = 1
    a.add_sp_imm(3, PAPP_OFF_ARGS + 8)    # r3 = &attr_ids
    a.blx_imm(PLT_get_curplayer_value_rsp)
    a.b_w("papp_done")

    a.label("papp_gc_n1_reject")
    # V13 §6.15.2 status 0x05 INVALID_PARAMETER. The response builder still
    # requires the n/ids/values triple; pass n=0 with dummy buffers at the
    # aligned slots.
    a.movs_imm8(0, 0)
    a.strb_w(0, 13, PAPP_OFF_ARGS + 8)
    a.strb_w(0, 13, PAPP_OFF_ARGS + 12)
    a.add_sp_imm(0, PAPP_OFF_ARGS + 12)
    a.str_sp_imm(0, PAPP_OFF_ARGS + 0)
    a.add_imm_t3(0, 5, 8)
    a.movs_imm8(1, 5)                     # INVALID_PARAMETER
    a.movs_imm8(2, 0)                     # n=0
    a.add_sp_imm(3, PAPP_OFF_ARGS + 8)
    a.blx_imm(PLT_get_curplayer_value_rsp)
    a.b_w("papp_done")

    # ---- 0x14 SetPlayerApplicationSettingValue ----
    # Parse first (attr_id, value) pair from inbound param body at caller's
    # sp+387/+388 (= our sp+0x19b/+0x19c after the PAPP_FRAME shift), write
    # the 2 bytes to /data/data/com.innioasis.y1/files/y1-papp-set, ACK the
    # peer with success. The music app's PappSetFileObserver picks up the
    # file write and forwards the change to setMusicRepeatMode /
    # setMusicIsShuffle via SharedPreferencesUtils.
    #
    # Multi-pair Sets (n > 1) apply only the first pair. AVRCP V13 §5.2.4
    # lets a TG that supports a subset of attributes acknowledge any Set
    # whose listed attributes it can honor.
    a.label("papp_set")
    # Validate (attr_id, value) against the values we ACTUALLY advertise via
    # 0x12 ListValues. AVRCP V13 §6.15.2 defines status 0x05 INVALID_PARAMETER
    # for "the parameter is invalid" — appropriate when the CT sets a value
    # outside the supported set.
    #   attr_id 0x02 (Repeat): valid values 0x01..0x03 (OFF / SINGLE / ALL)
    #   attr_id 0x03 (Shuffle): valid values 0x01..0x02 (OFF / ALL_TRACK)
    # Any other attr_id is unsupported.
    a.ldrb_w(6, 13, PAPP_PARAM_OFF + 1)         # r6 = attr_id (caller's sp+387)
    a.ldrb_w(7, 13, PAPP_PARAM_OFF + 2)         # r7 = value   (caller's sp+388)

    # attr_id == Repeat → check value in [1..3]
    a.cmp_imm8(6, PAPP_ATTR_REPEAT)
    a.bne("papp_set_check_shuffle")
    a.cmp_imm8(7, 1)
    a.blt("papp_set_reject")
    a.cmp_imm8(7, 3)
    a.bgt("papp_set_reject")
    a.b_w("papp_set_validated")

    a.label("papp_set_check_shuffle")
    a.cmp_imm8(6, PAPP_ATTR_SHUFFLE)
    a.bne("papp_set_reject")
    a.cmp_imm8(7, 1)
    a.blt("papp_set_reject")
    a.cmp_imm8(7, 2)
    a.bgt("papp_set_reject")

    a.label("papp_set_validated")

    # Pack [attr_id, value] into the outgoing-args region (sp+0..1) as
    # the 2-byte write payload. set_player_value_rsp later in this arm
    # doesn't consume any stack args, so sp+0..1 is free scratch.
    a.strb_w(6, 13, PAPP_OFF_ARGS + 0)
    a.strb_w(7, 13, PAPP_OFF_ARGS + 1)

    # open(path_papp_set, O_WRONLY|O_TRUNC, 0) — TrackInfoWriter.prepareFiles()
    # in the music app pre-creates it at process start; if it's somehow gone,
    # skip the write but still ACK (the peer's UI shouldn't get stuck because
    # of a transient writer-side outage). No O_CREAT — same rationale as the
    # y1-track-info / y1-trampoline-state writes elsewhere in this module.
    a.adr_w(0, "path_papp_set")
    a.movw(1, O_WRONLY | O_TRUNC)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("papp_set_skip_write")
    a.mov_lo_lo(4, 0)                            # r4 = fd

    a.mov_lo_lo(0, 4)
    a.add_sp_imm(1, PAPP_OFF_ARGS)               # r1 = &scratch[0]
    a.movs_imm8(2, 2)                            # 2 bytes: attr_id + value
    a.blx_imm(PLT_write)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)

    a.label("papp_set_skip_write")

    # ACK the peer with success. set_player_value_rsp(conn, 0) emits the
    # spec-correct success reply per AVRCP V13 §5.2.4 / §6.15.2.
    a.add_imm_t3(0, 5, 8)                        # r0 = conn
    a.movs_imm8(1, 0)                            # r1 = 0 (success ACK)
    a.blx_imm(PLT_set_player_value_rsp)
    a.b_w("papp_done")

    a.label("papp_set_reject")
    # Reject path: emit set_player_value_rsp(conn, 0x05 INVALID_PARAMETER).
    # Per V13 §6.15.2 Tbl 6.2 status 0x05 = "The parameter is invalid".
    # The peer's UI typically falls back to its previous value, keeping
    # CT-side and Y1-side state in sync.
    a.add_imm_t3(0, 5, 8)                        # r0 = conn
    a.movs_imm8(1, 5)                            # r1 = 0x05 INVALID_PARAMETER
    a.blx_imm(PLT_set_player_value_rsp)
    a.b_w("papp_done")

    # ---- 0x15 GetPlayerApplicationSettingAttributeText ----
    # Inbound: 1 byte n + n attr_ids at sp+386..386+n.
    # btmtk_avrcp_send_get_player_attr_text_rsp(
    #     conn, reject, idx, total, attr_id, charset, length, *str)
    #
    # Walk the inbound list, set wantRepeat / wantShuffle flags, then emit
    # text only for requested attrs (V13 §5.2.5).
    a.label("papp_attr_text")
    a.ldrb_w(6, 13, PAPP_PARAM_OFF + 0)   # r6 = n (count of attr_ids)
    a.cmp_imm8(6, 0)
    a.beq_w("papp_done")                  # n=0: nothing to emit

    # Walk attr_ids[0..n-1] and accumulate flags in r4 (wantRepeat) / r5
    # (wantShuffle). Loop variable in r3.
    a.movs_imm8(4, 0)                     # r4 = wantRepeat = 0
    a.movs_imm8(5, 0)                     # r5 = wantShuffle = 0
    a.movs_imm8(3, 0)                     # r3 = i = 0
    # Base pointer to attr_ids[0] = sp + PAPP_PARAM_OFF + 1. addw supports
    # 12-bit immediates (PAPP_PARAM_OFF+1 = 411 fits).
    a.addw(2, 13, PAPP_PARAM_OFF + 1)     # r2 = &attr_ids[0]

    a.label("papp_at_loop")
    a.cmp_w(3, 6)
    a.bge("papp_at_loop_done")
    a.ldrb_reg(0, 2, 3)                   # r0 = attr_ids[i]
    a.cmp_imm8(0, PAPP_ATTR_REPEAT)
    a.beq("papp_at_set_repeat")
    a.cmp_imm8(0, PAPP_ATTR_SHUFFLE)
    a.beq("papp_at_set_shuffle")
    a.b_w("papp_at_loop_next")

    a.label("papp_at_set_repeat")
    a.movs_imm8(4, 1)
    a.b_w("papp_at_loop_next")

    a.label("papp_at_set_shuffle")
    a.movs_imm8(5, 1)

    a.label("papp_at_loop_next")
    a.addw(3, 3, 1)                       # i++
    a.b_w("papp_at_loop")

    a.label("papp_at_loop_done")
    # total = wantRepeat + wantShuffle in r0
    a.adds_lo_lo(0, 4, 5)
    a.cmp_imm8(0, 0)
    a.beq_w("papp_done")                  # neither requested → no emit

    # Save total in r6 (which we no longer need for n — done iterating)
    a.mov_lo_lo(6, 0)

    # If wantRepeat: emit (idx=0, total=r6, attr_id=2, "Repeat", 6 B)
    a.cmp_imm8(4, 0)
    a.beq("papp_at_skip_repeat")
    a.movs_imm8(2, PAPP_ATTR_REPEAT)
    a.str_sp_imm(2, PAPP_OFF_ARGS + 0)
    a.movs_imm8(2, 0x6A)
    a.str_sp_imm(2, PAPP_OFF_ARGS + 4)
    a.movs_imm8(2, 6)                     # strlen("Repeat")
    a.str_sp_imm(2, PAPP_OFF_ARGS + 8)
    a.adr_w(2, "papp_text_repeat")
    a.str_sp_imm(2, PAPP_OFF_ARGS + 12)
    a.add_imm_t3(0, 5, 8)
    a.movs_imm8(1, 0)
    a.movs_imm8(2, 0)                     # idx 0
    a.mov_lo_lo(3, 6)                     # total
    a.blx_imm(PLT_get_player_attr_text_rsp)

    a.label("papp_at_skip_repeat")
    # If wantShuffle: emit (idx = wantRepeat ? 1 : 0, total=r6, attr_id=3, "Shuffle", 7 B)
    a.cmp_imm8(5, 0)
    a.beq_w("papp_done")
    a.movs_imm8(2, PAPP_ATTR_SHUFFLE)
    a.str_sp_imm(2, PAPP_OFF_ARGS + 0)
    a.movs_imm8(2, 0x6A)
    a.str_sp_imm(2, PAPP_OFF_ARGS + 4)
    a.movs_imm8(2, 7)                     # strlen("Shuffle")
    a.str_sp_imm(2, PAPP_OFF_ARGS + 8)
    a.adr_w(2, "papp_text_shuffle")
    a.str_sp_imm(2, PAPP_OFF_ARGS + 12)
    a.add_imm_t3(0, 5, 8)
    a.movs_imm8(1, 0)
    a.mov_lo_lo(2, 4)                     # idx = wantRepeat (0 or 1)
    a.mov_lo_lo(3, 6)                     # total
    a.blx_imm(PLT_get_player_attr_text_rsp)
    a.b_w("papp_done")

    a.label("papp_done")
    a.addw(13, 13, PAPP_FRAME)
    a.b_w("t4_to_epilogue")


def _emit_subscription_write(a: Asm, byte_value: int, state_byte_offset: int,
                             scratch_sp_offset: int, fail_label: str) -> None:
    """Write `byte_value` (0 or 1) to y1-trampoline-state[state_byte_offset].

    Used by T2 / T8 to ARM and T5 / T9 to CLEAR per-event subscription bits
    for the AVRCP §6.7.1 once-per-registration semantic. r4 is used as fd;
    caller must ensure r4 is dead. `scratch_sp_offset` is a 1-byte stack
    region the byte_value is written to first (so we can pass &sp[off] as
    the write source). Uses strb (1 byte) not str (4 bytes) so the scratch
    location can safely overlap state bytes we no longer need without
    clobbering adjacent state we still need. `fail_label` is the branch
    target if open() fails; the rest of the block falls through after close().
    """
    a.movs_imm8(0, byte_value)
    a.strb_w(0, 13, scratch_sp_offset)        # 1-byte store, no adjacent clobber

    a.adr_w(0, "path_state")
    a.movw(1, O_WRONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt_w(fail_label)                       # wide-form: ±1 MB range
    a.mov_lo_lo(4, 0)                         # r4 = fd

    a.mov_lo_lo(0, 4)
    a.movs_imm8(1, state_byte_offset)
    a.movs_imm8(2, SEEK_SET)
    a.movs_imm8(7, NR_lseek)
    a.svc(0)

    a.mov_lo_lo(0, 4)
    a.addw(1, 13, scratch_sp_offset)          # r1 = sp + scratch (12-bit imm,
                                              #   no 4-B alignment requirement)
    a.movs_imm8(2, 1)
    a.blx_imm(PLT_write)

    a.mov_lo_lo(0, 4)
    a.blx_imm(PLT_close)


def _emit_t8(a: Asm) -> None:
    """T8: RegisterNotification INTERIM dispatch for events other than
    TRACK_CHANGED (0x02, handled by extended_T2).

    Branched from extended_T2's "PDU 0x31 + non-0x02 event" arm. Reads
    y1-track-info into a stack buffer (for events 0x01 and 0x05 which
    need play_status / position from the schema), then dispatches on
    event_id and emits an INTERIM via the appropriate
    `reg_notievent_*_rsp` PLT entry. All these response builders share
    the same calling convention as their TRACK_CHANGED sibling: r0=conn,
    r1=0 (success), r2=reasonCode, r3=event-specific payload (or unused).
    transId is auto-extracted from conn[17] inside each builder.

    Events handled (per AVRCP 1.3 §5.4.2 Tables 5.29/5.31/5.32/5.33/5.34/5.36):
      0x01 PLAYBACK_STATUS_CHANGED  — Table 5.29; INTERIM with 1-byte
                                      play_status (from y1-track-info[792])
      0x03 TRACK_REACHED_END        — Table 5.31; INTERIM, no payload
      0x04 TRACK_REACHED_START      — Table 5.32; INTERIM, no payload
      0x05 PLAYBACK_POS_CHANGED     — Table 5.33; INTERIM with 4-byte
                                      position_ms (BE in file → REV → host
                                      order)
      0x06 BATT_STATUS_CHANGED      — Table 5.34; INTERIM with 1-byte canned
                                      0x00 NORMAL (Table 5.35 enum)
      0x07 SYSTEM_STATUS_CHANGED    — Table 5.36; INTERIM with 1-byte canned
                                      0x00 POWER_ON

    Unknown event_id falls through to "unknow indication" (0x65bc) for the
    spec-correct NOT_IMPLEMENTED reject.

    T8 ships INTERIM-only; proactive CHANGED for event 0x01
    PLAYBACK_STATUS_CHANGED lives in T9 (paired with the cardinality NOP at
    sswitch_18a / 0x3c4fe in MtkBt.odex, which mirrors the TRACK_CHANGED
    cardinality bypass at 0x3c530). CTs that subscribe to events 0x03..0x07
    receive the immediate INTERIM and can re-subscribe periodically to
    refresh.

    Frame: 800 B file_buf at sp+0. None of the response builders need
    stack args (all 4 args fit in r0 / r1 / r2 / r3). Caller's event_id is
    accessed via T8_EVENT_ID_OFF (= 386 + frame).
    """
    a.label("T8")

    # ---- allocate stack frame ----
    a.subw(13, 13, T8_FRAME)                  # sub.w sp, sp, #800

    # ---- memset(file_buf, 0, 800) ----
    # Default everything to 0 so a partial read (file shorter than 800 B
    # — e.g. an older writer built against an earlier schema) gives
    # play_status=0 (STOPPED) and position=0 rather than uninit stack
    # garbage.
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

    # Arm sub_play_status bit (event 0x01) per AVRCP §6.7.1.
    _emit_subscription_write(a, 1, 14, T8_OFF_TIMESPEC_SEC, "t8_done")
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

    # Arm sub_track_reached_end (event 0x03) per AVRCP §6.7.1.
    _emit_subscription_write(a, 1, 17, T8_OFF_TIMESPEC_SEC, "t8_done")
    a.b_w("t8_done")

    a.label("t8_check_4")
    a.cmp_imm8(0, 0x04)
    a.bne("t8_check_5")
    # 0x04 TRACK_REACHED_START
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(1, 0)
    a.add_imm_t3(0, 5, 8)
    a.blx_imm(PLT_reg_notievent_reached_start_rsp)

    # Arm sub_track_reached_start (event 0x04) per AVRCP §6.7.1.
    _emit_subscription_write(a, 1, 18, T8_OFF_TIMESPEC_SEC, "t8_done")
    a.b_w("t8_done")

    a.label("t8_check_5")
    a.cmp_imm8(0, 0x05)
    a.bne("t8_check_6")
    # 0x05 PLAYBACK_POS_CHANGED — live-extrapolate position when PLAYING
    # so a fresh CT subscribe sees the actual current position, not the
    # last state-change anchor. AVRCP 1.3 §5.4.1 Tbl 5.26 SongPosition is
    # "the current position of the playing in milliseconds elapsed".
    # When STOPPED/PAUSED the position field IS the freeze point (saved_pos
    # is the right value).
    a.ldrb_w(0, 13, T8_OFF_FILE_PLAYFLAG)
    a.cmp_imm8(0, 1)                          # 1 = PLAYING
    a.bne("t8_pos_static")

    # ---- live extrapolation ----
    # Same magic-multiply math T6/T9 use:
    #   now_ms = tv_sec * 1000 + tv_nsec / 1e6
    #   live_pos = saved_pos_ms + (now_ms - state_change_ms)
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, T8_OFF_TIMESPEC_SEC)
    a.str_sp_imm(0, T8_OFF_TIMESPEC_NSEC)
    a.movs_imm8(0, CLOCK_BOOTTIME)
    a.add_sp_imm(1, T8_OFF_TIMESPEC)
    a.movw(7, NR_clock_gettime)
    a.svc(0)

    a.ldr_sp_imm(2, T8_OFF_TIMESPEC_SEC)      # r2 = tv_sec
    a.movw(0, 1000)
    a.muls_lo_lo(2, 0)                        # r2 = tv_sec * 1000
    a.ldr_sp_imm(0, T8_OFF_TIMESPEC_NSEC)     # r0 = tv_nsec
    a.movw(1, 0xDE83)
    a.movt(1, 0x431B)                         # r1 = 0x431BDE83 (magic for /1e6)
    a.umull(4, 3, 0, 1)                       # r3:r4 = tv_nsec * magic
    a.lsrs_imm5(3, 3, 18)                     # r3 = tv_nsec / 1e6
    a.adds_lo_lo(2, 2, 3)                     # r2 = now_ms

    a.ldr_sp_imm(0, T8_OFF_FILE_STATE_TIME)   # r0 = state_change_ms (BE)
    a.rev_lo_lo(0, 0)                         # → host order
    a.subs_lo_lo(2, 2, 0)                     # r2 = delta_ms

    a.ldr_sp_imm(3, T8_OFF_FILE_POS)          # r3 = saved_pos (BE)
    a.rev_lo_lo(3, 3)                         # → host order
    a.adds_lo_lo(3, 3, 2)                     # r3 = live_pos
    a.b_w("t8_pos_emit")

    a.label("t8_pos_static")
    a.ldr_sp_imm(3, T8_OFF_FILE_POS)          # r3 = saved_pos (BE)
    a.rev_lo_lo(3, 3)                         # → host order

    a.label("t8_pos_emit")
    # reg_notievent_pos_changed_rsp(conn, 0, REASON_INTERIM, position_ms_u32)
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(1, 0)
    a.add_imm_t3(0, 5, 8)
    a.blx_imm(PLT_reg_notievent_pos_changed_rsp)

    # Arm sub_pos_changed bit (event 0x05) per AVRCP §6.7.1 per-subscription
    # "once" rule. T9 will emit exactly one PLAYBACK_POS_CHANGED CHANGED then
    # clear the bit; CT must re-register to receive the next.
    _emit_subscription_write(a, 1, 13, T8_OFF_TIMESPEC_SEC, "t8_done")
    a.b_w("t8_done")

    a.label("t8_check_6")
    a.cmp_imm8(0, 0x06)
    a.bne("t8_check_7")
    # 0x06 BATT_STATUS_CHANGED.
    # reg_notievent_battery_status_changed_rsp(conn, 0, REASON_INTERIM, batt_status_u8)
    # batt_status read from y1-track-info[794], where the music app's
    # BatteryReceiver writes the AVRCP enum (0=NORMAL, 1=WARNING, 2=CRITICAL,
    # 3=EXTERNAL, 4=FULL_CHARGE) bucket-mapped from
    # Android `Intent.ACTION_BATTERY_CHANGED`. Stack is memset to 0 before the
    # read, so a short file gives BATT_STATUS_NORMAL — benign default.
    a.ldrb_w(3, 13, T8_OFF_FILE_BATTERY)
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(1, 0)
    a.add_imm_t3(0, 5, 8)
    a.blx_imm(PLT_reg_notievent_battery_status_rsp)

    # Arm sub_battery (event 0x06) per AVRCP §6.7.1.
    _emit_subscription_write(a, 1, 19, T8_OFF_TIMESPEC_SEC, "t8_done")
    a.b_w("t8_done")

    a.label("t8_check_7")
    a.cmp_imm8(0, 0x07)
    a.bne("t8_check_8")
    # 0x07 SYSTEM_STATUS_CHANGED
    # reg_notievent_system_status_changed_rsp(conn, 0, REASON_INTERIM, system_status_u8)
    a.movs_imm8(3, SYSTEM_STATUS_POWERED)
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(1, 0)
    a.add_imm_t3(0, 5, 8)
    a.blx_imm(PLT_reg_notievent_system_status_rsp)
    a.b_w("t8_done")

    a.label("t8_check_8")
    a.cmp_imm8(0, 0x08)
    a.bne("t8_unknown_event")
    # 0x08 PLAYER_APPLICATION_SETTING_CHANGED INTERIM.
    # reg_notievent_player_appsettings_changed_rsp(
    #     conn, 0, REASON_INTERIM, n, *attr_ids, *values)
    # Live values read from y1-track-info[795..796] (the music app's
    # PappStateBroadcaster writes both bytes on every musicRepeatMode /
    # musicIsShuffle SharedPreferences change). file_buf is already loaded
    # into sp+0..799 above. Storing the outgoing-args at sp[0]/sp[4]
    # clobbers file_buf[0..7] (track_id), but track_id isn't read by this
    # arm and the frame is freed at t8_done.
    a.adr_w(0, "papp_attr_ids")
    a.str_sp_imm(0, 0)                          # sp[0] = &[2, 3]
    a.addw(0, 13, T8_OFF_FILE_REPEAT)           # r0 = &file[795] (= [r, s])
    a.str_sp_imm(0, 4)                          # sp[4] = current values
    a.add_imm_t3(0, 5, 8)                       # r0 = conn
    a.movs_imm8(1, 0)                           # success
    a.movs_imm8(2, REASON_INTERIM)
    a.movs_imm8(3, 2)                           # n=2
    a.blx_imm(PLT_reg_notievent_player_appsettings_rsp)

    # Arm sub_papp bit (event 0x08) per AVRCP §6.7.1.
    _emit_subscription_write(a, 1, 15, T8_OFF_TIMESPEC_SEC, "t8_done")
    a.b_w("t8_done")

    a.label("t8_unknown_event")
    # event_id we don't handle → spec-correct NOT_IMPLEMENTED reject via
    # the original "unknow indication" path. Restore stack first so the
    # reject-path's stack-canary check sees the correct sp.
    a.addw(13, 13, T8_FRAME)
    a.ldrh_w(14, 13, T4_LR_CANARY_OFF_ENTRY)  # restore lr canary = SIZE
    a.add_imm_t3(0, 5, 8)                     # restore r0 = conn
    a.b_w("t4_to_unknown")

    a.label("t8_done")
    # ---- restore stack and tail-call epilogue ----
    a.addw(13, 13, T8_FRAME)
    a.b_w("t4_to_epilogue")


def _emit_t9(a: Asm) -> None:
    """T9: proactive PLAYBACK_STATUS_CHANGED + BATT_STATUS_CHANGED + PLAYBACK_POS_CHANGED.

    Entered via `b.w T9` from the patched libextavrcp_jni.so::
    notificationPlayStatusChangedNative stub at file offset 0x3c88. MtkBt's
    handleKeyMessage path -- with the cardinality if-eqz NOPed at
    sswitch_18a (file offset 0x3c4fe in MtkBt.odex; mirrors the
    sswitch_1a3 / TRACK_CHANGED NOP at 0x3c530) -- invokes the native
    method on every `playstatechanged` broadcast emitted by the music app,
    asynchronously to any inbound AVRCP RegisterNotification.

    Closes the AVRCP 1.3 §5.4.2 spec gap that T8 alone leaves: T8 handles
    events 0x01 / 0x05 / 0x06 INTERIM-only, never fires the spec-mandated
    CHANGED frame when the value actually flips. Without T9 a polling CT
    subscribes to event 0x01 / 0x05 / 0x06, gets the immediate INTERIM,
    then never sees CHANGED, so the car-side play / pause icon, scrub bar,
    and battery indicator stay stuck on their initial values even though
    Y1's audio toggles correctly via the PASSTHROUGH path.

    Battery and periodic position both piggyback on this same trampoline.
    The music app fires `playstatechanged` whenever ANY of the following
    occurs: actual play / pause edge, battery bucket transition, or 1 s
    tick (while playing). T9 unconditionally:

      1. play_status: emit PLAYBACK_STATUS_CHANGED CHANGED on file[792]
         vs state[9] edge.
      2. battery_status: emit BATT_STATUS_CHANGED CHANGED on file[794]
         vs state[10] edge. Stock MtkBt's
         BTAvrcpSystemListener.onBatteryStatusChange dispatch chain is
         dead (BTAvrcpMusicAdapter$2 overrides it with a log-only stub),
         so reusing `playstatechanged` as the trigger is the cheapest
         spec-compliant alternative.
      3. pos_changed: emit PLAYBACK_POS_CHANGED CHANGED if file[792] == 1
         (PLAYING), with live-extrapolated position from
         clock_gettime(CLOCK_BOOTTIME) — same arithmetic T6 does for
         GetPlayStatus. Emits at our 1 s cadence rather than the CT's
         RegisterNotification `playback_interval`; this is a
         spec-permissible floor (the spec mandates a maximum interval,
         not a minimum cadence).

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
         play_status (AVRCP §5.4.1 Tbl 5.26 enum); file[794] = current
         battery_status (AVRCP §5.4.2 Tbl 5.35 enum).
      3. Read y1-trampoline-state (16 B) into state_buf @ sp+0..15.
         state[9]  = last_play_status.
         state[10] = last_battery_status.
      4. play_status compare → emit reg_notievent_playback_rsp CHANGED on
         edge; update state[9].
      5. battery_status compare → emit
         reg_notievent_battery_status_changed_rsp CHANGED on edge; update
         state[10].
      6. If either changed, write 16 B state back.

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
    # — e.g. an older writer built against an earlier schema) gives
    # play_status=0 (STOPPED) rather than uninit stack garbage.
    a.add_sp_imm(0, T9_OFF_FILE)
    a.movs_imm8(1, 0)
    a.movw(2, 800)
    a.blx_imm(PLT_memset)

    # ---- memset(state_buf, 0, 20) ----
    # State is 20 B: bytes 0..12 = T5 / T9 track + edge-tracking; bytes
    # 13..19 = per-event subscription gates (see T9_STATE_SUB_*_OFF).
    a.add_sp_imm(0, T9_OFF_STATE)
    a.movs_imm8(1, 0)
    a.movs_imm8(2, 20)
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
    a.movs_imm8(2, 20)                        # 20 B: 16 legacy + 4 sub_* bytes
    a.movs_imm8(7, NR_read)
    a.svc(0)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t9_skip_state_read")

    # r5 was the fd in the read blocks above; both closes ran, so r5 is
    # dead here. Repurpose r5 as `any_change` accumulator: 1 if either
    # play_status or battery_status edge fired (so the state file gets
    # written back). r5 is callee-save so PLT calls below preserve it.
    a.movs_imm8(5, 0)                         # r5 = any_change = 0

    # ---- play_status compare (file[792] vs state[9]) ----
    a.ldrb_w(0, 13, T9_OFF_FILE_PLAYFLAG)     # r0 = current play_status
    a.ldrb_w(1, 13, T9_STATE_LAST_PS_OFF)     # r1 = last_play_status
    a.cmp_w(0, 1)
    a.beq("t9_after_play_check")

    # Edge detected. Update state[9] = file[792] in-memory unconditionally
    # so we don't loop "edge detected" forever while un-subscribed; the
    # state-writeback below will persist this.
    a.strb_w(0, 13, T9_STATE_LAST_PS_OFF)
    a.movs_imm8(5, 1)                         # any_change = 1

    # Subscription gate (AVRCP §6.7.1): emit CHANGED only if T8 INTERIM
    # has armed sub_play_status (state[14] = 1) since last emit. CTs that
    # don't re-register won't get phantom CHANGEDs; CTs that do (Sonos,
    # iPhone) get a fresh CHANGED per subscription cycle.
    a.ldrb_w(1, 13, T9_STATE_SUB_PLAY_OFF)
    a.cmp_imm8(1, 0)
    a.beq("t9_after_play_check")

    # ---- emit CHANGED via reg_notievent_playback_rsp ----
    # r0 = conn (= struct + 8); r1 = 0 success; r2 = REASON_CHANGED;
    # r3 = play_status (from file_buf[792]).
    a.add_imm_t3(0, 4, 8)                     # r0 = r4 + 8 (conn)
    a.movs_imm8(1, 0)                         # success
    a.movs_imm8(2, REASON_CHANGED)
    a.ldrb_w(3, 13, T9_OFF_FILE_PLAYFLAG)     # r3 = play_status
    a.blx_imm(PLT_reg_notievent_playback_rsp)

    # Clear sub_play_status (state[14]) — subscription consumed.
    _emit_subscription_write(a, 0, 14, T9_OFF_TIMESPEC_SEC, "t9_after_play_check")

    a.label("t9_after_play_check")

    # ---- battery_status compare (file[794] vs state[10]) ----
    # AVRCP 1.3 §5.4.2 Tbl 5.34 (BATT_STATUS_CHANGED CHANGED) carries a
    # 1-byte battery_status payload (Tbl 5.35 enum). The music app's
    # BatteryReceiver bucket-maps Android `Intent.ACTION_BATTERY_CHANGED`
    # (level + plug state) to the AVRCP enum on every transition and writes
    # file[794] before firing `playstatechanged`. T9 then picks it up.
    a.ldrb_w(0, 13, T9_OFF_FILE_BATTERY)      # r0 = current battery_status
    a.ldrb_w(1, 13, T9_STATE_LAST_BATT_OFF)   # r1 = last_battery_status
    a.cmp_w(0, 1)
    a.beq("t9_after_batt_check")

    # Edge detected. Update state[10] = file[794] in-memory unconditionally
    # so we don't loop "edge detected, can't emit" forever while un-subscribed.
    a.strb_w(0, 13, T9_STATE_LAST_BATT_OFF)
    a.movs_imm8(5, 1)                         # any_change = 1

    # Subscription gate (AVRCP §6.7.1): emit only if sub_battery armed.
    a.ldrb_w(1, 13, T9_STATE_SUB_BATT_OFF)
    a.cmp_imm8(1, 0)
    a.beq("t9_after_batt_check")

    # ---- emit CHANGED via reg_notievent_battery_status_changed_rsp ----
    a.add_imm_t3(0, 4, 8)                     # r0 = r4 + 8 (conn)
    a.movs_imm8(1, 0)                         # success
    a.movs_imm8(2, REASON_CHANGED)
    a.ldrb_w(3, 13, T9_OFF_FILE_BATTERY)      # r3 = battery_status
    a.blx_imm(PLT_reg_notievent_battery_status_rsp)

    # Clear sub_battery (state[19]) — subscription consumed.
    _emit_subscription_write(a, 0, 19, T9_OFF_TIMESPEC_SEC, "t9_after_batt_check")

    a.label("t9_after_batt_check")

    # ---- papp settings compare (file[795] / file[796] vs state[11] / state[12]) ----
    # AVRCP 1.3 §5.4.2 Tbl 5.36 (PLAYER_APPLICATION_SETTING_CHANGED CHANGED).
    # The music app's PappStateBroadcaster writes y1-track-info[795] =
    # repeat_avrcp and [796] = shuffle_avrcp on every SharedPreferences change
    # to musicRepeatMode / musicIsShuffle and fires `playstatechanged` so T9
    # picks up the edge — same trigger pipeline as the play_status / battery
    # checks above.
    # Spec values: §5.2.4 Tbl 5.20 (Repeat: 0x01 OFF / 0x02 SINGLE / 0x03 ALL
    # / 0x04 GROUP); Tbl 5.21 (Shuffle: 0x01 OFF / 0x02 ALL / 0x03 GROUP).
    a.ldrb_w(0, 13, T9_OFF_FILE_REPEAT)       # r0 = current repeat
    a.ldrb_w(1, 13, T9_STATE_LAST_REPEAT_OFF) # r1 = last repeat
    a.cmp_w(0, 1)
    a.bne("t9_papp_emit")
    a.ldrb_w(0, 13, T9_OFF_FILE_SHUFFLE)
    a.ldrb_w(1, 13, T9_STATE_LAST_SHUFFLE_OFF)
    a.cmp_w(0, 1)
    a.beq("t9_after_papp_check")

    a.label("t9_papp_emit")
    # Edge detected. Update state[11] / state[12] in-memory unconditionally
    # so we don't loop "edge detected" forever while un-subscribed.
    a.ldrb_w(0, 13, T9_OFF_FILE_REPEAT)
    a.strb_w(0, 13, T9_STATE_LAST_REPEAT_OFF)
    a.ldrb_w(0, 13, T9_OFF_FILE_SHUFFLE)
    a.strb_w(0, 13, T9_STATE_LAST_SHUFFLE_OFF)
    a.movs_imm8(5, 1)                         # any_change = 1

    # Subscription gate (AVRCP §6.7.1): emit CHANGED only if T8 INTERIM
    # has armed sub_papp (state[15] = 1) since last emit. Without this,
    # Bolt's PApp UI freezes after the first CHANGED (subscription consumed,
    # no re-registration).
    a.ldrb_w(1, 13, T9_STATE_SUB_PAPP_OFF)
    a.cmp_imm8(1, 0)
    a.beq("t9_after_papp_check")

    # ---- emit CHANGED via reg_notievent_player_appsettings_changed_rsp ----
    # (conn, 0, REASON_CHANGED, n=2, *attr_ids, *values)
    # *values = &file[795] — file_buf already holds [repeat, shuffle]
    # contiguously at offsets 795..796.
    a.adr_w(0, "papp_attr_ids")
    a.str_sp_imm(0, T9_OFF_ARGS + 0)          # sp[0] = &[2, 3]
    a.addw(0, 13, T9_OFF_FILE_REPEAT)         # r0 = &file[795] (= [r, s])
    a.str_sp_imm(0, T9_OFF_ARGS + 4)          # sp[4] = current values
    a.add_imm_t3(0, 4, 8)                     # r0 = conn (struct + 8)
    a.movs_imm8(1, 0)                         # success
    a.movs_imm8(2, REASON_CHANGED)
    a.movs_imm8(3, 2)                         # n
    a.blx_imm(PLT_reg_notievent_player_appsettings_rsp)

    # Clear sub_papp (state[15]) — subscription consumed.
    _emit_subscription_write(a, 0, 15, T9_OFF_TIMESPEC_SEC, "t9_after_papp_check")

    a.label("t9_after_papp_check")

    # ---- write only T9's bytes (state[9..12] = 4 B) if any edge fired ----
    # No O_TRUNC and lseek to offset 9 so we leave T5's bytes 0..8
    # (track_id + transId) intact. Eliminates the read-modify-write race
    # that the previous full-16-B write had with concurrent T5 firings.
    a.cmp_imm8(5, 0)
    a.beq("t9_after_state_write")

    a.adr_w(0, "path_state")
    a.movw(1, O_WRONLY)
    a.movs_imm8(2, 0)
    a.blx_imm(PLT_open)
    a.cmp_imm8(0, 0)
    a.blt("t9_after_state_write")             # open failed → skip write, still proceed
    a.mov_lo_lo(5, 0)                         # r5 = fd

    # lseek(fd, 9, SEEK_SET) — position at start of T9's owned region.
    a.mov_lo_lo(0, 5)
    a.movs_imm8(1, 9)
    a.movs_imm8(2, SEEK_SET)
    a.movs_imm8(7, NR_lseek)
    a.svc(0)

    # write(fd, &state[9], 4) — 4 bytes: last_play / last_battery /
    # last_repeat / last_shuffle.
    a.mov_lo_lo(0, 5)
    a.addw(1, 13, T9_STATE_LAST_PS_OFF)       # r1 = sp + state[9] offset
    a.movs_imm8(2, 4)
    a.blx_imm(PLT_write)

    a.mov_lo_lo(0, 5)
    a.blx_imm(PLT_close)

    a.label("t9_after_state_write")

    # ---- emit PLAYBACK_POS_CHANGED CHANGED if playing ----
    # AVRCP 1.3 §5.4.2 Tbl 5.33. ICS Table 7 row 27 (Optional). Emit a
    # live-extrapolated position whenever T9 fires while file[792] == 1
    # (PLAYING). The music app runs a 1 s tick that fires the
    # `playstatechanged` broadcast — same trigger T9 already uses for the
    # play-status / battery checks above — so this gives the CT roughly 1 Hz
    # CHANGED frames while playing. Strictly the spec says the CT gets to
    # set its own `playback_interval` via the original RegisterNotification
    # command and we should emit at exactly that rate; honoring the
    # CT-supplied interval would require us to capture and persist it from
    # T8's INTERIM-time stack frame, which is more involved than the
    # current build budget. Emitting at our 1 s cadence is spec-permissible
    # because (1) the spec doesn't forbid emitting MORE frequently than
    # requested (`shall be emitted` defines a floor, not a ceiling), and
    # (2) the CT can simply ignore frames that arrive faster than its
    # display refresh rate.
    a.ldrb_w(0, 13, T9_OFF_FILE_PLAYFLAG)
    a.cmp_imm8(0, 1)                          # 1 = PLAYING (AVRCP §5.4.1 Tbl 5.26)
    a.bne("t9_done")

    # Subscription gate per AVRCP §6.7.1: state[13] = 1 means T8 emitted an
    # INTERIM for event 0x05 since the last CHANGED. If 0, the previous
    # CHANGED already consumed the subscription and we must wait for a new
    # RegisterNotification before emitting again — strict CTs (Kia) reject
    # unsolicited CHANGEDs and freeze the playhead display.
    a.ldrb_w(0, 13, T9_STATE_SUB_POS_OFF)
    a.cmp_imm8(0, 0)
    a.beq("t9_done")

    # ---- clock_gettime(CLOCK_BOOTTIME, &timespec) ----
    # Default the timespec to zero so a syscall failure yields a useless
    # but bounded fallback (delta_sec computed against now=0 is negative,
    # live_pos collapses to saved_pos minus a constant — CTs render a
    # static or rewinding value rather than uninit garbage).
    a.movs_imm8(0, 0)
    a.str_sp_imm(0, T9_OFF_TIMESPEC_SEC)
    a.str_sp_imm(0, T9_OFF_TIMESPEC_NSEC)

    a.movs_imm8(0, CLOCK_BOOTTIME)
    a.add_sp_imm(1, T9_OFF_TIMESPEC)          # r1 = &timespec
    a.movw(7, NR_clock_gettime)
    a.svc(0)

    # ---- now_ms = tv_sec * 1000 + tv_nsec / 1_000_000 ----
    # Same arithmetic T6 does for GetPlayStatus, including the magic-multiply
    # for tv_nsec/1e6. The music app's TrackInfoWriter writes
    # state_change_time_ms directly from SystemClock.elapsedRealtime() with no
    # /1000 truncation, so both endpoints carry full ms precision.
    # CLOCK_BOOTTIME parity with elapsedRealtime makes the subtraction exact.
    a.ldr_sp_imm(2, T9_OFF_TIMESPEC_SEC)      # r2 = tv_sec
    a.movw(0, 1000)
    a.muls_lo_lo(2, 0)                        # r2 = tv_sec * 1000
    a.ldr_sp_imm(0, T9_OFF_TIMESPEC_NSEC)     # r0 = tv_nsec
    a.movw(1, 0xDE83)
    a.movt(1, 0x431B)                         # r1 = 0x431BDE83 (magic)
    a.umull(5, 3, 0, 1)                       # r3:r5 = tv_nsec * magic; r3 = high half
    a.lsrs_imm5(3, 3, 18)                     # r3 = high >> 18 = tv_nsec / 1e6
    a.adds_lo_lo(2, 2, 3)                     # r2 = now_ms

    # ---- delta_ms = now_ms - state_change_ms ----
    # u32 modular subtraction; correct under wrap (u32 ms wraps at ~49.7
    # days uptime, well past Y1 reboot cadence).
    a.ldr_sp_imm(0, T9_OFF_FILE_STATE_TIME)   # r0 = state_change_ms (BE)
    a.rev_lo_lo(0, 0)                         # → host order
    a.subs_lo_lo(2, 2, 0)                     # r2 = delta_ms

    # ---- live_pos = saved_pos + delta_ms ----
    a.ldr_sp_imm(3, T9_OFF_FILE_POS)          # r3 = saved_pos (BE)
    a.rev_lo_lo(3, 3)                         # → host order
    a.adds_lo_lo(3, 3, 2)                     # r3 = live_pos

    # ---- emit reg_notievent_pos_changed_rsp(conn, 0, REASON_CHANGED, live_pos) ----
    a.add_imm_t3(0, 4, 8)                     # r0 = conn (= struct + 8)
    a.movs_imm8(1, 0)                         # success
    a.movs_imm8(2, REASON_CHANGED)
    # r3 already = live_pos
    a.blx_imm(PLT_reg_notievent_pos_changed_rsp)

    # Clear sub_pos_changed (event 0x05) per AVRCP §6.7.1 once-consumed
    # semantics. Next CHANGED only fires after T8 INTERIM emit re-arms the
    # bit (CT re-registers). State-writeback block above already ran for
    # this T9 invocation; dedicated 1-byte write needed here.
    _emit_subscription_write(a, 0, 13, T9_OFF_TIMESPEC_SEC, "t9_done")

    a.label("t9_done")
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
    _emit_t_charset(a)                        # Inform PDU 0x17
    _emit_t_battery(a)                        # Inform PDU 0x18
    _emit_t_continuation(a)                   # Continuation PDUs 0x40/0x41
    _emit_t6(a)                               # PDU 0x30 GetPlayStatus
    _emit_t_papp(a)                           # PApp PDUs 0x11..0x16
    _emit_t8(a)                               # PDU 0x31 RegisterNotification dispatch
    _emit_t9(a)                               # proactive PLAYBACK_STATUS_CHANGED + battery + position

    # Path strings, 4-byte-aligned for clean ADR offsets.
    a.align(4)
    a.label("path_track_info")
    a.asciiz("/data/data/com.innioasis.y1/files/y1-track-info")
    a.align(4)
    a.label("path_state")
    a.asciiz("/data/data/com.innioasis.y1/files/y1-trampoline-state")
    a.align(4)
    a.label("path_papp_set")
    a.asciiz("/data/data/com.innioasis.y1/files/y1-papp-set")
    a.align(4)

    # 0xFF×8 sentinel passed as the track_id pointer to
    # btmtk_avrcp_send_reg_notievent_track_changed_rsp for both INTERIM and
    # CHANGED responses. AVRCP 1.3 §5.4.2 Table 5.30 ("If no track currently
    # selected, then return 0xFFFFFFFF in the INTERIM response"; the field
    # is 8 bytes — printed text in 1.3 is a typo) + ESR07 §2.2 clarifying
    # to the 8-byte form 0xFFFFFFFFFFFFFFFF.
    # Semantic: "this information is not bound to a particular media
    # element", which keeps the CT in poll-on-each-event mode.
    a.label("sentinel_ffx8")
    a.raw(b"\xFF" * 8)

    # PApp data tables (PDU 0x11..0x16). All AVRCP 1.3 §5.2 spec values.
    a.align(4)
    a.label("papp_attr_ids")
    a.raw(bytes([PAPP_ATTR_REPEAT, PAPP_ATTR_SHUFFLE]))
    a.align(4)
    a.label("papp_repeat_values")
    # Y1's musicRepeatMode int enum has 3 values (0=OFF, 1=ONE, 2=ALL) — no
    # GROUP. Spec V13 Tbl 5.20 also defines 0x04 GROUP but we'd be lying to
    # advertise it (T_papp 0x14 would ACK a Set-to-GROUP that Y1 can't honor).
    a.raw(bytes([0x01, 0x02, 0x03]))         # OFF, SINGLE, ALL
    a.align(4)
    a.label("papp_shuffle_values")
    # Y1's musicIsShuffle is a boolean (false/true). Spec V13 Tbl 5.21 also
    # defines 0x03 GROUP, omitted here for the same honesty reason.
    a.raw(bytes([0x01, 0x02]))               # OFF, ALL_TRACK
    a.align(4)
    a.label("papp_current_values")
    # Fallback OFF/OFF for T_papp 0x13 GetCurrent on file-I/O failure.
    # T8 0x08 INTERIM + T9 papp CHANGED read live values from
    # y1-track-info[795..796].
    a.raw(bytes([PAPP_REPEAT_OFF, PAPP_SHUFFLE_OFF]))
    a.align(4)

    # PApp UTF-8 attribute / value text strings (charset 0x006A).
    a.label("papp_text_repeat")
    a.raw(b"Repeat")                          # 6 B, no null terminator (length passed explicitly)
    a.align(4)
    a.label("papp_text_shuffle")
    a.raw(b"Shuffle")                         # 7 B
    a.align(4)
    a.label("papp_text_off")
    a.raw(b"Off")                             # 3 B
    a.align(4)
    a.label("papp_text_single")
    a.raw(b"Single Track")                    # 12 B
    a.align(4)
    a.label("papp_text_all")
    a.raw(b"All Tracks")                      # 10 B
    a.align(4)

    blob = a.resolve()
    addrs = {k: v for k, v in a.labels.items()
             if k not in ("t4_to_unknown", "t4_to_epilogue",
                          "jni_get_avrcp_state")}
    return blob, addrs


if __name__ == "__main__":
    # LOAD #2 starts at file 0xbc08 in stock libextavrcp_jni.so; we can
    # extend LOAD #1 up to but not into LOAD #2, so the padding budget is
    # 0xbc08 - T4_VADDR = 4020 bytes.
    LOAD2_OFFSET = 0xbc08
    PADDING_BUDGET = LOAD2_OFFSET - T4_VADDR
    blob, addrs = build()
    print(f"blob length: {len(blob)} bytes  (LOAD #1 padding budget: {PADDING_BUDGET} bytes; "
          f"{PADDING_BUDGET - len(blob)} free)")
    print(f"final vaddr: 0x{T4_VADDR + len(blob):x}")
    print()
    print("labels:")
    for name, addr in sorted(addrs.items(), key=lambda kv: kv[1]):
        print(f"  0x{addr:06x}  {name}")

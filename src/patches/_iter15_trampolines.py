"""
iter16 trampoline assembly for libextavrcp_jni.so.

Emits two trampolines into the LOAD #1 page-padding area starting at vaddr
0xac54:

  T4 (GetElementAttributes, PDU 0x20):
    - Reads y1-track-info (776B: 8B track_id + 3 × 256B Title/Artist/Album)
    - Reads y1-trampoline-state (16B: last_synced track_id [0..7] + transId [8])
    - If state[0..7] != file[0..7] (the track has actually changed since the
      last CHANGED we emitted) → calls track_changed_rsp with reason=CHANGED,
      transId=state[8], **track_id=0xFF×8 (hardcoded sentinel)**, then writes
      file[0..7] back into state[0..7] so we don't re-emit until Y1MediaBridge
      moves the track_id again.
    - Replies with 3× get_element_attributes_rsp (Title/Artist/Album)

  extended_T2 (RegisterNotification(TRACK_CHANGED), PDU 0x31, event 0x02):
    - Reads y1-track-info first 8 bytes (track_id) into a stack buffer — used
      ONLY to seed state[0..7] so T4's first comparison succeeds; not sent to
      the peer.
    - Writes [file_track_id || transId || pad] to y1-trampoline-state.
    - Replies track_changed_rsp with reason=INTERIM, **track_id=0xFF×8
      (hardcoded sentinel)**.

  T2 stub at 0x72d4 is rewritten to a single `b.w extended_T2`.

iter16 design rationale: iter15 sent the file's real track_id in INTERIM,
which flipped Sonos into "stable identity, only refresh on CHANGED" mode.
T4 only fires when Sonos polls GetElementAttributes; Sonos won't poll until
it gets CHANGED — deadlock confirmed by 14 minutes of zero AVRCP traffic
during testing. iter16 keeps INTERIM/CHANGED's wire-level track_id at the
"no stable identity" sentinel so Sonos stays in the iter14c-style polling
mode (~50 GetElementAttributes per minute) AND adds CHANGED edges on real
track changes so Sonos invalidates its 0xFF×8-keyed cache and re-renders.

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

# Function-internal landmarks in saveRegEventSeqId.
EPILOGUE          = 0x712a   # mov r9,#1; canary check; pop {r4-r9, sl, fp, pc}
UNKNOW_INDICATION = 0x65bc   # original "unknow indication" path

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

    # ---- pre-check: only PDU 0x20 (GetElementAttributes) goes through us ----
    a.ldrb_w(0, 13, T4_PDU_OFF_ENTRY)         # r0 = PDU
    a.cmp_imm8(0, 0x20)
    a.beq("t4_main")
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
    # track_changed_rsp(conn, transId, REASON_CHANGED, &SENTINEL_FFx8)
    # iter16: track_id field is the 0xFF×8 sentinel, NOT the real track_id.
    # See top-of-file rationale.
    a.add_imm_t3(0, 5, 8)                     # r0 = conn
    a.ldrb_w(1, 13, T4_OFF_STATE + 8)         # r1 = state[8] = last register transId
    a.movs_imm8(2, REASON_CHANGED)
    a.adr_w(3, "sentinel_ffx8")               # r3 = &(8 bytes 0xFF)
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

    # ---- 3× get_element_attributes_rsp(conn, 0, transId, 0,
    #                                    [attr_id, charset=0x6a, len, ptr]) ----
    for label_suffix, attr_id, str_offset in (
        ("title",  0x01, T4_OFF_FILE_TITLE),
        ("artist", 0x02, T4_OFF_FILE_ARTIST),
        ("album",  0x03, T4_OFF_FILE_ALBUM),
    ):
        a.label(f"t4_reply_{label_suffix}")
        a.add_sp_imm(0, str_offset)           # r0 = string ptr
        a.blx_imm(PLT_strlen)                 # r0 = strlen
        a.mov_lo_lo(6, 0)                     # r6 = strlen

        a.add_imm_t3(0, 5, 8)                 # r0 = conn
        a.movs_imm8(1, 0)                     # r1 = 0 (string-follows flag)
        a.ldrb_w(2, 13, T4_TRANSID_OFF)       # r2 = transId
        a.movs_imm8(3, 0)                     # r3 = 0
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
    # iter16: the wire-level track_id is the 0xFF×8 sentinel (not the real
    # id we just stashed in state). See top-of-file rationale.
    a.add_imm_t3(0, 5, 8)                     # r0 = conn
    a.ldrb_w(1, 13, T2_TRANSID_CALLER_OFF)    # r1 = transId
    a.movs_imm8(2, REASON_INTERIM)
    a.adr_w(3, "sentinel_ffx8")               # r3 = &(8 bytes 0xFF)
    a.blx_imm(PLT_track_changed_rsp)

    # Restore stack and branch to epilogue.
    a.addw(13, 13, T2_FRAME)
    a.b_w("t4_to_epilogue")


def build() -> tuple[bytes, dict[str, int]]:
    """Build the iter15 LOAD-#1-padding code blob.

    Returns:
        (bytes, label_addresses)
        - bytes: the full assembled blob to splice in at vaddr T4_VADDR
        - label_addresses: dict of name → vaddr (so the patcher can wire the
          T2 stub at 0x72d4 to extended_T2)
    """
    a = Asm(T4_VADDR)

    # External landmarks — pre-register so b_w resolves them to absolute targets.
    a.labels["t4_to_unknown"] = UNKNOW_INDICATION
    a.labels["t4_to_epilogue"] = EPILOGUE

    _emit_t4(a)
    _emit_extended_t2(a)

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
             if k not in ("t4_to_unknown", "t4_to_epilogue")}
    return blob, addrs


if __name__ == "__main__":
    blob, addrs = build()
    print(f"blob length: {len(blob)} bytes  (LOAD #1 padding budget: 3712 bytes)")
    print(f"final vaddr: 0x{T4_VADDR + len(blob):x}")
    print()
    print("labels:")
    for name, addr in sorted(addrs.items(), key=lambda kv: kv[1]):
        print(f"  0x{addr:06x}  {name}")

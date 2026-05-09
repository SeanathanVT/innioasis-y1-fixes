package com.y1.mediabridge;

import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.ContentUris;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.database.Cursor;
import android.os.BatteryManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.AudioManager;
import android.media.MediaMetadataRetriever;
import android.media.MediaScannerConnection;
import android.media.RemoteControlClient;
import android.net.Uri;
import android.os.Binder;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Parcel;
import android.os.RemoteException;
import android.os.SystemClock;
import android.provider.MediaStore;
import android.util.Log;
import android.view.KeyEvent;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.UnsupportedEncodingException;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * MediaBridgeService — single-process AVRCP metadata bridge for the Innioasis Y1.
 *
 * WHY THIS EXISTS
 * ===============
 * Y1 stock player plays audio but does not publish metadata on the MtkBt AVRCP
 * path. MtkBt binds to "com.android.music.MediaPlaybackService" for metadata
 * and wraps the returned IBinder with two different AIDL proxies.
 *
 *   1. IBTAvrcpMusic  (descriptor com.mediatek.bluetooth.avrcp.IBTAvrcpMusic)
 *      Ground-truth transaction codes extracted from MtkBt.odex → MtkBt.dex,
 *      class com.mediatek.bluetooth.avrcp.IBTAvrcpMusic$Stub:
 *          1  registerCallback(IBTAvrcpMusicCallback)
 *          2  unregisterCallback(IBTAvrcpMusicCallback)
 *          3  regNotificationEvent(byte,int)       -> boolean
 *          4  setPlayerApplicationSettingValue(b,b)-> boolean
 *          5  getCapabilities()                    -> byte[]
 *          6  play()                               -> void
 *          7  stop()                               -> void
 *          8  pause()                              -> void
 *          9  resume()                             -> void
 *         10  prev()                               -> void
 *         11  next()                               -> void
 *         12  prevGroup()                          -> void
 *         13  nextGroup()                          -> void
 *         14  setEqualizeMode(int)                 -> boolean
 *         15  getEqualizeMode()                    -> int
 *         16  setShuffleMode(int)                  -> boolean
 *         17  getShuffleMode()                     -> int
 *         18  setRepeatMode(int)                   -> boolean
 *         19  getRepeatMode()                      -> int
 *         20  setScanMode(int)                     -> boolean
 *         21  getScanMode()                        -> int
 *         22  informDisplayableCharacterSet(int)   -> boolean
 *         23  informBatteryStatusOfCT()            -> boolean
 *         24  getPlayStatus()                      -> byte
 *         25  position()                           -> long
 *         26  duration()                           -> long
 *         27  getAudioId()                         -> long
 *         28  getTrackName()                       -> String
 *         29  getAlbumName()                       -> String
 *         30  getAlbumId()                         -> long
 *         31  getArtistName()                      -> String
 *         32  enqueue(long[], int)                 -> void
 *         33  getNowPlaying()                      -> long[]
 *         34  getNowPlayingItemName(long)          -> String
 *         35  open(long[], int)                    -> void
 *         36  getQueuePosition()                   -> int
 *         37  setQueuePosition(int)                -> void
 *
 *   2. IMediaPlaybackService  (descriptor com.android.music.IMediaPlaybackService)
 *      Ground truth from MtkBt.dex IMediaPlaybackService$Stub. MtkBt only reads
 *      these on its metadata path:
 *          4=isPlaying, 13=getTrackName, 14=getAlbumName, 16=getArtistName,
 *         24=getAudioId, 11=position, 10=duration, 15=getAlbumId, 17=getArtistId.
 *      (Setters and queue ops exist on the interface but MtkBt never calls them.)
 *
 * Both proxies call transact() on the SAME IBinder. The transaction codes
 * overlap by number but mean different things per interface (e.g. code 6 is
 * "pause" on IMediaPlaybackService but "play" on IBTAvrcpMusic). Therefore
 * onTransact dispatches on the interface token at the head of the Parcel,
 * not on the code alone.
 *
 * WHY EVERY DECLARED CODE NEEDS A WELL-FORMED REPLY
 * =================================================
 * Returning false from onTransact tells the kernel binder driver the code is
 * unknown; the caller's generated Stub.Proxy then throws RemoteException or
 * reads a malformed reply. On the MtkBt side this makes BTAvrcpMusicAdapter
 * swallow errors silently — in particular, if regNotificationEvent (code 3)
 * fails, mRegBit never populates and every subsequent notifyTrackChanged the
 * callback fires is dropped before the AVRCP packet is emitted. That is the
 * "[BT][AVRCP] onReceive EVENT_TRACK_CHANGED fail" symptom. So every declared
 * code returns true and writes writeNoException() plus a typed zero / empty / true
 * for the declared return type, even when we have no semantic answer.
 *
 * IBTAvrcpMusicCallback (outgoing calls MtkBt subscribes to via registerCallback).
 * Ground truth from IBTAvrcpMusicCallback$Stub:
 *    1=notifyPlaybackStatus(byte)  (1=stopped,2=playing,3=paused)
 *    2=notifyTrackChanged(long)
 *    3=notifyTrackReachStart
 *    4=notifyTrackReachEnd
 *    5=notifyPlaybackPosChanged
 *    6=notifyAppSettingChanged
 *    7=notifyNowPlayingContentChanged
 *    8=notifyVolumehanged(byte)
 *
 * ARCHITECTURE
 * ============
 * Installed at /system/app/Y1MediaBridge.apk, android:persistent="true" so
 * this survives low-memory kills. A background thread reads logcat directly
 * (READ_LOGS permission, granted to /system/app on Android 4.2) and parses
 * the Y1 player's debug lines to keep the track / state fields current. When
 * anything changes we (a) write RCC metadata and (b) fire the callbacks MtkBt
 * registered with us, which is what makes the car head unit update.
 *
 * NO AUDIO FOCUS
 * ==============
 * We deliberately do NOT call requestAudioFocus(). AUDIOFOCUS_GAIN would make
 * any focus-respecting player (including possibly the stock Y1 player) pause.
 * MtkBt queries us through the direct Binder above, so audio focus is not on
 * the critical path for metadata. RCC still registers for lockscreen use.
 */
public class MediaBridgeService extends Service {

    private static final String TAG = "Y1MediaBridge";

    public static final String ACTION_PLAY_SONG = "com.y1.mediabridge.PLAY_SONG";
    public static final String ACTION_SHUTDOWN  = "com.y1.mediabridge.SHUTDOWN";

    /** Max album-art dimension; larger bitmaps blow binder transaction size. */
    private static final int MAX_ART_PX = 512;

    private static final String DESCRIPTOR_AVRCP_MUSIC =
            "com.mediatek.bluetooth.avrcp.IBTAvrcpMusic";
    private static final String DESCRIPTOR_AVRCP_CALLBACK =
            "com.mediatek.bluetooth.avrcp.IBTAvrcpMusicCallback";
    private static final String DESCRIPTOR_MEDIA_PLAYBACK =
            "com.android.music.IMediaPlaybackService";

    // -----------------------------------------------------------------------
    // Y1 logcat patterns. BasePlayerActivity is a strict superstring of
    // BaseActivity — we test the longer tag first to avoid false matches.
    //
    //   D/DebugY1  BasePlayerActivity(...): 刷新一次歌词 /storage/sdcard0/Music/...
    //   D/DebugY1  BasePlayerActivity(...): 刷新一次专辑图 /storage/sdcard0/Music/...
    //   I/DebugY1  BaseActivity(...):       播放状态切换   1    (playing)
    //   I/DebugY1  BaseActivity(...):       播放状态切换   3    (paused)
    // -----------------------------------------------------------------------

    private static final String TAG_BASE_PLAYER   = "DebugY1  BasePlayerActivity";
    private static final String TAG_BASE_ACTIVITY = "DebugY1  BaseActivity";
    private static final String PREFIX_LYRICS     = "刷新一次歌词 ";
    private static final String PREFIX_ALBUM      = "刷新一次专辑图 ";
    private static final String PREFIX_STATE      = "播放状态切换";

    // -----------------------------------------------------------------------
    // Current track state. volatile because the Binder thread reads, the
    // main thread (and the logcat monitor via mMainHandler) writes.
    // -----------------------------------------------------------------------

    private volatile String  mCurrentPath     = "";
    private volatile String  mCurrentTitle    = "";
    private volatile String  mCurrentArtist   = "";
    private volatile String  mCurrentAlbum    = "";
    private volatile String  mCurrentGenre    = "";
    private volatile long    mCurrentDuration = 0;
    private volatile long    mCurrentAudioId  = -1;
    private volatile long    mCurrentAlbumId  = -1;
    private volatile long    mCurrentArtistId = -1;
    private volatile int     mCurrentTrackNumber = 0;
    private volatile int     mCurrentTotalTracks = 0;
    private volatile boolean mIsPlaying       = false;
    /** AVRCP §5.4.1 Tbl 5.26 PlayStatus enum, kept in lockstep with
     *  mIsPlaying (which stays a boolean for the IBTAvrcpMusicCallback
     *  contract that uses a different byte enum and for the
     *  IMediaPlaybackService.isPlaying return type). PLAYING=1 maps to
     *  mIsPlaying=true; STOPPED=0 and PAUSED=2 both map to mIsPlaying=false.
     *  Written to y1-track-info[792] by writeTrackInfoFile so T6 / T8 / T9
     *  carry the spec-correct three-valued state instead of collapsing
     *  STOPPED into PAUSED. */
    private volatile byte mPlayStatus = 0;        // 0=STOPPED at startup

    /** Position at last state change; with mStateChangeTime gives us a live
     *  running position estimate since the stock player never reports one. */
    private volatile long mPositionAtStateChange = 0;
    private volatile long mStateChangeTime       = 0;

    /** Whether the previous track ended naturally (position at duration) vs.
     *  was interrupted by a skip / stop / explicit pause+resume on a different
     *  track. Set in onTrackDetected by comparing the previous track's
     *  extrapolated position against its duration before mCurrent* fields are
     *  rewritten. Read by the AVRCP T5 trampoline (libextavrcp_jni.so) from
     *  y1-track-info[793] to gate emission of the AVRCP 1.3 §5.4.2 Tbl 5.31
     *  TRACK_REACHED_END (event 0x03) CHANGED frame — strict spec semantic
     *  is "natural-end-only", and a skip is meant to fire only TRACK_CHANGED
     *  + TRACK_REACHED_START. */
    private volatile boolean mPreviousTrackNaturalEnd = false;

    /** AVRCP §5.4.2 Tbl 5.34/5.35 BATT_STATUS bucket value:
     *  0=NORMAL, 1=WARNING, 2=CRITICAL, 3=EXTERNAL, 4=FULL_CHARGE.
     *  Updated by mBatteryReceiver on every `Intent.ACTION_BATTERY_CHANGED`
     *  bucket transition; written to y1-track-info[794] by writeTrackInfoFile.
     *  Read by the AVRCP T8 trampoline at INTERIM time (event 0x06
     *  RegisterNotification) and by T9 at every `playstatechanged`
     *  broadcast for CHANGED-on-edge dispatch. */
    private volatile byte mCurrentBatteryStatus = 0; // default = NORMAL

    /** BroadcastReceiver registered for Android's sticky `ACTION_BATTERY_CHANGED`.
     *  Held as a field so we can unregister cleanly in onDestroy. */
    private BroadcastReceiver mBatteryReceiver;

    /** 1-second-recurring tick that fires the `playstatechanged`
     *  broadcast while mIsPlaying. Drives T9's PLAYBACK_POS_CHANGED CHANGED
     *  emission (alongside the existing play / battery edge checks T9 already
     *  does). The tick stops when playback pauses / stops; the next play edge
     *  in onStateDetected restarts it. AVRCP 1.3 §5.4.2 Tbl 5.33. */
    private Runnable mPosTickRunnable;
    private static final long POS_TICK_INTERVAL_MS = 1000L;

    private Bitmap mCurrentAlbumArt;

    private AudioManager        mAudioManager;
    private RemoteControlClient mRemoteControlClient;
    private ComponentName       mMediaButtonReceiver;

    private final Handler mMainHandler = new Handler(Looper.getMainLooper());
    private LogcatMonitor mLogcatMonitor;

    /** Path currently being scanned — prevents duplicate MediaScanner requests
     *  when the player emits both a lyrics line and an album-art line for the
     *  same track before the first scan completes. Written / read on main thread. */
    private String mPendingScanPath = null;

    /** Callback IBinders registered by MtkBt via IBTAvrcpMusic.registerCallback. */
    private final CopyOnWriteArrayList<IBinder> mAvrcpCallbacks =
            new CopyOnWriteArrayList<IBinder>();

    // =======================================================================
    // The binder — single instance, dual-interface dispatch (see AvrcpBinder)
    // =======================================================================

    /**
     * The binder — single instance, dual-interface dispatch.
     *
     * Plain Binder subclass — deliberately does NOT call attachInterface and
     * does NOT implement IInterface. This ensures IBTAvrcpMusic.Stub.asInterface()
     * always takes the remote Proxy path: queryLocalInterface returns null →
     * asInterface wraps us in a Proxy → all calls including registerCallback
     * arrive as binder transactions at onTransact where our handlers live.
     *
     * History: attachInterface was added (versionCode 7) hoping to populate
     * queryLocalInterface so asInterface() takes the local path. It did — but
     * the local path casts the result to IBTAvrcpMusic, which AvrcpBinder does
     * not implement. MtkBt's registerCallback call hit a missing method and was
     * silently swallowed, leaving callbacks=0 permanently (confirmed by
     * notifyPlaybackStatus callbacks=0 throughout the versionCode 7 session).
     * Removing attachInterface forces the Proxy path for all calls.
     */
    private final class AvrcpBinder extends Binder {
        AvrcpBinder() {
            // No attachInterface — see class javadoc.
        }

        @Override
        public String getInterfaceDescriptor() {
            return DESCRIPTOR_AVRCP_MUSIC;
        }

        @Override
        protected boolean onTransact(int code, Parcel data, Parcel reply, int flags)
                throws RemoteException {
            // Log.e so this is visible regardless of log-level filtering.
            Log.e(TAG, "onTransact: code=" + code + " pid=" + getCallingPid()
                    + " uid=" + getCallingUid());
            if (code == INTERFACE_TRANSACTION) {
                return super.onTransact(code, data, reply, flags);
            }

            // Skip the interface token header unconditionally rather than
            // enforcing it. Every previous dispatch strategy (peek+rewind,
            // try/enforceInterface/catch) failed because the descriptor string
            // MtkBt writes may differ from what we expect — different package
            // path, ROM variation, or encoding difference — causing all
            // transactions including registerCallback (code 1) to fall through
            // to super.onTransact() and return false, silently aborting
            // registration and leaving cardinality permanently at 0.
            //
            // writeInterfaceToken writes:
            //   int32  strictModePolicy
            //   string descriptor (int32 charCount + UTF-16 chars, 4-byte aligned)
            // We consume both to advance the cursor to the first argument byte,
            // then dispatch on code number alone. Both interfaces share this
            // layout so the skip is correct for either caller.
            data.readInt();    // strictModePolicy — discard
            data.readString(); // descriptor — discard

            // IBTAvrcpMusic covers codes 1–37. IMediaPlaybackService codes
            // (4, 10–17, 24) all fall within that range and are handled there
            // with compatible return types, so route everything 1–37 to the
            // AVRCP handler. Anything outside that range falls to the media
            // playback handler (defensive; MtkBt shouldn't send those here).
            if (code >= 1 && code <= 37) {
                return handleAvrcpMusic(code, data, reply);
            }
            return handleMediaPlayback(code, data, reply);
        }
    }

    private final AvrcpBinder mBinder = new AvrcpBinder();

    // -----------------------------------------------------------------------
    // IBTAvrcpMusic dispatch — every declared code returns a well-formed reply
    // even when we have no semantic answer, because returning false / garbage
    // makes MtkBt's BTAvrcpMusicAdapter abort registration (see class doc).
    // -----------------------------------------------------------------------

    private boolean handleAvrcpMusic(int code, Parcel data, Parcel reply)
            throws RemoteException {
        switch (code) {
            case 1: { // registerCallback(IBTAvrcpMusicCallback cb)
                // On the local binder path this is called directly by
                // BTAvrcpMusicAdapter.onServiceConnected — no onTransact involved.
                // On the remote path it arrives via onTransact code 1.
                IBinder cb = data.readStrongBinder();
                Log.d(TAG, "IBTAvrcpMusic.registerCallback cb=" + cb
                        + " pid=" + android.os.Binder.getCallingPid()
                        + " uid=" + android.os.Binder.getCallingUid());
                if (cb != null && !mAvrcpCallbacks.contains(cb)) {
                    mAvrcpCallbacks.add(cb);
                    byte cbStatus = callbackPlayStatusByte();
                    Log.d(TAG, "IBTAvrcpMusic.registerCallback registered total="
                            + mAvrcpCallbacks.size()
                            + " — pushing cbStatus=" + cbStatus
                            + " avrcpStatus=" + mPlayStatus
                            + " audioId=" + mCurrentAudioId);
                    notifyAvrcpCallbacks(1, cbStatus);
                    notifyAvrcpCallbacks(2, mCurrentAudioId);
                } else {
                    Log.w(TAG, "IBTAvrcpMusic.registerCallback: cb="
                            + (cb == null ? "null" : "duplicate"));
                }
                if (reply != null) reply.writeNoException();
                return true;
            }
            case 2: { // unregisterCallback(IBTAvrcpMusicCallback cb)
                IBinder cb = data.readStrongBinder();
                boolean removed = cb != null && mAvrcpCallbacks.remove(cb);
                Log.d(TAG, "IBTAvrcpMusic.unregisterCallback cb=" + cb
                        + " removed=" + removed + " remaining=" + mAvrcpCallbacks.size());
                if (reply != null) reply.writeNoException();
                return true;
            }

            case 3: { // regNotificationEvent(byte eventId, int param) -> boolean
                // CRITICAL: Called by BTAvrcpMusicAdapter.registerNotification
                // when the car subscribes to EVENT_TRACK_CHANGED (0x05),
                // EVENT_PLAYBACK_STATUS_CHANGED (0x01), etc. Returning false
                // leaves MtkBt's mRegBit empty and every later notifyTrackChanged
                // gets dropped before the AVRCP packet is emitted. We always
                // ack success (true). The adapter owns the bitset, not us.
                byte eventId = data.readByte();
                int  param   = data.readInt();
                Log.d(TAG, "IBTAvrcpMusic.regNotificationEvent event=0x"
                        + Integer.toHexString(eventId & 0xff) + " param=" + param);
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;
            }

            case 4: { // setPlayerApplicationSettingValue(byte attr, byte val) -> boolean
                // Read args so the parcel is consumed; we don't apply them.
                try { data.readByte(); data.readByte(); } catch (Exception ignored) {}
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;
            }

            case 5: { // getCapabilities() -> byte[]
                // MtkBt stashes this in mCapabilities and the car reads it
                // during GET_CAPABILITIES to decide which events to register
                // for via REGISTER_NOTIFICATION. An empty array is legal per
                // our AIDL contract, but some AVRCP 1.3 CTs interpret it as
                // "TG supports no events" and skip REGISTER_NOTIFICATION
                // entirely — leaving cardinality:0 and no metadata flow.
                //
                // Return the two mandatory AVRCP event IDs:
                //   0x01 = EVENT_PLAYBACK_STATUS_CHANGED
                //   0x02 = EVENT_TRACK_CHANGED
                // These are the only events MtkBt's BTAvrcpMusicAdapter
                // notifies us about (via notifyPlaybackStatus and
                // notifyTrackChanged), so this is both necessary and sufficient.
                Log.d(TAG, "IBTAvrcpMusic.getCapabilities called → returning [0x01, 0x02]"
                        + " pid=" + android.os.Binder.getCallingPid());
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeByteArray(new byte[]{ 0x01, 0x02 });
                }
                return true;
            }

            // Transport commands — forward as media keys to the stock player.
            // Note: IBTAvrcpMusic code 6 = play, NOT pause (differs from
            // IMediaPlaybackService which uses 6=pause, 7=play).
            //
            // We route play/pause/resume through KEYCODE_MEDIA_PLAY_PAUSE
            // (85) instead of distinct MEDIA_PLAY (126) / MEDIA_PAUSE (127).
            // The Y1 player's `PlayControllerReceiver` matches against
            // `KeyMap.KEY_PLAY` which is hardwired to 85 (KEYCODE_MEDIA_PLAY_PAUSE);
            // it has no native handler for 126 or 127. Hitting it with
            // PLAY_PAUSE always toggles, which is what we want for both
            // play→pause and pause→play transitions. (Patch E in
            // patch_y1_apk.py separately teaches PlayControllerReceiver to
            // recognize 126/127 too, covering the libextavrcp_jni.so/uinput
            // injection path that bypasses Y1MediaBridge entirely.)
            case 6:  Log.d(TAG, "IBTAvrcpMusic.play → KEYCODE_MEDIA_PLAY_PAUSE");   return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE);
            case 7:  Log.d(TAG, "IBTAvrcpMusic.stop → KEYCODE_MEDIA_STOP");         return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_STOP);
            case 8:  Log.d(TAG, "IBTAvrcpMusic.pause → KEYCODE_MEDIA_PLAY_PAUSE");  return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE);
            case 9:  Log.d(TAG, "IBTAvrcpMusic.resume → KEYCODE_MEDIA_PLAY_PAUSE"); return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE);
            case 10: Log.d(TAG, "IBTAvrcpMusic.prev → KEYCODE_MEDIA_PREVIOUS");     return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PREVIOUS);
            case 11: Log.d(TAG, "IBTAvrcpMusic.next → KEYCODE_MEDIA_NEXT");         return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_NEXT);
            case 12: Log.d(TAG, "IBTAvrcpMusic.prevGroup → KEYCODE_MEDIA_PREVIOUS"); return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PREVIOUS);
            case 13: Log.d(TAG, "IBTAvrcpMusic.nextGroup → KEYCODE_MEDIA_NEXT");     return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_NEXT);

            // Setter/getter pairs for player-app settings. Setters return
            // boolean success; getters return int (zero = not-applicable).
            case 14: // setEqualizeMode(int) -> boolean
            case 16: // setShuffleMode(int)  -> boolean
            case 18: // setRepeatMode(int)   -> boolean
            case 20: // setScanMode(int)     -> boolean
                try { data.readInt(); } catch (Exception ignored) {}
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;
            case 15: // getEqualizeMode()  -> int
            case 17: // getShuffleMode()   -> int
            case 19: // getRepeatMode()    -> int
            case 21: // getScanMode()      -> int
            case 36: // getQueuePosition() -> int
                if (reply != null) { reply.writeNoException(); reply.writeInt(0); }
                return true;

            case 22: // informDisplayableCharacterSet(int) -> boolean
            case 23: // informBatteryStatusOfCT()         -> boolean
                if (code == 22) { try { data.readInt(); } catch (Exception ignored) {} }
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;

            case 24: // getPlayStatus() -> byte (1=stopped, 2=playing, 3=paused)
                if (reply != null) {
                    byte status = callbackPlayStatusByte();
                    Log.v(TAG, "IBTAvrcpMusic.getPlayStatus → " + status);
                    reply.writeNoException();
                    reply.writeByte(status);
                }
                return true;
            case 25: // position() -> long
                if (reply != null) {
                    long pos = computePosition();
                    Log.v(TAG, "IBTAvrcpMusic.position → " + pos);
                    reply.writeNoException(); reply.writeLong(pos);
                }
                return true;
            case 26: // duration() -> long
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.duration → " + mCurrentDuration);
                    reply.writeNoException(); reply.writeLong(mCurrentDuration);
                }
                return true;
            case 27: // getAudioId() -> long
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.getAudioId → " + mCurrentAudioId);
                    reply.writeNoException(); reply.writeLong(mCurrentAudioId);
                }
                return true;
            case 28: // getTrackName() -> String
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.getTrackName → " + safeString(mCurrentTitle));
                    reply.writeNoException(); reply.writeString(safeString(mCurrentTitle));
                }
                return true;
            case 29: // getAlbumName() -> String
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.getAlbumName → " + safeString(mCurrentAlbum));
                    reply.writeNoException(); reply.writeString(safeString(mCurrentAlbum));
                }
                return true;
            case 30: // getAlbumId() -> long (IBTAvrcpMusic numbering)
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.getAlbumId → " + mCurrentAlbumId);
                    reply.writeNoException(); reply.writeLong(mCurrentAlbumId);
                }
                return true;
            case 31: // getArtistName() -> String
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.getArtistName → " + safeString(mCurrentArtist));
                    reply.writeNoException(); reply.writeString(safeString(mCurrentArtist));
                }
                return true;

            case 32: // enqueue(long[], int) -> void
            case 35: // open(long[], int)    -> void
                try { data.createLongArray(); data.readInt(); } catch (Exception ignored) {}
                if (reply != null) reply.writeNoException();
                return true;

            case 33: // getNowPlaying() -> long[]
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeLongArray(new long[0]);
                }
                return true;

            case 34: // getNowPlayingItemName(long) -> String
                try { data.readLong(); } catch (Exception ignored) {}
                if (reply != null) { reply.writeNoException(); reply.writeString(""); }
                return true;

            case 37: // setQueuePosition(int) -> void
                try { data.readInt(); } catch (Exception ignored) {}
                if (reply != null) reply.writeNoException();
                return true;

            default:
                // Unknown codes — delegate to super so INTERFACE_TRANSACTION
                // and DUMP / PING etc. still work.
                Log.v(TAG, "IBTAvrcpMusic: unhandled code=" + code
                        + " — falling through to super");
                return false;
        }
    }

    private boolean avrcpAck(Parcel data, Parcel reply, int keyCode)
            throws RemoteException {
        // All void transport methods still must consume the interface token
        // and write writeNoException so the caller's readException sees a
        // clean reply parcel.
        sendMediaKey(keyCode);
        if (reply != null) reply.writeNoException();
        return true;
    }

    // -----------------------------------------------------------------------
    // IMediaPlaybackService dispatch (AOSP-style interface as packaged inside
    // MtkBt.dex — codes verified against com.android.music.IMediaPlaybackService$Stub).
    //
    // MtkBt's BTAvrcpMusicAdapter also wraps our binder with
    // IMediaPlaybackService.Stub.asInterface() for the metadata read path.
    // Earlier hand-derived code tables had a few codes off — corrected here
    // from the authoritative TRANSACTION_* fields extracted from the DEX.
    // -----------------------------------------------------------------------

    private boolean handleMediaPlayback(int code, Parcel data, Parcel reply)
            throws RemoteException {
        switch (code) {
            case 4:  // isPlaying() -> int (0/1)
                Log.v(TAG, "IMediaPlayback.isPlaying → " + mIsPlaying);
                if (reply != null) { reply.writeNoException(); reply.writeInt(mIsPlaying ? 1 : 0); }
                return true;
            case 10: // duration() -> long
                Log.v(TAG, "IMediaPlayback.duration → " + mCurrentDuration);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentDuration); }
                return true;
            case 11: // position() -> long
                Log.v(TAG, "IMediaPlayback.position → " + computePosition());
                if (reply != null) { reply.writeNoException(); reply.writeLong(computePosition()); }
                return true;
            case 13: // getTrackName() -> String
                Log.v(TAG, "IMediaPlayback.getTrackName → " + safeString(mCurrentTitle));
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentTitle)); }
                return true;
            case 14: // getAlbumName() -> String
                Log.v(TAG, "IMediaPlayback.getAlbumName → " + safeString(mCurrentAlbum));
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentAlbum)); }
                return true;
            case 15: // getAlbumId() -> long
                Log.v(TAG, "IMediaPlayback.getAlbumId → " + mCurrentAlbumId);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentAlbumId); }
                return true;
            case 16: // getArtistName() -> String
                Log.v(TAG, "IMediaPlayback.getArtistName → " + safeString(mCurrentArtist));
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentArtist)); }
                return true;
            case 17: // getArtistId() -> long
                Log.v(TAG, "IMediaPlayback.getArtistId → " + mCurrentArtistId);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentArtistId); }
                return true;
            case 24: // getAudioId() -> long
                Log.v(TAG, "IMediaPlayback.getAudioId → " + mCurrentAudioId);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentAudioId); }
                return true;
            default:
                // MtkBt only queries the codes above on the metadata path,
                // but other codes might leak through from legacy paths. Ack
                // everything else as a no-op void so the RPC succeeds.
                Log.v(TAG, "IMediaPlaybackService: ack-only code=" + code);
                if (reply != null) reply.writeNoException();
                return true;
        }
    }

    // -----------------------------------------------------------------------
    // Outgoing IBTAvrcpMusicCallback invocations to all registered MtkBt
    // callback binders. These are what actually make AVRCP events fire.
    // -----------------------------------------------------------------------

    private void notifyAvrcpCallbacks(int code, Object... args) {
        if (mAvrcpCallbacks.isEmpty()) {
            Log.v(TAG, "notifyAvrcpCallbacks code=" + code + " — no callbacks registered");
            return;
        }
        // Build a readable arg summary for logging
        StringBuilder argStr = new StringBuilder();
        if (args != null) {
            for (Object a : args) argStr.append(a).append(' ');
        }
        Log.d(TAG, "notifyAvrcpCallbacks code=" + code
                + " args=[" + argStr.toString().trim() + "]"
                + " targets=" + mAvrcpCallbacks.size());
        for (IBinder cb : mAvrcpCallbacks) {
            Parcel data  = Parcel.obtain();
            Parcel reply = Parcel.obtain();
            try {
                data.writeInterfaceToken(DESCRIPTOR_AVRCP_CALLBACK);
                if (args != null) {
                    for (Object arg : args) {
                        if (arg instanceof Byte)         data.writeByte((Byte) arg);
                        else if (arg instanceof Long)    data.writeLong((Long) arg);
                        else if (arg instanceof Integer) data.writeInt((Integer) arg);
                    }
                }
                int rc = cb.transact(code, data, reply, 0) ? 0 : -1;
                Log.v(TAG, "notifyAvrcpCallbacks code=" + code + " cb=" + cb + " rc=" + rc);
            } catch (RemoteException e) {
                Log.w(TAG, "AVRCP callback transact failed code=" + code
                        + " cb=" + cb + " — dropping: " + e);
                mAvrcpCallbacks.remove(cb);
            } finally {
                reply.recycle();
                data.recycle();
            }
        }
    }

    /** Maps the AVRCP §5.4.1 Tbl 5.26 enum (`mPlayStatus`) to the
     *  IBTAvrcpMusicCallback contract's notifyPlaybackStatus byte:
     *    AVRCP 0 STOPPED → cb 1 (stopped)
     *    AVRCP 1 PLAYING → cb 2 (playing)
     *    AVRCP 2 PAUSED  → cb 3 (paused)
     *  Anything outside [0..2] collapses to PAUSED (cb 3) — defensive
     *  default that matches the pre-three-state-coverage behavior. */
    private byte callbackPlayStatusByte() {
        switch (mPlayStatus) {
            case 0: return 1;   // STOPPED
            case 1: return 2;   // PLAYING
            case 2: return 3;   // PAUSED
            default: return 3;
        }
    }

    private void notifyPlaybackStatus(byte status) {
        Log.d(TAG, "notifyPlaybackStatus status=" + status
                + " (" + (status == 2 ? "playing" : status == 3 ? "paused" : "stopped") + ")"
                + " callbacks=" + mAvrcpCallbacks.size());
        notifyAvrcpCallbacks(1, status);
    }
    private void notifyTrackChanged(long id) {
        Log.d(TAG, "notifyTrackChanged id=" + id
                + " title=" + safeString(mCurrentTitle)
                + " callbacks=" + mAvrcpCallbacks.size());
        notifyAvrcpCallbacks(2, id);
    }

    /** Live position estimate. Approximate since the Y1 player emits no
     *  position updates, but accurate enough for head-unit scrub bars. */
    private long computePosition() {
        if (!mIsPlaying) return mPositionAtStateChange;
        long elapsed = SystemClock.elapsedRealtime() - mStateChangeTime;
        long pos = mPositionAtStateChange + elapsed;
        if (mCurrentDuration > 0 && pos > mCurrentDuration) pos = mCurrentDuration;
        return pos;
    }

    // =======================================================================
    // Service lifecycle
    // =======================================================================

    @Override
    public void onCreate() {
        super.onCreate();
        Log.d(TAG, "MediaBridgeService created versionCode=15 (pid=" + android.os.Process.myPid()
                + " uid=" + android.os.Process.myUid() + ")");

        mAudioManager = (AudioManager) getSystemService(AUDIO_SERVICE);
        setupRemoteControlClient();
        startLogcatMonitor();
        prepareTrackInfoDir();
        registerBatteryReceiver();
    }

    /**
     * Register a BroadcastReceiver for `Intent.ACTION_BATTERY_CHANGED` and
     * also synchronously read the sticky broadcast so we have a value at
     * cold-boot before the first battery-change tick (which can be many
     * minutes away). On every level / plug bucket transition we update
     * mCurrentBatteryStatus, write y1-track-info, and fire a
     * `playstatechanged` broadcast — that wakes T9 (via the existing
     * cardinality NOP at MtkBt.odex:0x3c4fe → notificationPlayStatusChangedNative
     * → T9 in libextavrcp_jni.so), which now also checks the battery byte
     * and emits `BATT_STATUS_CHANGED` CHANGED on edge.
     *
     * Stock MtkBt's BTAvrcpSystemListener.onBatteryStatusChange dispatch
     * chain is dead (BTAvrcpMusicAdapter$2 overrides it with a log-only
     * stub), so we cannot drive `notificationBatteryStatusChangedNative`
     * via Android's system battery broadcast — reusing `playstatechanged`
     * as the trigger is the cheapest correct alternative.
     */
    private void registerBatteryReceiver() {
        IntentFilter filter = new IntentFilter(Intent.ACTION_BATTERY_CHANGED);
        mBatteryReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                handleBatteryIntent(intent, false);
            }
        };
        Intent sticky = registerReceiver(mBatteryReceiver, filter);
        // Sticky broadcast fires the receiver synchronously the first time
        // through registerReceiver if there's a cached value; on Android 4.2
        // it is also returned as the registerReceiver return value. Process
        // it directly so cold boot has a real bucket value before the next
        // ACTION_BATTERY_CHANGED tick.
        if (sticky != null) handleBatteryIntent(sticky, true);
    }

    /**
     * Bucket-map Android `ACTION_BATTERY_CHANGED` into the AVRCP §5.4.2
     * Tbl 5.35 enum and update mCurrentBatteryStatus + drive a CHANGED
     * emission on bucket transitions (or always, for the cold-boot pass).
     *
     * Mapping rationale:
     *   STATUS_FULL                            → 4 FULL_CHARGE
     *   PLUGGED (AC | USB | wireless)          → 3 EXTERNAL
     *   level <= 15                            → 2 CRITICAL
     *   level <= 30                            → 1 WARNING
     *   else                                   → 0 NORMAL
     * The `STATUS_FULL` test runs before plugged because some firmwares
     * report `plugged != 0` even when topped off; FULL_CHARGE is the more
     * informative value when both apply. Spec is permissive about the
     * exact thresholds.
     */
    private void handleBatteryIntent(Intent intent, boolean coldBoot) {
        int level   = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
        int scale   = intent.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
        int plugged = intent.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0);
        int status  = intent.getIntExtra(BatteryManager.EXTRA_STATUS,
                                          BatteryManager.BATTERY_STATUS_UNKNOWN);
        int pct = (level >= 0 && scale > 0) ? (level * 100 / scale) : -1;

        byte bucket;
        if (status == BatteryManager.BATTERY_STATUS_FULL) {
            bucket = 4;       // FULL_CHARGE
        } else if (plugged != 0) {
            bucket = 3;       // EXTERNAL
        } else if (pct >= 0 && pct <= 15) {
            bucket = 2;       // CRITICAL
        } else if (pct >= 0 && pct <= 30) {
            bucket = 1;       // WARNING
        } else {
            bucket = 0;       // NORMAL
        }

        if (!coldBoot && bucket == mCurrentBatteryStatus) {
            // Same bucket as last tick — no-op to avoid spamming
            // `playstatechanged` broadcasts (and AVRCP wire CHANGED emits)
            // on every percent-level change.
            return;
        }
        Log.d(TAG, "Battery: pct=" + pct + " plugged=" + plugged
                + " status=" + status + " → AVRCP bucket=" + bucket
                + (coldBoot ? " (cold boot)" : ""));
        mCurrentBatteryStatus = bucket;
        // Persist new bucket before triggering the trampoline path so T9
        // sees fresh file[794] when notificationPlayStatusChangedNative
        // fires.
        writeTrackInfoFile();
        if (!coldBoot) {
            // Fire a `playstatechanged` broadcast to drive T9 → BATT_STATUS_CHANGED
            // CHANGED emission. T9 reads file[792] vs state[9] (play_status,
            // unchanged here so no spurious play emit) AND file[794] vs
            // state[10] (battery, just changed → emit CHANGED).
            sendMusicBroadcast("com.android.music.playstatechanged");
            notifyPlaybackStatus(callbackPlayStatusByte());
        }
    }

    /**
     * Make our private files dir traversable by other uids so the AVRCP T4
     * trampoline (running in the Bluetooth process, uid bluetooth) can open
     * /data/data/com.y1.mediabridge/files/y1-track-info.
     *
     * Also ensure y1-trampoline-state exists, owned by us but world-rw, so
     * the BT process can stash its "last seen track_id" + "last register
     * transId" between AVRCP exchanges. See docs/ARCHITECTURE.md for the
     * trampoline-state schema.
     *
     * Default Android filesDir mode is 0700 (owner-only). We chmod to add
     * world execute (traversal). The file we write inside is then made
     * world-readable via setReadable(true, false).
     */
    private void prepareTrackInfoDir() {
        try {
            File dir = getFilesDir();
            if (dir == null) return;
            boolean ok = dir.setExecutable(true, false);
            Log.d(TAG, "prepareTrackInfoDir: setExecutable on " + dir.getPath()
                    + " → " + ok);

            // Create y1-trampoline-state if missing. 16 zero bytes:
            //   bytes 0..7  = last_seen_track_id (0 forces a CHANGED on first
            //                 GetElementAttributes, which strict CTs may drop
            //                 as bogus if transId is also 0 — but real
            //                 subscriptions update transId before T4 ever fires)
            //   byte  8     = last RegisterNotification transId
            //   bytes 9..15 = padding
            File state = new File(dir, TRAMPOLINE_STATE_FILENAME);
            if (!state.exists()) {
                FileOutputStream fos = new FileOutputStream(state);
                try { fos.write(new byte[STATE_LEN]); } finally { fos.close(); }
            }
            // World rw: BT process (uid bluetooth) must read AND write.
            state.setReadable(true, false);
            state.setWritable(true, false);
        } catch (Throwable t) {
            Log.w(TAG, "prepareTrackInfoDir: " + t);
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        Log.d(TAG, "onBind: " + (intent != null ? intent.getAction() : "null"));
        return mBinder;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null) {
            String action = intent.getAction();
            Log.d(TAG, "onStartCommand: " + action);
            if (ACTION_SHUTDOWN.equals(action)) {
                // ACTION_SHUTDOWN means "music app is going away" — that
                // maps to AVRCP STOPPED (0x00), not PAUSED (0x02). Pre-fix
                // this collapsed to PAUSED on the wire and onto the
                // IBTAvrcpMusicCallback as cb=3 (paused), so a strict CT
                // saw "paused" indefinitely after shutdown rather than
                // "stopped".
                mPlayStatus = 0;
                mIsPlaying = false;
                mStateChangeTime = SystemClock.elapsedRealtime();
                // writeTrackInfoFile before any cross-process broadcast so
                // T6 GetPlayStatus polls and T9 PLAYBACK_STATUS_CHANGED
                // CHANGED emits both see the new playing_flag. Same
                // ordering as the onStateDetected play/pause path.
                writeTrackInfoFile();
                publishState();
                sendMusicBroadcast("com.android.music.playstatechanged");
                notifyPlaybackStatus(callbackPlayStatusByte());
                cancelPosTick();
            }
            // ACTION_PLAY_SONG: logcat monitor handles the actual state change.
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        Log.d(TAG, "MediaBridgeService destroyed");
        if (mLogcatMonitor != null) mLogcatMonitor.shutdown();
        if (mRemoteControlClient != null) {
            mAudioManager.unregisterRemoteControlClient(mRemoteControlClient);
        }
        if (mMediaButtonReceiver != null) {
            mAudioManager.unregisterMediaButtonEventReceiver(mMediaButtonReceiver);
        }
        if (mBatteryReceiver != null) {
            try { unregisterReceiver(mBatteryReceiver); }
            catch (IllegalArgumentException ignore) { }
            mBatteryReceiver = null;
        }
        cancelPosTick();
        if (mCurrentAlbumArt != null && !mCurrentAlbumArt.isRecycled()) {
            mCurrentAlbumArt.recycle();
        }
        super.onDestroy();
    }

    // =======================================================================
    // RemoteControlClient — not on MtkBt's critical path, but we keep it
    // registered for lockscreen / system UI. NO audio focus is requested
    // because AUDIOFOCUS_GAIN would tell any focus-respecting player
    // (possibly including the stock Y1 player) to pause.
    // =======================================================================

    private void setupRemoteControlClient() {
        mMediaButtonReceiver = new ComponentName(getPackageName(),
                PlaySongReceiver.class.getName());
        mAudioManager.registerMediaButtonEventReceiver(mMediaButtonReceiver);

        Intent mediaButtonIntent = new Intent(Intent.ACTION_MEDIA_BUTTON);
        mediaButtonIntent.setComponent(mMediaButtonReceiver);
        PendingIntent mediaPendingIntent = PendingIntent.getBroadcast(
                this, 0, mediaButtonIntent, 0);

        mRemoteControlClient = new RemoteControlClient(mediaPendingIntent);
        mRemoteControlClient.setTransportControlFlags(
                RemoteControlClient.FLAG_KEY_MEDIA_PLAY
              | RemoteControlClient.FLAG_KEY_MEDIA_PAUSE
              | RemoteControlClient.FLAG_KEY_MEDIA_PLAY_PAUSE
              | RemoteControlClient.FLAG_KEY_MEDIA_NEXT
              | RemoteControlClient.FLAG_KEY_MEDIA_PREVIOUS
              | RemoteControlClient.FLAG_KEY_MEDIA_STOP);
        mAudioManager.registerRemoteControlClient(mRemoteControlClient);

        publishState();
        Log.d(TAG, "RemoteControlClient registered (no audio focus request)");
    }

    private void publishMetadata() {
        if (mRemoteControlClient == null) return;
        RemoteControlClient.MetadataEditor editor =
                mRemoteControlClient.editMetadata(true);
        editor.putString(MediaMetadataRetriever.METADATA_KEY_TITLE,  safeString(mCurrentTitle));
        editor.putString(MediaMetadataRetriever.METADATA_KEY_ARTIST, safeString(mCurrentArtist));
        editor.putString(MediaMetadataRetriever.METADATA_KEY_ALBUM,  safeString(mCurrentAlbum));
        editor.putLong(MediaMetadataRetriever.METADATA_KEY_DURATION, mCurrentDuration);
        if (mCurrentAlbumArt != null) {
            // Bitmap must be immutable for binder transport in some ROMs.
            Bitmap immutable = mCurrentAlbumArt.copy(mCurrentAlbumArt.getConfig(), false);
            editor.putBitmap(RemoteControlClient.MetadataEditor.BITMAP_KEY_ARTWORK, immutable);
        }
        editor.apply();
        Log.d(TAG, "RCC metadata: " + mCurrentTitle + " / " + mCurrentArtist
                + (mCurrentAlbumArt != null ? " [art]" : ""));
    }

    private void publishState() {
        if (mRemoteControlClient == null) return;
        // Single-argument setPlaybackState(int) — the (state, pos, speed)
        // overload was added in API 18 and we target API 17. Map the
        // three-valued AVRCP enum (mPlayStatus) into the matching
        // RemoteControlClient.PLAYSTATE_* constants. STOPPED is its own
        // RCC state (PLAYSTATE_STOPPED) — pre-fix it collapsed to
        // PLAYSTATE_PAUSED, so any locally-listening RCC consumer (lock
        // screen, system media UI, etc.) saw "paused" indefinitely after
        // a STOPPED transition.
        int state;
        String label;
        switch (mPlayStatus) {
            case 1: state = RemoteControlClient.PLAYSTATE_PLAYING; label = "PLAYING"; break;
            case 2: state = RemoteControlClient.PLAYSTATE_PAUSED;  label = "PAUSED";  break;
            case 0:
            default: state = RemoteControlClient.PLAYSTATE_STOPPED; label = "STOPPED"; break;
        }
        mRemoteControlClient.setPlaybackState(state);
        Log.d(TAG, "RCC state: " + label);
    }

    // =======================================================================
    // Logcat monitor — reads Y1 player debug lines directly from our process.
    // Requires READ_LOGS, granted to /system/app on Android 4.2 without a
    // platform signature.
    // =======================================================================

    private void startLogcatMonitor() {
        mLogcatMonitor = new LogcatMonitor();
        mLogcatMonitor.start();
    }

    private class LogcatMonitor extends Thread {
        private volatile boolean mRunning = true;
        private java.lang.Process mProcess;   // fully-qualified to disambiguate

        LogcatMonitor() {
            super("Y1-LogcatMonitor");
            setDaemon(true);
        }

        void shutdown() {
            mRunning = false;
            if (mProcess != null) mProcess.destroy();
            interrupt();
        }

        @Override
        public void run() {
            Log.d(TAG, "Logcat monitor thread started");
            // History pass first so we resync if the player was already
            // playing when we started. Then switch to live tail.
            processLogcat(new String[]{ "logcat", "-v", "tag", "-d" });
            Log.d(TAG, "Logcat history processed");
            while (mRunning) {
                processLogcat(new String[]{ "logcat", "-v", "tag" });
                if (mRunning) {
                    Log.w(TAG, "Logcat pipe closed, restarting in 1s");
                    try { Thread.sleep(1000); } catch (InterruptedException e) { break; }
                }
            }
            Log.d(TAG, "Logcat monitor thread exiting");
        }

        private void processLogcat(String[] args) {
            try {
                mProcess = Runtime.getRuntime().exec(args);
                BufferedReader reader = new BufferedReader(
                        new InputStreamReader(mProcess.getInputStream()));
                String line;
                while (mRunning && (line = reader.readLine()) != null) {
                    processLogLine(line);
                }
                reader.close();
                if (mProcess != null) {
                    mProcess.destroy();
                    mProcess = null;
                }
            } catch (Exception e) {
                Log.e(TAG, "Logcat read error: " + e.getMessage());
            }
        }
    }

    /** Dispatches mutations to the main thread so RCC & binder state aren't
     *  poked from the logcat reader thread. */
    private void processLogLine(String line) {
        if (line == null) return;

        // Check BasePlayerActivity first — "BaseActivity" is a substring of
        // it, so the BaseActivity match would otherwise fire on player lines.
        if (line.contains(TAG_BASE_PLAYER)) {
            int idx = line.indexOf(PREFIX_LYRICS);
            if (idx < 0) idx = line.indexOf(PREFIX_ALBUM);
            if (idx < 0) return;
            String prefix = line.startsWith(PREFIX_LYRICS, idx)
                    ? PREFIX_LYRICS : PREFIX_ALBUM;
            String path = line.substring(idx + prefix.length()).trim();
            if (path.isEmpty() || path.charAt(0) != '/') return;
            final String fPath = path;
            mMainHandler.post(new Runnable() {
                @Override public void run() { onTrackDetected(fPath); }
            });
            return;
        }

        if (line.contains(TAG_BASE_ACTIVITY)) {
            int idx = line.indexOf(PREFIX_STATE);
            if (idx < 0) return;
            int pos = idx + PREFIX_STATE.length();
            while (pos < line.length()
                    && (line.charAt(pos) == ' ' || line.charAt(pos) == '\t')) pos++;
            if (pos >= line.length()) return;
            char stateChar = line.charAt(pos);
            // Y1 BaseActivity emits state-code chars after `播放状态切换 `
            // ("playback state switch"). Observed mapping:
            //   '1' → PLAYING (audio rolling)
            //   '3' → PAUSED  (audio held; can resume)
            //   '5' → STOPPED (FF/RW cascade terminated, end-of-stream, etc.)
            // AVRCP 1.3 §5.4.1 Tbl 5.26 PlayStatus enum:
            //   0=STOPPED, 1=PLAYING, 2=PAUSED, 3=FWD_SEEK, 4=REV_SEEK,
            //   0xFF=ERROR. We currently map only the three states Y1
            //   emits; FWD_SEEK / REV_SEEK could be added if Y1 is
            //   observed emitting a hold-key state code, but the test
            //   matrix has not surfaced one yet.
            final byte avrcpStatus;
            if      (stateChar == '1') avrcpStatus = 1;   // PLAYING
            else if (stateChar == '3') avrcpStatus = 2;   // PAUSED
            else if (stateChar == '5') avrcpStatus = 0;   // STOPPED
            else                       return;
            mMainHandler.post(new Runnable() {
                @Override public void run() { onStateDetected(avrcpStatus); }
            });
        }
    }

    // =======================================================================
    // State / track change handlers — always on main thread
    // =======================================================================

    private void onStateDetected(byte avrcpStatus) {
        // avrcpStatus: 0=STOPPED, 1=PLAYING, 2=PAUSED (AVRCP §5.4.1 Tbl 5.26).
        boolean playing = (avrcpStatus == 1);
        if (avrcpStatus == mPlayStatus) return;
        Log.d(TAG, "State change: avrcpStatus=" + avrcpStatus
                + " (" + (avrcpStatus == 1 ? "PLAYING"
                        : avrcpStatus == 0 ? "STOPPED" : "PAUSED") + ")"
                + " callbacks=" + mAvrcpCallbacks.size());
        mPositionAtStateChange = computePosition();
        mStateChangeTime = SystemClock.elapsedRealtime();
        mPlayStatus = avrcpStatus;
        mIsPlaying = playing;
        // Refresh the playing_flag byte at y1-track-info[792] BEFORE any
        // broadcast fires. Per AVRCP 1.3 §5.4.1 GetPlayStatus must report
        // the current play_status, and per §5.4.2 (Table 5.29
        // EVENT_PLAYBACK_STATUS_CHANGED) the CHANGED frame must reflect
        // the post-edge value. Both T6 (GetPlayStatus) and T9
        // (PLAYBACK_STATUS_CHANGED proactive) read y1-track-info[792]
        // for that source-of-truth value; without this writeTrackInfoFile()
        // call the file stays stale across play/pause toggles within a
        // track, and a CT polling GetPlayStatus or subscribed to event
        // 0x01 sees the pre-edge value indefinitely.
        writeTrackInfoFile();
        publishState();
        sendMusicBroadcast("com.android.music.playstatechanged");
        notifyPlaybackStatus(callbackPlayStatusByte());
        // Drive the 1 s position-tick cadence. Start on play, stop on
        // pause/stop. The tick fires `playstatechanged` so T9 emits
        // PLAYBACK_POS_CHANGED CHANGED with a fresh live-extrapolated
        // position.
        if (playing) {
            schedulePosTick();
        } else {
            cancelPosTick();
        }
    }

    /**
     * 1 s tick that drives PLAYBACK_POS_CHANGED CHANGED. While
     * mIsPlaying, fire `playstatechanged` every {@link #POS_TICK_INTERVAL_MS}
     * to wake T9 on the libextavrcp_jni.so side. T9 reads file[792] (still
     * PLAYING), live-extrapolates the position via clock_gettime
     * CLOCK_BOOTTIME, and emits the CHANGED frame.
     *
     * The tick is idempotent — re-calling schedulePosTick() while one is
     * already pending cancels the old before posting the new (mostly
     * defensive; in practice we only call it on play edges and from the
     * tick body itself).
     */
    private void schedulePosTick() {
        if (mPosTickRunnable != null) {
            mMainHandler.removeCallbacks(mPosTickRunnable);
        }
        mPosTickRunnable = new Runnable() {
            @Override
            public void run() {
                if (!mIsPlaying) return;
                // Don't re-write y1-track-info — the file's
                // pos_at_state_change_ms / state_change_time_sec already
                // anchor the live extrapolation T9 does on the trampoline
                // side. Just fire the broadcast (with the standard music
                // extras MtkBt's BroadcastReceiver expects). T9 will read
                // y1-track-info, see file[792] == PLAYING, compute
                // live_pos via clock_gettime CLOCK_BOOTTIME, and emit
                // PLAYBACK_POS_CHANGED CHANGED.
                sendMusicBroadcast("com.android.music.playstatechanged");
                // Re-schedule.
                mMainHandler.postDelayed(this, POS_TICK_INTERVAL_MS);
            }
        };
        mMainHandler.postDelayed(mPosTickRunnable, POS_TICK_INTERVAL_MS);
    }

    private void cancelPosTick() {
        if (mPosTickRunnable != null) {
            mMainHandler.removeCallbacks(mPosTickRunnable);
            mPosTickRunnable = null;
        }
    }

    private void onTrackDetected(String path) {
        if (path.equals(mCurrentPath)) return;

        // Detect whether the previous track ended naturally
        // (position ≈ duration at the moment of the track edge) vs was
        // interrupted by a skip / stop / pause+resume-on-different-track.
        // AVRCP 1.3 §5.4.2 Tbl 5.31 (TRACK_REACHED_END) is "Notify when
        // reached the end of the track of the playing element" —
        // natural-end-only, not skip-driven. The T5 trampoline reads this
        // flag from y1-track-info[793] to decide whether to emit the
        // event 0x03 CHANGED frame alongside the standard 0x02 + 0x04.
        //
        // Heuristic: previous track's extrapolated position was within
        // [-1s..+2s] of its duration at the moment the new track was
        // detected. The 1s lower bound covers tracks where the player
        // overshoots duration slightly before signalling end-of-track;
        // the 2s upper bound covers normal LogcatMonitor staleness
        // (state-change anchor can be a few hundred ms behind real-time
        // playback). Tighter bounds risk false negatives on slow logcat
        // pipes; looser bounds risk false positives on aggressive
        // skip-near-end. Skip when there's no previous track (cold start)
        // or no known duration (couldn't read tags) — both leave the
        // flag at its default `false`.
        boolean previousNaturalEnd = false;
        if (mCurrentPath != null && mCurrentDuration > 0) {
            long prevPos = computePosition();
            long delta = mCurrentDuration - prevPos;
            previousNaturalEnd = (delta >= -1000L && delta <= 2000L);
        }
        Log.d(TAG, "Track change: " + path
                + " prevNaturalEnd=" + previousNaturalEnd);

        mCurrentPath = path;
        mPreviousTrackNaturalEnd = previousNaturalEnd;
        mPositionAtStateChange = 0;
        mStateChangeTime = SystemClock.elapsedRealtime();

        if (queryMetadataFromStore(path)) {
            // MediaStore had it cached — fast path, single broadcast.
            broadcastTrackAndState();
            return;
        }

        // MediaStore miss. Try a direct ID3 read (~50ms) so the CT sees real
        // metadata immediately rather than the filename. Whether or not that
        // succeeds, we always kick the scanner so future plays of this file
        // hit MediaStore — and on firmwares where the direct read fails with
        // EACCES (mediaserver/our-uid lacking read on /storage/sdcard0), we
        // re-broadcast canonical metadata when the scanner completes. Worst
        // case the user sees filename → canonical metadata in ~1s instead of
        // filename forever.
        boolean directReadOk = readTagsDirectly(path);
        broadcastTrackAndState();

        // Stage 2: scanner-completion follow-up. Skip if the direct read
        // already gave us real metadata, or a scan is already in flight.
        if (directReadOk) return;
        if (path.equals(mPendingScanPath)) {
            Log.d(TAG, "MediaStore miss for " + path
                    + " — direct read empty, scan already in progress");
            return;
        }
        Log.d(TAG, "MediaStore miss for " + path
                + " — kicking scanner; will re-broadcast on completion");
        mPendingScanPath = path;
        MediaScannerConnection.scanFile(this, new String[]{ path }, null,
                new MediaScannerConnection.OnScanCompletedListener() {
                    @Override
                    public void onScanCompleted(String p, Uri uri) {
                        final String finalPath = p;
                        mMainHandler.post(new Runnable() {
                            @Override public void run() {
                                if (finalPath.equals(mPendingScanPath)) {
                                    mPendingScanPath = null;
                                }
                                // Track may have changed since we kicked the
                                // scan — only re-broadcast if this is still
                                // the active path.
                                if (!finalPath.equals(mCurrentPath)) return;
                                if (queryMetadataFromStore(finalPath)) {
                                    Log.d(TAG, "Scanner-completion re-broadcast: "
                                            + mCurrentTitle + " / " + mCurrentArtist);
                                    broadcastTrackAndState();
                                }
                            }
                        });
                    }
                });
    }

    private void broadcastTrackAndState() {
        // Write y1-track-info FIRST, before any cross-process notification
        // dispatch. The native AVRCP T5 trampoline (libextavrcp_jni.so,
        // reached via the patched notificationTrackChangedNative) reads
        // y1-track-info and compares its first 8 bytes against
        // y1-trampoline-state to decide whether to emit a CHANGED edge on
        // the wire. T5 fires inside MtkBt's process when it receives the
        // metachanged broadcast we send below — if writeTrackInfoFile
        // hadn't run yet, T5 would read the previous track's bytes, see
        // no change, and skip the CHANGED. Reorder so the file write
        // completes before the broadcast can possibly round-trip through
        // ActivityManager → MtkBt → handleKeyMessage → T5.
        writeTrackInfoFile();
        Log.d(TAG, "broadcastTrackAndState title=" + safeString(mCurrentTitle)
                + " artist=" + safeString(mCurrentArtist)
                + " audioId=" + mCurrentAudioId
                + " playing=" + mIsPlaying
                + " callbacks=" + mAvrcpCallbacks.size());
        publishMetadata();
        publishState();
        sendMusicBroadcast("com.android.music.metachanged");
        notifyTrackChanged(mCurrentAudioId);
        notifyPlaybackStatus(callbackPlayStatusByte());
    }

    // =======================================================================
    // Track-info file for the AVRCP T4 trampoline (in libextavrcp_jni.so)
    //
    // Java-side AVRCP is a stub on this firmware (cardinality:0 — no peer
    // subscriptions tracked, getElementAttributesRspNative declared but never
    // called). To deliver metadata, --avrcp-min patches a native trampoline
    // chain into libextavrcp_jni.so that handles inbound AVRCP commands
    // directly. The T4 trampoline (PDU 0x20 GetElementAttributes) reads this
    // file via open(2) + read(2) syscall, then calls
    // btmtk_avrcp_send_get_element_attributes_rsp via PLT 0x3570 with the
    // strings as arguments. See docs/ARCHITECTURE.md in the koensayr repo.
    //
    // Path is the app's private getFilesDir() rather than /data/local/tmp/
    // because Y1MediaBridge runs as uid 10000 (regular app uid, not system)
    // and /data/local/tmp/ is mode 0771 owner=shell — uid 10000 has no write
    // permission there. Our private files dir is owned by us; we chmod it
    // world-x at startup (prepareTrackInfoDir) and the file world-r here.
    //
    // y1-track-info file format. Fixed layout, null-padded UTF-8 string slots.
    //   bytes 0..7      = track_id (mCurrentAudioId, big-endian) — extended_T2
    //                     answers RegisterNotification(TRACK_CHANGED) with this
    //                     and T4 compares against last_seen to detect changes.
    //   bytes 8..263    = title  (255 UTF-8 bytes max + trailing null)
    //   bytes 264..519  = artist (same)
    //   bytes 520..775  = album  (same)
    //   bytes 776..779  = duration_ms              u32 BE  (T6, T4 attr 7)
    //   bytes 780..783  = pos_at_state_change_ms  u32 BE  (T6, T8 event 0x05)
    //   bytes 784..787  = state_change_time_sec   u32 BE  (T6 live-position
    //                                                       extrapolation)
    //   bytes 788..791  = pad                                                    (reserved)
    //   bytes 792       = playing_flag             u8     (T6, T8/T9 event 0x01)
    //   bytes 793       = previous_track_natural_end u8  (T5 gate for AVRCP
    //                                                       §5.4.2 Tbl 5.31
    //                                                       TRACK_REACHED_END)
    //   bytes 794       = battery_status u8 (T8 INTERIM + T9 CHANGED-on-edge;
    //                                          AVRCP §5.4.2 Tbl 5.35 enum 0..4)
    //   bytes 795..799  = pad   (reserved for PlayerApplicationSettings)
    //   bytes 800..815  = TrackNumber              UTF-8 ASCII decimal (16 B slot)
    //   bytes 816..831  = TotalNumberOfTracks      UTF-8 ASCII decimal (16 B slot)
    //   bytes 832..847  = PlayingTime              UTF-8 ASCII decimal ms (16 B slot)
    //   bytes 848..1103 = Genre                    UTF-8 (256 B slot)
    //
    // Each string field is null-padded; we truncate to slot-1 bytes max so each
    // slot has at least one trailing 0x00 — the trampoline calls strlen() on
    // each slot start, and the trailing null bounds the read. Multi-byte
    // numeric fields are big-endian on disk; T6 byte-swaps via REV before
    // passing to btmtk_avrcp_send_get_playstatus_rsp.
    //
    // Schema is append-only so older trampolines keep working when run
    // against a newer file (T6/T8/T9 only read up to offset 792 and are
    // unaffected by attrs 4-7 being appended past 800).
    //
    // Defensive: every code path is wrapped in try/catch(Throwable) so a
    // write failure (e.g., disk-full, EACCES, weird OS state) never
    // crashes the service. An earlier silent IOException-vs-Throwable
    // bug took the entire Y1MediaBridge down on EACCES; the catch is
    // intentionally broad.
    private static final String TRACK_INFO_FILENAME = "y1-track-info";
    private static final String TRAMPOLINE_STATE_FILENAME = "y1-trampoline-state";
    private static final int TRACK_ID_LEN = 8;
    private static final int FIELD_LEN = 256;
    private static final int NUMERIC_STR_LEN = 16;
    private static final int TITLE_OFFSET  = TRACK_ID_LEN;             // 8
    private static final int ARTIST_OFFSET = TITLE_OFFSET + FIELD_LEN; // 264
    private static final int ALBUM_OFFSET  = ARTIST_OFFSET + FIELD_LEN; // 520
    private static final int DURATION_OFFSET    = ALBUM_OFFSET + FIELD_LEN;       // 776 - duration_ms u32 BE
    private static final int POSITION_OFFSET    = DURATION_OFFSET + 4;            // 780 - pos_at_state_change u32 BE
    private static final int STATE_TIME_OFFSET  = POSITION_OFFSET + 4;            // 784 - state_change_time_sec u32 BE (reserved)
    private static final int PLAY_STATUS_OFFSET = STATE_TIME_OFFSET + 8;          // 792 - playing_flag u8
    /** Previous track's natural-end flag at byte 793.
     *  T5 (libextavrcp_jni.so trampoline) reads this to gate AVRCP 1.3 §5.4.2
     *  Tbl 5.31 TRACK_REACHED_END (event 0x03) CHANGED emission. 1=natural
     *  end (emit TRACK_REACHED_END), 0=skip / interrupt (omit). */
    private static final int NATURAL_END_OFFSET = PLAY_STATUS_OFFSET + 1;         // 793 - previous_track_natural_end u8
    /** Bucket-mapped AVRCP §5.4.2 Tbl 5.35 battery enum at byte 794.
     *  T8 reads this for event 0x06 INTERIM and T9 reads it for CHANGED-on-edge
     *  detection (compares against y1-trampoline-state[10]). 0=NORMAL,
     *  1=WARNING, 2=CRITICAL, 3=EXTERNAL, 4=FULL_CHARGE. */
    private static final int BATTERY_STATUS_OFFSET = PLAY_STATUS_OFFSET + 2;      // 794 - battery_status u8
    // GetElementAttributes attrs 4-7. Pre-formatted UTF-8 strings — keeps the
    // T4 trampoline a uniform strlen+memcpy loop and avoids hand-rolled Thumb-2
    // itoa for the numeric fields.
    private static final int TRACK_NUM_OFFSET   = 800;                            // 800 - TrackNumber (attr 4)
    private static final int TOTAL_TRACKS_OFFSET = TRACK_NUM_OFFSET + NUMERIC_STR_LEN;   // 816 - TotalNumberOfTracks (attr 5)
    private static final int PLAYING_TIME_OFFSET = TOTAL_TRACKS_OFFSET + NUMERIC_STR_LEN; // 832 - PlayingTime (attr 7)
    private static final int GENRE_OFFSET       = PLAYING_TIME_OFFSET + NUMERIC_STR_LEN; // 848 - Genre (attr 6)
    private static final int TOTAL_LEN          = GENRE_OFFSET + FIELD_LEN;       // 1104
    private static final int STATE_LEN          = 16;

    /** Per-attribute byte cap for the GetElementAttributes response builder
     *  in `libextavrcp.so:btmtk_avrcp_send_get_element_attributes_rsp`. The
     *  OEM builder enforces a 511-byte hard cap and silently drops any
     *  attribute that exceeds it (`[BT][AVRCP][ERR] too large attr_index:%d`).
     *  We truncate at 240 B at the y1-track-info layer so even with multi-byte
     *  UTF-8 expansion the per-attribute payload stays well under 511 — and a
     *  strict CT receives a complete (if truncated) value instead of a
     *  silent attribute drop. AVRCP 1.3 §5.3.4 places no per-attribute cap
     *  itself; the cap is purely a deviation in the OEM TG response builder. */
    private static final int AVRCP_ATTR_MAX_BYTES = 240;

    private void writeTrackInfoFile() {
        try {
            byte[] buf = new byte[TOTAL_LEN];  // zero-initialized
            // 8-byte big-endian track_id at offset 0.
            long id = mCurrentAudioId;
            for (int i = 0; i < TRACK_ID_LEN; i++) {
                buf[i] = (byte) ((id >> (56 - i * 8)) & 0xFF);
            }
            putUtf8Padded(buf, TITLE_OFFSET,  FIELD_LEN, mCurrentTitle);
            putUtf8Padded(buf, ARTIST_OFFSET, FIELD_LEN, mCurrentArtist);
            putUtf8Padded(buf, ALBUM_OFFSET,  FIELD_LEN, mCurrentAlbum);

            // duration_ms / position / play_status for T6 GetPlayStatus.
            // Big-endian u32 to match the existing track_id encoding; T6 byte-swaps
            // before passing to the response builder. AVRCP 1.3 §5.4.1
            // Table 5.26 notes "If TG does not support SongLength And
            // SongPosition on TG, then TG shall return 0xFFFFFFFF"; we use 0
            // for both when unknown rather than the sentinel because the CTs
            // in our test matrix render 0 cleanly while some interpret the
            // 0xFFFFFFFF sentinel as a literal duration (also a Table-5.26
            // allowed value 0..(2^32-1)). Both are spec-permissible. See
            // `docs/INVESTIGATION.md` "Hardware test history per CT" for the
            // empirical observations.
            long duration = mCurrentDuration > 0 ? mCurrentDuration : 0L;
            putBE32(buf, DURATION_OFFSET, (int) Math.min(duration, 0xFFFFFFFFL));
            putBE32(buf, POSITION_OFFSET, (int) Math.min(mPositionAtStateChange, 0xFFFFFFFFL));
            putBE32(buf, STATE_TIME_OFFSET, (int) (mStateChangeTime / 1000L));
            // playing_flag: 0=STOPPED, 1=PLAYING, 2=PAUSED — direct mapping
            // to AVRCP 1.3 §5.4.1 Table 5.26 PlayStatus enum. mPlayStatus is
            // maintained in lockstep with mIsPlaying by onStateDetected,
            // which receives the three-valued AVRCP enum byte from
            // LogcatMonitor (which now recognizes Y1's state-code '5' =
            // STOPPED in addition to '1' PLAYING and '3' PAUSED).
            buf[PLAY_STATUS_OFFSET] = mPlayStatus;
            // Natural-end flag for the AVRCP T5 trampoline's TRACK_REACHED_END
            // gate. mPreviousTrackNaturalEnd is set in onTrackDetected by
            // comparing the previous track's extrapolated position against its
            // duration at the moment of the track edge.
            buf[NATURAL_END_OFFSET] = (byte) (mPreviousTrackNaturalEnd ? 0x01 : 0x00);
            // Battery_status bucket for AVRCP T8 INTERIM (event 0x06) and
            // T9 CHANGED-on-edge detection. Updated by mBatteryReceiver on
            // `Intent.ACTION_BATTERY_CHANGED` bucket transitions.
            buf[BATTERY_STATUS_OFFSET] = mCurrentBatteryStatus;

            // GetElementAttributes attrs 4-7 (AVRCP 1.3 §5.3.4). Store as
            // pre-formatted UTF-8 strings so T4 ships them with the same
            // strlen+memcpy machinery it uses for title/artist/album. Per
            // §5.3.4: "if the requested element attribute does not exist
            // (e.g., the element does not have a Genre tag), the AttributeID
            // and CharacterSet are returned but the AttributeValue is the
            // null string and the AttributeValueLength is 0" — empty string
            // is the spec-correct sentinel for unknown.
            putUtf8Padded(buf, TRACK_NUM_OFFSET,    NUMERIC_STR_LEN,
                    mCurrentTrackNumber > 0 ? Integer.toString(mCurrentTrackNumber) : "");
            putUtf8Padded(buf, TOTAL_TRACKS_OFFSET, NUMERIC_STR_LEN,
                    mCurrentTotalTracks > 0 ? Integer.toString(mCurrentTotalTracks) : "");
            putUtf8Padded(buf, PLAYING_TIME_OFFSET, NUMERIC_STR_LEN,
                    duration > 0 ? Long.toString(duration) : "");
            putUtf8Padded(buf, GENRE_OFFSET,        FIELD_LEN, mCurrentGenre);

            File dir = getFilesDir();
            if (dir == null) {
                Log.w(TAG, "writeTrackInfoFile: getFilesDir() returned null");
                return;
            }
            File tmp = new File(dir, TRACK_INFO_FILENAME + ".tmp");
            File target = new File(dir, TRACK_INFO_FILENAME);

            FileOutputStream fos = new FileOutputStream(tmp);
            try { fos.write(buf); } finally { fos.close(); }

            if (!tmp.renameTo(target)) {
                Log.w(TAG, "writeTrackInfoFile: rename failed");
                tmp.delete();
                return;
            }
            // World-readable so the BT process (uid bluetooth) can open it.
            target.setReadable(true, false);
        } catch (Throwable t) {
            Log.w(TAG, "writeTrackInfoFile: " + t);
        }
    }

    private static void putUtf8Padded(byte[] dst, int off, int slot, String s) {
        if (s == null) return;
        byte[] src;
        try {
            // String form for API 17 compat; java.nio.charset.StandardCharsets is API 19+.
            src = s.getBytes("UTF-8");
        } catch (UnsupportedEncodingException e) {
            // UTF-8 is always supported by the JVM — this branch is unreachable.
            return;
        }
        // Cap = min(slot-1, AVRCP_ATTR_MAX_BYTES). slot-1 guarantees a trailing
        // null byte for the trampoline's strlen+memcpy chain; AVRCP_ATTR_MAX_BYTES
        // keeps each value under the OEM 511-byte per-attribute hard cap.
        int cap = slot - 1;
        if (cap > AVRCP_ATTR_MAX_BYTES) cap = AVRCP_ATTR_MAX_BYTES;
        int n = src.length < cap ? src.length : cap;
        // UTF-8 codepoint-safe truncation: if the byte just past our truncation
        // point is a continuation byte (10xxxxxx, i.e. 0x80..0xBF), we'd be
        // cutting a multi-byte codepoint in half and leaving a partial sequence
        // — strict CTs reject that as malformed UTF-8 per AVRCP 1.3 §5.3.4
        // CharacterSet=0x6A (UTF-8). Walk back to the codepoint boundary.
        while (n > 0 && n < src.length && (src[n] & 0xC0) == 0x80) n--;
        System.arraycopy(src, 0, dst, off, n);
    }

    /** Big-endian u32 store. Used by writeTrackInfoFile for the
     *  GetPlayStatus fields (duration_ms / position_at_state_change_ms /
     *  state_change_time_sec). Matches the existing track_id BE encoding
     *  so the T6 trampoline can REV-swap uniformly. */
    private static void putBE32(byte[] dst, int off, int v) {
        dst[off]     = (byte) ((v >> 24) & 0xFF);
        dst[off + 1] = (byte) ((v >> 16) & 0xFF);
        dst[off + 2] = (byte) ((v >>  8) & 0xFF);
        dst[off + 3] = (byte)  (v        & 0xFF);
    }

    // =======================================================================
    // Helpers
    // =======================================================================

    private void sendMusicBroadcast(String action) {
        Intent i = new Intent(action);
        i.putExtra("id",       mCurrentAudioId);
        i.putExtra("artist",   safeString(mCurrentArtist));
        i.putExtra("album",    safeString(mCurrentAlbum));
        i.putExtra("track",    safeString(mCurrentTitle));
        i.putExtra("playing",  mIsPlaying);
        i.putExtra("duration", mCurrentDuration);
        i.putExtra("position", computePosition());
        sendBroadcast(i);
    }

    private void sendMediaKey(int keyCode) {
        ComponentName target = new ComponentName("com.innioasis.y1",
                "com.innioasis.y1.receiver.PlayControllerReceiver");
        KeyEvent down = new KeyEvent(KeyEvent.ACTION_DOWN, keyCode);
        KeyEvent up   = new KeyEvent(KeyEvent.ACTION_UP,   keyCode);
        Intent i = new Intent(Intent.ACTION_MEDIA_BUTTON);
        i.setComponent(target);
        i.putExtra(Intent.EXTRA_KEY_EVENT, down);
        sendBroadcast(i);
        i = new Intent(Intent.ACTION_MEDIA_BUTTON);
        i.setComponent(target);
        i.putExtra(Intent.EXTRA_KEY_EVENT, up);
        sendBroadcast(i);
    }

    // =======================================================================
    // Metadata loading
    // =======================================================================

    private boolean queryMetadataFromStore(String path) {
        String[] projection = {
                MediaStore.Audio.Media.TITLE,
                MediaStore.Audio.Media.ARTIST,
                MediaStore.Audio.Media.ALBUM,
                MediaStore.Audio.Media.DURATION,
                MediaStore.Audio.Media.ALBUM_ID,
                MediaStore.Audio.Media.ARTIST_ID,
                MediaStore.Audio.Media._ID,
                MediaStore.Audio.Media.TRACK
        };
        Cursor cursor = null;
        try {
            cursor = getContentResolver().query(
                    MediaStore.Audio.Media.EXTERNAL_CONTENT_URI,
                    projection,
                    MediaStore.Audio.Media.DATA + "=?",
                    new String[]{ path }, null);
            if (cursor != null && cursor.moveToFirst()) {
                mCurrentTitle    = cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.TITLE));
                mCurrentArtist   = cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.ARTIST));
                mCurrentAlbum    = cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.ALBUM));
                mCurrentDuration = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.DURATION));
                mCurrentAlbumId  = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.ALBUM_ID));
                mCurrentArtistId = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.ARTIST_ID));
                mCurrentAudioId  = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media._ID));
                // MediaStore.TRACK encodes track number as (disc * 1000) + track per
                // android.provider.MediaStore.Audio.AudioColumns#TRACK. Mod 1000
                // yields the in-disc track number; we don't surface disc number.
                int trackRaw = cursor.getInt(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.TRACK));
                mCurrentTrackNumber = trackRaw > 0 ? trackRaw % 1000 : 0;
                if ("<unknown>".equals(mCurrentArtist)) mCurrentArtist = "";
                if ("<unknown>".equals(mCurrentAlbum))  mCurrentAlbum  = "";
                mCurrentAlbumArt = loadAlbumArt(mCurrentAlbumId);
                mCurrentGenre = lookupGenreForAudioId(mCurrentAudioId);
                mCurrentTotalTracks = mCurrentAlbumId > 0 ? lookupTotalTracksForAlbum(mCurrentAlbumId) : 0;
                Log.d(TAG, "MediaStore hit: " + mCurrentTitle + " / " + mCurrentArtist
                        + " (track " + mCurrentTrackNumber + "/" + mCurrentTotalTracks
                        + ", genre=" + mCurrentGenre + ")");
                return true;
            }
        } catch (Exception e) {
            Log.e(TAG, "MediaStore error: " + e.getMessage());
        } finally {
            if (cursor != null) cursor.close();
        }
        return false;
    }

    /** Genre lookup via MediaStore.Audio.Genres.Members — single-genre per audio
     *  is the common case; first match wins on multi-genre files. */
    private String lookupGenreForAudioId(long audioId) {
        if (audioId <= 0) return "";
        Cursor c = null;
        try {
            c = getContentResolver().query(
                    MediaStore.Audio.Genres.getContentUriForAudioId("external", (int) audioId),
                    new String[]{ MediaStore.Audio.Genres.NAME },
                    null, null, null);
            if (c != null && c.moveToFirst()) {
                String g = c.getString(0);
                return g != null ? g : "";
            }
        } catch (Exception e) {
            Log.v(TAG, "genre lookup: " + e.getMessage());
        } finally {
            if (c != null) c.close();
        }
        return "";
    }

    /** TotalNumberOfTracks (attr 5) = count of audio rows on this album. */
    private int lookupTotalTracksForAlbum(long albumId) {
        Cursor c = null;
        try {
            c = getContentResolver().query(
                    MediaStore.Audio.Media.EXTERNAL_CONTENT_URI,
                    new String[]{ "count(*) AS n" },
                    MediaStore.Audio.Media.ALBUM_ID + "=?",
                    new String[]{ Long.toString(albumId) }, null);
            if (c != null && c.moveToFirst()) return c.getInt(0);
        } catch (Exception e) {
            Log.v(TAG, "total-tracks lookup: " + e.getMessage());
        } finally {
            if (c != null) c.close();
        }
        return 0;
    }

    /**
     * Try to read ID3 tags directly from the file head. Returns true if we
     * obtained a real Title and at least one of Artist / Album; false on any
     * failure (EACCES, missing file, parser error) or when the resulting
     * tags are empty enough that the caller should treat it as a miss and
     * fall back on the scanner-completion re-broadcast path.
     */
    private boolean readTagsDirectly(String path) {
        MediaMetadataRetriever r = new MediaMetadataRetriever();
        FileInputStream fis = null;
        try {
            // setDataSource(String) IPCs the path to mediaserver (uid 1013).
            // Opening via FileInputStream first runs the open(2) in our own
            // process so the access check uses our supplementary groups,
            // not mediaserver's. With WRITE_MEDIA_STORAGE granted (system
            // app in /system/app/), AID_MEDIA_RW is in our group set and
            // /storage/sdcard0/Music is readable. On firmwares where the
            // permission isn't actually granted we still get EACCES — the
            // caller schedules the scanner and re-broadcasts on completion.
            fis = new FileInputStream(path);
            r.setDataSource(fis.getFD());
            mCurrentTitle    = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_TITLE);
            mCurrentArtist   = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ARTIST);
            mCurrentAlbum    = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ALBUM);
            String dur       = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION);
            mCurrentDuration = dur != null ? Long.parseLong(dur) : 0;
            mCurrentGenre    = safeString(r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_GENRE));
            // CD_TRACK_NUMBER is "n" or "n/total" depending on the tagger.
            // ID3v2 TRCK frame uses "n/total"; vorbiscomment uses separate
            // TRACKNUMBER + TRACKTOTAL but MediaMetadataRetriever only exposes
            // CD_TRACK_NUMBER, so attr 5 (TotalNumberOfTracks) is best-effort
            // here and may be 0 when the underlying tag lacks the total.
            int[] trk = parseTrackNumber(r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_CD_TRACK_NUMBER));
            mCurrentTrackNumber = trk[0];
            mCurrentTotalTracks = trk[1];
            mCurrentAudioId  = syntheticAudioId(path);
            mCurrentAlbumId  = -1;
            mCurrentArtistId = -1;
            byte[] art = r.getEmbeddedPicture();
            mCurrentAlbumArt = art != null ? decodeSampled(art, MAX_ART_PX) : null;
            boolean realTitle = mCurrentTitle != null && !mCurrentTitle.isEmpty();
            boolean realArtist = mCurrentArtist != null && !mCurrentArtist.isEmpty();
            boolean realAlbum  = mCurrentAlbum  != null && !mCurrentAlbum.isEmpty();
            if (!realTitle) mCurrentTitle = stripExtension(path);
            if (mCurrentArtist == null) mCurrentArtist = "";
            if (mCurrentAlbum  == null) mCurrentAlbum  = "";
            Log.d(TAG, "Direct tags: " + mCurrentTitle + " / " + mCurrentArtist
                    + " / " + mCurrentAlbum + " (track " + mCurrentTrackNumber
                    + "/" + mCurrentTotalTracks + ", genre=" + mCurrentGenre + ")");
            return realTitle && (realArtist || realAlbum);
        } catch (Exception e) {
            Log.e(TAG, "MediaMetadataRetriever error: " + e);
            mCurrentTitle    = stripExtension(path);
            mCurrentArtist   = "";
            mCurrentAlbum    = "";
            mCurrentGenre    = "";
            mCurrentTrackNumber = 0;
            mCurrentTotalTracks = 0;
            mCurrentAlbumArt = null;
            mCurrentAudioId  = syntheticAudioId(path);
            mCurrentAlbumId  = -1;
            mCurrentArtistId = -1;
            return false;
        } finally {
            try { r.release(); } catch (Exception ignored) {}
            if (fis != null) try { fis.close(); } catch (Exception ignored) {}
        }
    }

    private Bitmap loadAlbumArt(long albumId) {
        if (albumId <= 0) return null;
        try {
            Uri uri = ContentUris.withAppendedId(
                    Uri.parse("content://media/external/audio/albumart"), albumId);
            BitmapFactory.Options opts = new BitmapFactory.Options();
            opts.inJustDecodeBounds = true;
            InputStream is = getContentResolver().openInputStream(uri);
            if (is == null) return null;
            BitmapFactory.decodeStream(is, null, opts);
            is.close();
            opts.inSampleSize = sampleSize(opts.outWidth, opts.outHeight, MAX_ART_PX);
            opts.inJustDecodeBounds = false;
            is = getContentResolver().openInputStream(uri);
            if (is == null) return null;
            Bitmap bmp = BitmapFactory.decodeStream(is, null, opts);
            is.close();
            Log.v(TAG, "loadAlbumArt id=" + albumId + " → "
                    + (bmp != null ? bmp.getWidth() + "x" + bmp.getHeight() : "null"));
            return bmp;
        } catch (Exception e) {
            Log.v(TAG, "loadAlbumArt id=" + albumId + " failed: " + e.getMessage());
            return null;
        }
    }

    private Bitmap decodeSampled(byte[] data, int maxPx) {
        BitmapFactory.Options opts = new BitmapFactory.Options();
        opts.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(data, 0, data.length, opts);
        opts.inSampleSize = sampleSize(opts.outWidth, opts.outHeight, maxPx);
        opts.inJustDecodeBounds = false;
        return BitmapFactory.decodeByteArray(data, 0, data.length, opts);
    }

    private int sampleSize(int w, int h, int maxPx) {
        int s = 1;
        while ((h / (s * 2)) >= maxPx && (w / (s * 2)) >= maxPx) s *= 2;
        return s;
    }

    /**
     * Stable synthetic track_id derived from the file path, for the case where
     * MediaStore hasn't populated yet so we don't have a real {@code _ID}.
     *
     * The trampoline state machine in libextavrcp_jni.so (T5 / extended_T2)
     * detects "track changed since last register / notify" by comparing bytes
     * 0..7 of {@code y1-track-info} against bytes 0..7 of
     * {@code y1-trampoline-state}. If we leave audioId at {@code -1} for
     * every track (which would happen if the readTagsDirectly success
     * path bypassed MediaStore without computing a synthetic id), bytes
     * 0..7 stay at {@code 0xFFFFFFFFFFFFFFFF} forever and T5 never
     * detects a change → no proactive CHANGED → CTs that don't subscribe
     * to event 0x02 must rely on their polling cadence for any UI
     * refresh, which is enough for mid-session track skips but not for
     * the very first track after a connection hand-shake.
     *
     * Wire-level track_id is independently pinned to the {@code 0xFF×8}
     * sentinel in the trampoline (AVRCP 1.3 §5.4.2 Table 5.30 + ESR07 §2.2
     * 8-byte clarification), so this internal-only
     * id never reaches the peer device. Synthetic ids OR'd with bit 32 to
     * keep them distinct from MediaStore _IDs (typically small integers).
     */
    private static long syntheticAudioId(String path) {
        if (path == null) return 0x100000000L;
        return ((long) path.hashCode() & 0xFFFFFFFFL) | 0x100000000L;
    }

    private String stripExtension(String path) {
        if (path == null) return "";
        int slash = path.lastIndexOf('/');
        String name = slash >= 0 ? path.substring(slash + 1) : path;
        int dot = name.lastIndexOf('.');
        return dot > 0 ? name.substring(0, dot) : name;
    }

    private static String safeString(String s) { return s == null ? "" : s; }

    /** Parse "n", "n/total", or null into [track, total]. ID3v2 TRCK stores
     *  "n/total"; vorbiscomment splits across TRACKNUMBER + TRACKTOTAL but
     *  MediaMetadataRetriever only exposes the combined CD_TRACK_NUMBER field.
     *  Returns [0, 0] on any parse failure. */
    private static int[] parseTrackNumber(String s) {
        int[] out = new int[]{ 0, 0 };
        if (s == null || s.isEmpty()) return out;
        try {
            int slash = s.indexOf('/');
            if (slash < 0) {
                out[0] = Integer.parseInt(s.trim());
            } else {
                out[0] = Integer.parseInt(s.substring(0, slash).trim());
                String tail = s.substring(slash + 1).trim();
                if (!tail.isEmpty()) out[1] = Integer.parseInt(tail);
            }
        } catch (NumberFormatException e) {
            // tolerate junk in tags
        }
        return out;
    }
}

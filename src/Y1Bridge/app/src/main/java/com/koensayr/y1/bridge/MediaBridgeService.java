package com.koensayr.y1.bridge;

import android.app.Service;
import android.content.Intent;
import android.os.Binder;
import android.os.IBinder;
import android.os.Parcel;
import android.os.RemoteException;
import android.os.SystemClock;
import android.util.Log;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.charset.Charset;

/**
 * MediaBridgeService — Binder host that satisfies MtkBt's
 * {@code bindService(Intent("com.android.music.MediaPlaybackService"))}.
 *
 * <p>The music APK cannot declare this intent-filter itself
 * ({@code sharedUserId="android.uid.system"} + JarVerifier — see
 * {@code docs/ARCHITECTURE.md}). The bridge process therefore hosts the
 * Binder. State queries from {@code BTAvrcpMusicAdapter} are answered live
 * from {@code /data/data/com.innioasis.y1/files/y1-track-info}, the
 * 1104-byte file maintained by the music app's injected
 * {@code TrackInfoWriter} (Patch B5).
 *
 * <p>The proactive {@code IBTAvrcpMusicCallback} dispatch path is handled
 * out-of-band by the music-app-side wake helpers plus MtkBt's cardinality
 * NOPs plus the trampoline chain in {@code libextavrcp_jni.so} — the
 * Binder only needs to answer the synchronous queries
 * ({@code getPlayStatus}, {@code position}, {@code duration},
 * {@code getAudioId}, {@code getTrackName}, etc.) with real values so
 * {@code BTAvrcpMusicAdapter.mRegBit} stays armed and the Java mirror
 * matches the on-disk state.
 */
public class MediaBridgeService extends Service {

    private static final String TAG = "Y1Bridge";

    private static final String TRACK_INFO_PATH =
            "/data/data/com.innioasis.y1/files/y1-track-info";
    private static final int TRACK_INFO_SIZE = 1104;

    private static final Charset UTF8 = Charset.forName("UTF-8");

    // y1-track-info schema (BE byte order for u32 / u64; see docs/BT-COMPLIANCE.md §4).
    private static final int OFF_AUDIO_ID          = 0;
    private static final int OFF_TITLE             = 8;
    private static final int OFF_ARTIST            = 264;
    private static final int OFF_ALBUM             = 520;
    private static final int OFF_DURATION_MS       = 776;
    private static final int OFF_POSITION_MS       = 780;
    private static final int OFF_STATE_CHANGE_TIME = 784;
    private static final int OFF_PLAY_STATUS       = 792;
    private static final int OFF_REPEAT_AVRCP      = 795;
    private static final int OFF_SHUFFLE_AVRCP     = 796;
    private static final int STRING_FIELD_LEN      = 256;

    private final IBinder mBinder = new AvrcpBinder();

    @Override
    public void onCreate() {
        super.onCreate();
        Log.d(TAG, "MediaBridgeService.onCreate");
    }

    @Override
    public IBinder onBind(Intent intent) {
        return mBinder;
    }

    @Override
    public boolean onUnbind(Intent intent) {
        return true;
    }

    private static byte[] readTrackInfo() {
        byte[] buf = new byte[TRACK_INFO_SIZE];
        FileInputStream in = null;
        try {
            in = new FileInputStream(TRACK_INFO_PATH);
            int total = 0;
            while (total < TRACK_INFO_SIZE) {
                int n = in.read(buf, total, TRACK_INFO_SIZE - total);
                if (n < 0) break;
                total += n;
            }
        } catch (IOException ignored) {
            // Cold boot / music app not yet up. Zero-filled buffer = sensible
            // defaults (play_status=STOPPED, duration=0, empty strings).
        } finally {
            if (in != null) try { in.close(); } catch (IOException ignored) {}
        }
        return buf;
    }

    private static long readBeU64(byte[] buf, int off) {
        long v = 0;
        for (int i = 0; i < 8; i++) v = (v << 8) | (buf[off + i] & 0xffL);
        return v;
    }

    private static long readBeU32(byte[] buf, int off) {
        return ((buf[off]     & 0xffL) << 24)
             | ((buf[off + 1] & 0xffL) << 16)
             | ((buf[off + 2] & 0xffL) <<  8)
             |  (buf[off + 3] & 0xffL);
    }

    private static String readUtf8(byte[] buf, int off) {
        int end = off;
        int limit = off + STRING_FIELD_LEN;
        while (end < limit && buf[end] != 0) end++;
        return new String(buf, off, end - off, UTF8);
    }

    // Schema AVRCP enum (0=STOPPED, 1=PLAYING, 2=PAUSED) → IBTAvrcpMusicCallback
    // contract byte (1=STOPPED, 2=PLAYING, 3=PAUSED).
    private static byte avrcpToCallback(byte avrcpStatus) {
        switch (avrcpStatus & 0xff) {
            case 0:  return 1;
            case 1:  return 2;
            case 2:  return 3;
            default: return 3;
        }
    }

    private static long computePosition(byte[] buf) {
        long base = readBeU32(buf, OFF_POSITION_MS);
        if ((buf[OFF_PLAY_STATUS] & 0xff) != 1) return base;
        long anchor = readBeU32(buf, OFF_STATE_CHANGE_TIME);
        long elapsed = SystemClock.elapsedRealtime() - anchor;
        if (elapsed < 0) elapsed = 0;
        return base + elapsed;
    }

    private static final class AvrcpBinder extends Binder {
        @Override
        protected boolean onTransact(int code, Parcel data, Parcel reply, int flags)
                throws RemoteException {
            if (code == INTERFACE_TRANSACTION) {
                return super.onTransact(code, data, reply, flags);
            }
            try {
                // Skip strictModePolicy + descriptor. Descriptor strings drift
                // across ROM variations; enforcing them historically left
                // BTAvrcpMusicAdapter.mRegBit empty (all registrations fell
                // through to super.onTransact()).
                data.readInt();
                data.readString();
                return dispatch(code, data, reply);
            } catch (Throwable t) {
                Log.w(TAG, "onTransact code=" + code + ": " + t);
                if (reply != null) reply.writeNoException();
                return true;
            }
        }
    }

    private static boolean dispatch(int code, Parcel data, Parcel reply)
            throws RemoteException {
        switch (code) {

            case 1:  // registerCallback(IBTAvrcpMusicCallback)
            case 2:  // unregisterCallback(IBTAvrcpMusicCallback)
                // Proactive notification is driven by the cardinality-NOP
                // wake path in MtkBt.odex + T9 trampoline, not the callback
                // Binder. Ack so registration succeeds.
                try { data.readStrongBinder(); } catch (Exception ignored) {}
                if (reply != null) reply.writeNoException();
                return true;

            case 3: // regNotificationEvent(byte eventId, int param) -> boolean
                // Returning false (or empty reply) leaves mRegBit empty and
                // every later notification is dropped before AVRCP emission.
                try { data.readByte(); data.readInt(); } catch (Exception ignored) {}
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;

            case 4:  // setPlayerApplicationSettingValue(byte, byte) -> boolean
            case 14: // setEqualizeMode(int) -> boolean
            case 16: // setShuffleMode(int)  -> boolean
            case 18: // setRepeatMode(int)   -> boolean
            case 20: // setScanMode(int)     -> boolean
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;

            case 5: // getCapabilities() -> byte[]
                // 0x01 PLAYBACK_STATUS_CHANGED, 0x02 TRACK_CHANGED.
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeByteArray(new byte[]{ 0x01, 0x02 });
                }
                return true;

            // Passthrough — actual key delivery happens via libextavrcp_jni.so
            // uinput injection → ACTION_MEDIA_BUTTON → PlayControllerReceiver
            // (Patch E discrete arms). Bridge just acks.
            case 6:  case 7:  case 8:  case 9:
            case 10: case 11: case 12: case 13:
                if (reply != null) reply.writeNoException();
                return true;

            case 15: // getEqualizeMode()  -> int
            case 21: // getScanMode()     -> int
            case 36: // getQueuePosition() -> int
                if (reply != null) { reply.writeNoException(); reply.writeInt(0); }
                return true;

            case 17: { // getShuffleMode() -> int
                byte[] buf = readTrackInfo();
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeInt(buf[OFF_SHUFFLE_AVRCP] & 0xff);
                }
                return true;
            }
            case 19: { // getRepeatMode() -> int
                byte[] buf = readTrackInfo();
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeInt(buf[OFF_REPEAT_AVRCP] & 0xff);
                }
                return true;
            }

            case 22: // informDisplayableCharacterSet(int) -> boolean
            case 23: // informBatteryStatusOfCT()         -> boolean
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;

            case 24: { // getPlayStatus() -> byte (callback contract: 1=STOPPED, 2=PLAYING, 3=PAUSED)
                byte[] buf = readTrackInfo();
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeByte(avrcpToCallback(buf[OFF_PLAY_STATUS]));
                }
                return true;
            }
            case 25: { // position() -> long
                byte[] buf = readTrackInfo();
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeLong(computePosition(buf));
                }
                return true;
            }
            case 26: { // duration() -> long
                byte[] buf = readTrackInfo();
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeLong(readBeU32(buf, OFF_DURATION_MS));
                }
                return true;
            }
            case 27: { // getAudioId() -> long
                byte[] buf = readTrackInfo();
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeLong(readBeU64(buf, OFF_AUDIO_ID));
                }
                return true;
            }
            case 28: { // getTrackName() -> String
                byte[] buf = readTrackInfo();
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeString(readUtf8(buf, OFF_TITLE));
                }
                return true;
            }
            case 29: { // getAlbumName() -> String
                byte[] buf = readTrackInfo();
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeString(readUtf8(buf, OFF_ALBUM));
                }
                return true;
            }
            case 30: // getAlbumId() -> long  (not in schema; benign 0)
                if (reply != null) { reply.writeNoException(); reply.writeLong(0); }
                return true;

            case 31: { // getArtistName() -> String
                byte[] buf = readTrackInfo();
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeString(readUtf8(buf, OFF_ARTIST));
                }
                return true;
            }

            case 32: // enqueue(long[], int) -> void
            case 35: // open(long[], int)    -> void
            case 37: // setQueuePosition(int) -> void
                if (reply != null) reply.writeNoException();
                return true;

            case 33: // getNowPlaying() -> long[]
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeLongArray(new long[0]);
                }
                return true;

            case 34: // getNowPlayingItemName(long) -> String
                if (reply != null) { reply.writeNoException(); reply.writeString(""); }
                return true;

            default:
                if (reply != null) reply.writeNoException();
                return true;
        }
    }
}

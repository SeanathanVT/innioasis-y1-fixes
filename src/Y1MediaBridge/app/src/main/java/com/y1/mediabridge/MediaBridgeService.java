package com.y1.mediabridge;

import android.app.Service;
import android.content.Intent;
import android.os.Binder;
import android.os.IBinder;
import android.os.Parcel;
import android.os.RemoteException;
import android.util.Log;

/**
 * MediaBridgeService — minimal Binder host that satisfies MtkBt's
 * {@code bindService(Intent("com.android.music.MediaPlaybackService"))}.
 *
 * <p>This APK exists for one reason: the music app
 * ({@code com.innioasis.y1}) cannot declare the
 * {@code com.android.music.MediaPlaybackService} intent-filter in its
 * manifest. The music app declares {@code android:sharedUserId="android.uid.system"}
 * which constrains its signing key to the OEM platform key (we don't have it).
 * Any modification to {@code AndroidManifest.xml} invalidates the
 * {@code META-INF/MANIFEST.MF} SHA1-Digest and PackageManager rejects the
 * APK at {@code /system/app/} scan with "no certificates at entry
 * AndroidManifest.xml; ignoring!" (captured in
 * {@code docs/INVESTIGATION.md} Trace #23).
 *
 * <p>This bridge APK is self-signed (test debug key) and lives at its own
 * package name, so its manifest can be edited freely.
 *
 * <p>All real AVRCP work happens inside the music app process:
 * <ul>
 *   <li>{@code com.koensayr.y1.trackinfo.TrackInfoWriter} writes the
 *       1104-byte {@code y1-track-info} schema that the {@code libextavrcp_jni.so}
 *       trampoline chain reads.</li>
 *   <li>{@code com.koensayr.y1.playback.PlaybackStateBridge} hooks the music
 *       app's player engine (Static.setPlayValue + IjkMediaPlayer/MediaPlayer
 *       listener lambdas) so state edges are observed in-process — no logcat
 *       scraping, no foreground/background visibility gaps.</li>
 *   <li>{@code com.koensayr.y1.battery.BatteryReceiver} bucket-maps
 *       {@code ACTION_BATTERY_CHANGED} and fires
 *       {@code com.android.music.playstatechanged} so MtkBt's
 *       {@code BluetoothAvrcpReceiver} wakes T9 → AVRCP
 *       {@code BATT_STATUS_CHANGED CHANGED}.</li>
 *   <li>{@code com.koensayr.y1.papp.PappStateBroadcaster} +
 *       {@code PappSetFileObserver} round-trip Repeat/Shuffle between the CT
 *       and the music app's SharedPreferences.</li>
 * </ul>
 *
 * <p>Per the Sonos capture in {@code docs/INVESTIGATION.md} Trace #21, MtkBt
 * never actually transacts on this Binder — the broadcast wake path
 * (cardinality-NOP-patched JNI natives + {@code metachanged}/{@code playstatechanged}
 * fired by the music app) is what drives T5/T9 on the wire. So
 * {@code onTransact} is ack-only for every code: {@code writeNoException} +
 * {@code return true}. {@code getCapabilities} (transact code 5) is the one
 * exception — it returns {@code [0x01, 0x02]} so MtkBt's adapter actually
 * issues {@code REGISTER_NOTIFICATION} for the events we care about.
 *
 * <p>Returning {@code true} from {@code onUnbind} keeps the framework's
 * service record alive for the next {@code bindService} (avoids spurious
 * teardown when MtkBt rebinds across BT-enable cycles).
 */
public class MediaBridgeService extends Service {

    private static final String TAG = "Y1MediaBridge";

    private final IBinder mBinder = new AvrcpBinder();

    @Override
    public void onCreate() {
        super.onCreate();
        Log.d(TAG, "MediaBridgeService.onCreate (slim Binder host)");
    }

    @Override
    public IBinder onBind(Intent intent) {
        return mBinder;
    }

    @Override
    public boolean onUnbind(Intent intent) {
        return true;
    }

    /**
     * Minimal IBTAvrcpMusic + IMediaPlaybackService Binder. Skips
     * {@code strictModePolicy} + descriptor string and dispatches by transact
     * code — descriptors observed to drift across ROM variations, which
     * historically aborted {@code registerCallback} on {@code enforceInterface}
     * mismatches and left the cardinality at 0 forever.
     *
     * <p>Only {@code getCapabilities} (code 5) returns real data. Every other
     * code is ack-only: {@code writeNoException} + {@code true}. This is
     * sufficient because the C-side trampoline chain in
     * {@code libextavrcp_jni.so} handles every CT-visible AVRCP PDU directly
     * on the wire.
     */
    private static final class AvrcpBinder extends Binder {
        @Override
        protected boolean onTransact(int code, Parcel data, Parcel reply, int flags)
                throws RemoteException {
            if (code == INTERFACE_TRANSACTION) {
                return super.onTransact(code, data, reply, flags);
            }
            try {
                // Skip strictModePolicy + descriptor without enforcing them.
                data.readInt();
                data.readString();

                if (code == 5) {
                    // IBTAvrcpMusic.getCapabilities() -> byte[].
                    // 0x01 = EVENT_PLAYBACK_STATUS_CHANGED, 0x02 = EVENT_TRACK_CHANGED.
                    // MtkBt's adapter reads this to decide which events to
                    // REGISTER_NOTIFICATION for. Empty would cause some 1.3
                    // CTs to skip event subscription entirely.
                    if (reply != null) {
                        reply.writeNoException();
                        reply.writeByteArray(new byte[]{ 0x01, 0x02 });
                    }
                    return true;
                }

                if (reply != null) reply.writeNoException();
                return true;
            } catch (Throwable t) {
                Log.w(TAG, "onTransact code=" + code + ": " + t);
                if (reply != null) reply.writeNoException();
                return true;
            }
        }
    }
}

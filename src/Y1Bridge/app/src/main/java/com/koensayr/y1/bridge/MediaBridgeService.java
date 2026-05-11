package com.koensayr.y1.bridge;

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
 * <p>The music APK can't declare this intent-filter itself — see
 * {@code src/Y1Bridge/README.md} for the platform-key / JarVerifier rationale,
 * and {@code docs/ARCHITECTURE.md} for how MtkBt's bind dispatches into this
 * service. Returning {@code true} from {@code onUnbind} keeps the framework
 * service record alive across MtkBt rebinds.
 */
public class MediaBridgeService extends Service {

    private static final String TAG = "Y1Bridge";

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

    private static final class AvrcpBinder extends Binder {
        @Override
        protected boolean onTransact(int code, Parcel data, Parcel reply, int flags)
                throws RemoteException {
            if (code == INTERFACE_TRANSACTION) {
                return super.onTransact(code, data, reply, flags);
            }
            try {
                // Skip strictModePolicy + descriptor (descriptors drift across
                // ROM variations; enforcing them historically left mRegBit empty).
                data.readInt();
                data.readString();

                if (code == 5) {
                    // IBTAvrcpMusic.getCapabilities() — MtkBt registers
                    // notifications for whatever events we list.
                    // 0x01 = PLAYBACK_STATUS_CHANGED, 0x02 = TRACK_CHANGED.
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

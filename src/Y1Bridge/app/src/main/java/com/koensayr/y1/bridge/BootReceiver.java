package com.koensayr.y1.bridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * Starts {@link MediaBridgeService} at boot so MtkBt's first
 * {@code bindService} doesn't have to cold-start the process via
 * {@code BIND_AUTO_CREATE}.
 */
public class BootReceiver extends BroadcastReceiver {

    private static final String TAG = "Y1Bridge";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        Log.d(TAG, "BOOT_COMPLETED — starting MediaBridgeService");
        context.startService(new Intent(context, MediaBridgeService.class));
    }
}

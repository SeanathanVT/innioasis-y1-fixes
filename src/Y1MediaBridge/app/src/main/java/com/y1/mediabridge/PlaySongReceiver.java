package com.y1.mediabridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * PlaySongReceiver
 *
 * Static broadcast receiver for three Y1-internal lifecycle events:
 *   android.intent.action.BOOT_COMPLETED  → start MediaBridgeService
 *   android.intent.action.MY_PLAY_SONG    → wake service for a track event
 *   com.innioasis.y1.ABOUT_SHUT_DOWN      → notify service of imminent shutdown
 *
 * AVRCP transport commands (PLAY / PAUSE / STOP / NEXT / PREV) deliberately
 * do NOT route through this receiver. The music app's PlayControllerReceiver
 * declares an ACTION_MEDIA_BUTTON intent-filter at priority MAX_VALUE and is
 * the canonical media-button target; AudioService's ordered-broadcast
 * fallback delivers AVRCP-driven keys to it directly, where Patch E's
 * discrete arms route them to PlayerService.play(true) / pause(0x12, true) /
 * stop(). Lock-screen / system-UI media buttons reach the same receiver via
 * the RCC PendingIntent set up in MediaBridgeService.setupRemoteControlClient.
 */
public class PlaySongReceiver extends BroadcastReceiver {

    private static final String TAG = "Y1MediaBridge";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (action == null) return;

        if ("android.intent.action.BOOT_COMPLETED".equals(action)) {
            Log.d(TAG, "BOOT_COMPLETED — starting service");
            context.startService(new Intent(context, MediaBridgeService.class));
            return;
        }

        if ("android.intent.action.MY_PLAY_SONG".equals(action)) {
            // Don't log MY_PLAY_SONG — fires frequently and is noisy
            Intent i = new Intent(context, MediaBridgeService.class);
            i.setAction(MediaBridgeService.ACTION_PLAY_SONG);
            context.startService(i);
            return;
        }

        if ("com.innioasis.y1.ABOUT_SHUT_DOWN".equals(action)) {
            Log.d(TAG, "ABOUT_SHUT_DOWN");
            Intent i = new Intent(context, MediaBridgeService.class);
            i.setAction(MediaBridgeService.ACTION_SHUTDOWN);
            context.startService(i);
        }
    }
}

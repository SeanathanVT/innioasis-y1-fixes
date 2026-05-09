package com.y1.mediabridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;
import android.util.Log;
import android.view.KeyEvent;

/**
 * PlaySongReceiver
 *
 * Static broadcast receiver that handles three categories of events:
 *
 * 1. System lifecycle:
 *    android.intent.action.BOOT_COMPLETED  → start service
 *
 * 2. Y1 stock player events:
 *    android.intent.action.MY_PLAY_SONG    → wake service
 *    com.innioasis.y1.ABOUT_SHUT_DOWN      → notify service
 *
 * 3. Media button events from car / Bluetooth headset:
 *    android.intent.action.MEDIA_BUTTON    → re-broadcast for stock player
 *
 * The MEDIA_BUTTON handling is critical for transparent integration.
 * When Android receives AVRCP transport commands from the car, it routes
 * them here (via AudioManager.registerMediaButtonEventReceiver in the
 * service). We re-broadcast them so the stock player's PlayControllerReceiver
 * picks them up and controls playback as if a hardware key was pressed.
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
            return;
        }

        if (Intent.ACTION_MEDIA_BUTTON.equals(action)) {
            handleMediaButton(context, intent);
        }
    }

    /**
     * When a media button event comes in (from the car via Bluetooth, or from
     * a wired headset), forward it to the stock Y1 player as an ordered
     * broadcast. The stock player's PlayControllerReceiver (confirmed via
     * APK analysis) listens for ACTION_MEDIA_BUTTON with EXTRA_KEY_EVENT.
     *
     * We use sendOrderedBroadcast rather than sendBroadcast so the receivers
     * fire in priority order — the stock player is system app so it has
     * priority.
     */
    private void handleMediaButton(Context context, Intent intent) {
        KeyEvent key = intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT);
        if (key == null) return;

        int keyCode = key.getKeyCode();
        // Only forward recognized media keys
        if (keyCode != KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
         && keyCode != KeyEvent.KEYCODE_MEDIA_PLAY
         && keyCode != KeyEvent.KEYCODE_MEDIA_PAUSE
         && keyCode != KeyEvent.KEYCODE_MEDIA_NEXT
         && keyCode != KeyEvent.KEYCODE_MEDIA_PREVIOUS
         && keyCode != KeyEvent.KEYCODE_MEDIA_STOP) {
            return;
        }

        Log.d(TAG, "MEDIA_BUTTON keyCode=" + keyCode
                + " action=" + key.getAction() + " — forwarding to stock player");

        // Target the stock player's receiver explicitly to avoid
        // receiving our own re-broadcast (infinite loop).
        // PlayControllerReceiver confirmed from APK analysis.
        Intent forward = new Intent(Intent.ACTION_MEDIA_BUTTON);
        forward.putExtra(Intent.EXTRA_KEY_EVENT, key);
        forward.setComponent(new android.content.ComponentName(
                "com.innioasis.y1",
                "com.innioasis.y1.receiver.PlayControllerReceiver"));
        context.sendBroadcast(forward);
    }
}

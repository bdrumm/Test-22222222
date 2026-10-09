// The route in progress as a foreground service: keeps the sensors, the location fixes and the feed polls going
// with the screen off (the iOS app uses the location background mode), and carries the ongoing notification
// that stands in for the Live Activity.
package com.whichway.app.trip

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.whichway.app.MainActivity

class TripService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val session = TripSession.get(this)
        val n = notification(this, session.notificationTitle, session.notificationText)
        if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION) else startForeground(NOTIFICATION_ID, n)
        session.serviceStarted()
        return START_STICKY
    }

    override fun onDestroy() {
        TripSession.get(this).serviceStopped()
        super.onDestroy()
    }

    companion object {
        const val CHANNEL = "trip"
        const val NOTIFICATION_ID = 1

        fun channel(ctx: Context) {
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CHANNEL) == null) {
                nm.createNotificationChannel(NotificationChannel(CHANNEL, "Route in progress", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "The train to take, the change and the arrival while a route is on"
                    setShowBadge(false)
                })
            }
        }

        fun notification(ctx: Context, title: String, text: String): Notification {
            channel(ctx)
            val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            return NotificationCompat.Builder(ctx, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_menu_directions)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setSilent(true)
                .setContentIntent(open)
                .setCategory(NotificationCompat.CATEGORY_NAVIGATION)
                .build()
        }

        fun update(ctx: Context, title: String, text: String) {
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.notify(NOTIFICATION_ID, notification(ctx, title, text))
        }

        fun start(ctx: Context) {
            val i = Intent(ctx, TripService::class.java)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }

        fun stop(ctx: Context) { ctx.stopService(Intent(ctx, TripService::class.java)) }
    }
}

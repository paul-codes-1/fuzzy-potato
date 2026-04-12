import { useState, useEffect, useCallback } from 'react'

/**
 * Hook for managing Web Push notification subscriptions.
 *
 * Provides:
 *  - permission: 'default' | 'granted' | 'denied' | 'unsupported'
 *  - isSubscribed: boolean
 *  - subscribe(): Promise<void>
 *  - unsubscribe(): Promise<void>
 *  - sendTest(): Promise<void>
 *  - error: string | null
 *  - loading: boolean
 */
export function usePushNotifications() {
  const [permission, setPermission] = useState('default')
  const [isSubscribed, setIsSubscribed] = useState(false)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const apiBase = import.meta.env.VITE_API_BASE || ''

  const getHeaders = useCallback(() => {
    const headers = { 'Content-Type': 'application/json' }
    const apiKey = sessionStorage.getItem('api_key')
    if (apiKey) {
      headers['X-API-Key'] = apiKey
    }
    return headers
  }, [])

  // Check support and current state on mount
  useEffect(() => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      setPermission('unsupported')
      return
    }

    setPermission(Notification.permission)

    // Check if we already have an active subscription
    navigator.serviceWorker.ready.then((registration) => {
      registration.pushManager.getSubscription().then((sub) => {
        setIsSubscribed(sub !== null)
      })
    })
  }, [])

  /**
   * Fetch the VAPID public key from the server.
   */
  const fetchVapidKey = useCallback(async () => {
    const res = await fetch(`${apiBase}/api/v1/push/vapid-key`, {
      headers: getHeaders(),
    })
    if (!res.ok) {
      throw new Error('Failed to fetch VAPID key from server.')
    }
    const data = await res.json()
    return data.public_key
  }, [apiBase, getHeaders])

  /**
   * Convert a URL-safe base64 string to a Uint8Array for applicationServerKey.
   */
  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
    const raw = window.atob(base64)
    const array = new Uint8Array(raw.length)
    for (let i = 0; i < raw.length; i++) {
      array[i] = raw.charCodeAt(i)
    }
    return array
  }

  /**
   * Subscribe to push notifications.
   * Requests notification permission if not yet granted, then
   * creates a push subscription and registers it with the backend.
   */
  const subscribe = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      if (permission === 'unsupported') {
        throw new Error('Push notifications are not supported in this browser.')
      }

      // Request permission
      const result = await Notification.requestPermission()
      setPermission(result)

      if (result !== 'granted') {
        throw new Error('Notification permission was denied.')
      }

      // Get VAPID key from server
      const vapidKey = await fetchVapidKey()

      // Subscribe via the push manager
      const registration = await navigator.serviceWorker.ready
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey),
      })

      const subJson = subscription.toJSON()

      // Register subscription with the backend
      const res = await fetch(`${apiBase}/api/v1/push/subscribe`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          endpoint: subJson.endpoint,
          keys: {
            p256dh: subJson.keys.p256dh,
            auth: subJson.keys.auth,
          },
        }),
      })

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || 'Failed to register push subscription.')
      }

      setIsSubscribed(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [permission, apiBase, getHeaders, fetchVapidKey])

  /**
   * Unsubscribe from push notifications.
   */
  const unsubscribe = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      const registration = await navigator.serviceWorker.ready
      const subscription = await registration.pushManager.getSubscription()

      if (subscription) {
        const endpoint = subscription.endpoint

        // Unsubscribe locally
        await subscription.unsubscribe()

        // Remove from backend
        await fetch(`${apiBase}/api/v1/push/subscribe`, {
          method: 'DELETE',
          headers: getHeaders(),
          body: JSON.stringify({ endpoint }),
        })
      }

      setIsSubscribed(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [apiBase, getHeaders])

  /**
   * Send a test push notification to verify the subscription works.
   */
  const sendTest = useCallback(async () => {
    setError(null)
    try {
      const res = await fetch(`${apiBase}/api/v1/push/test`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          title: 'CivicLens Test',
          body: 'Push notifications are working!',
        }),
      })

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || 'Failed to send test notification.')
      }
    } catch (err) {
      setError(err.message)
    }
  }, [apiBase, getHeaders])

  return {
    permission,
    isSubscribed,
    subscribe,
    unsubscribe,
    sendTest,
    error,
    loading,
  }
}

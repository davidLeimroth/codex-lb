# Preserve main HTTP logging when metrics start

The secondary Uvicorn metrics configuration resets process-wide logger handlers and raises the shared access logger to WARNING. With metrics enabled, real main API requests consequently lose their JSON access records.

Preserve the primary server's logging configuration when constructing the secondary server. Keep the native Prometheus application and signal-neutral lifecycle. Metrics scrapes may use the same JSON access formatter. Apply the change as an exact-context overlay on the pinned production runtime.

Validate one real HTTP regression covering both servers, then rebuild the runtime once. Reuse unchanged adapter, worker, SSE and backend results. Production promotion still requires the bounded synthetic image smoke and final authenticated client checks.

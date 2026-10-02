# Independent runtime review and real HTTP verification

## Review inputs and boundary

The review read only independently written successor modules under `apps/Zendure-AppDaemon-Proxy/`, the successor data/interface contracts, synthetic tests, and the installed genuine aiohttp/AppDaemon packages. Predecessor source, Node-RED flows and upstream implementations were excluded.

`tests/test_real_http.py` runs `scripts/verify_http_runtime.py` in the successor `.venv` interpreter as a separate process. The subprocess avoids the synthetic suite's fake aiohttp/AppDaemon modules. The helper asserts genuine package imports and binds both HTTP servers to `127.0.0.1` on ephemeral ports. No Home Assistant or physical device is contacted.

## Verified behavior

With genuine aiohttp 3.11.18 and AppDaemon 4.5.13, both direct-returning and Task-returning mocked AppDaemon methods passed:

- `ZendureProxy.initialize()` registers three endpoints, three UI routes and four timer callbacks. The serial-number bootstrap task populates the synthetic device serial before the coalescing measurement begins.
- Eight concurrent requests across `/properties/report` and `/endpoint/properties/report` share one upstream GET before completion and receive the same synthetic fresh counter.
- Both HTTP write routes send the physical synthetic serial number and bounded power; array payloads and invalid JSON return HTTP 400.
- Registered report/write callbacks and the compatibility callback invoke real successor request processing.
- Proxy sensor state publication creates the expected serial sensor; 93 entities exist in each method-return case.
- Separate HTTP connection pools keep one device GET/POST worker active at a time.
- An upstream HTTP 500 returns an age-qualified cache response; an expired cache returns HTTP 504; a fresh response after failure recovers.
- Termination while a genuine upstream socket waits cancels report callers, closes both device sessions and the proxy listener, stops worker/processor tasks, cancels all registered timers, and deregisters every endpoint/route.
- No unfinished asynchronous helper tasks remain after both cases.

Checks run:

- `.venv/bin/python scripts/verify_http_runtime.py`: passed both cases.
- `python3 -m pytest tests/test_real_http.py -q`: passed.

The test imports the genuine AppDaemon base class and replaces methods on an instance created with `__new__`. The check is a mocked AppDaemon boundary and real aiohttp integration test. The check does not instantiate an AppDaemon daemon, Home Assistant Core, the MQTT integration, or HACS.

## Review findings sent to owners

- `zendure_proxy_post_handler._allocation()` could index `ordered[0]` when every eligible device's `soc_limit` forbids the requested direction. The owning implementer added an empty-allocation guard and two charge/discharge regression cases; the owning implementer reported the regression suite passed.
- `zendure_proxy_get_handler.execute_get()` accepted every dictionary as a usable HTTP report, including `{}` and error dictionaries. The owning implementer added report validation. Genuine HTTP 200 empty/error-shaped reports now preserve `last_response` and serve the cache; both method-return cases passed the new regression checks.
- `ZendureProxy.initialize()` starts device workers/sessions before the local listener is bound. An exception during registration or binding could leave runtime resources active. The owning implementer added failure cleanup. A genuine occupied loopback port now preserves the bind `OSError`, closes client sessions/workers and queue, deregisters all endpoints/routes, and removes runner sites. Both method-return cases passed, including a subsequent repeated `terminate()`.

The helper additionally injects a real HTTP 503 POST response. The public acknowledgement remains compatible, the internal failure marker is removed from the public reply, and the device records `last_post_error: POST HTTP 503` with a warning log. A following successful GET restores device health. After the background serial-number bootstrap was added, the helper waits for bootstrap completion and verifies the serial. The eight-to-one report coalescing assertion remains unchanged. The final helper passed both method-return cases with twelve upstream GETs and six upstream POSTs per case, plus both occupied-port failure cleanup cases. The subprocess test passed in 1.19 seconds.

# Request queue, device client, and standby implementation inputs

The new request queue, HTTP device client, and standby scheduler use:

- `audit/interface-signatures.json`: public helper and class-method signatures.
- `audit/data-contract.json`: configuration and runtime-state names.
- `tests/test_node_red_repeat_standby_serial_simulated.py`: synthetic device URLs, separate/shared session behavior, idle-session closure, standby guards, delay scheduling, session closure and duplicate zero-event suppression.
- `tests/test_appdaemon_proxy_release_gate.py`: queue future use, HTTP error status and exception handling, POST failure markers, fake aiohttp request/session API.
- `tests/test_node_red_post_power_compat.py`: standby protection for relay-saver devices.
- `tests/node_red_expected.py`: synthetic standby properties contract.
- The retained `zendure_proxy_health.py` functions `post_failure_response`, `post_response_failed`, `record_post_results` and the retained `zendure_proxy_metrics.py` methods `start_outgoing`, `finish_outgoing`, `set_outgoing_queue_depth`.
- Coordinator compatibility requirements: queued GET coalescing, POST deduplication by property key-set with the latest request and individual skipped futures, one outgoing worker per physical device, recent GET and transition standby guards.
- The aiohttp package API guide fetched with `chub get aiohttp/package --lang py`, version 3.13.3, updated 2026-03-11. The guide cites https://docs.aiohttp.org/en/stable/client_quickstart.html and https://docs.aiohttp.org/en/stable/client_reference.html.

The request batching, worker serialization, session lifecycle and standby scheduling code were written independently. No predecessor core modules, Node-RED flows or upstream implementations were opened for these modules.

Verification: `compileall` passed for all five assigned modules. The focused pytest run of configuration, URL, connection and standby tests passed 16 tests. The focused release-gate run for `DeviceClientPostFailureTests` and configuration defaults passed 3 tests. An asynchronous manual simulation checked cancelled GET filtering, POST latest-value deduplication and skipped futures, empty queue depths, sequential GET/POST execution with one active request, and cancellation of active/queued callers during `DeviceClient.close()`.

Runtime integration also requested `async RequestQueue.close()` to cancel pending callers during termination. The new method cancels queued futures, wakes a waiting drain, and makes later enqueue calls return cancelled futures. Direct shutdown assertions passed for pending GET and POST futures, later callers, zero depths, and drain cancellation.

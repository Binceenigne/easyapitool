The public gateway, distribution keys, USD budgets, RPM and proxy exit remain in Go.
This private worker runs the shared `mirra_agents` package using the official
OpenAI Agents SDK (`Agent`, `Runner`, `WebSearchTool`). It must never be published
through Nginx or listen outside loopback.

Deploy the repository-root `mirra_agents/` package beside `agent_runtime/` in
`/opt/imggen-agent-runtime/releases/sdk-1.2.6`, publish it through the
`/opt/imggen-agent-runtime/current` symlink, create the separate
`/opt/imggen-agent-runtime/venv-sdk-0.25.0` virtualenv and install the shared
package's pinned dependencies with `venv-sdk-0.25.0/bin/python -m pip install -r current/agent_runtime/requirements.txt`
from `/opt/imggen-agent-runtime`. Do not add them to the legacy
FastAPI environment. Install the included systemd unit and create a private
`/etc/imggen-agent-runtime.env` from the example for the worker. Put matching
Go settings in `/etc/imggen-gateway/agent-sdk.env` and add it through a Go
systemd drop-in. Replace the token; the example is not
a production credential. Keep the public service's other settings unchanged.

When `MIRRA_AGENT_WORKER_URL` is configured, Go rewrite operations require this
SDK worker and never fall back silently. Omitting the URL retains the old
adapter only for installations that have not yet migrated. Default Agent and
image attempts are three. The SDK owns Agent model-turn retries, with OpenAI's
hidden retries disabled. Go owns retries for each failed image; completed
images are not regenerated. Native API requests have no extra logical retry.

The worker receives a short-lived `lease-…` capability rather than an OpenAI or
distribution key. Each SDK request goes to Go's loopback
`/internal/agent/v1/responses`, where the original caller IP, current key policy,
hourly account affinity, per-request reservation, RPM and actual usage are
checked independently. A lease permits one configured model/effort and only
the requested web search tool (at most three hosted tool calls per response,
preserving any stricter request limit). Direct official and legacy keys use their
existing Go upstream route and egress proxy; Python cannot choose an external
base URL.

`POST /run` takes `{requestId, request: {...AgentRequest fields...}}` and streams
NDJSON. `POST /cancel` takes `{requestId}`; both need the shared Bearer token.
`GET /healthz` is also authenticated. The Go task stream forwards deltas and
`request_retry` events. Terminal `errorDetails` contains stage, code, attempts,
maxAttempts, cause/lastCause, HTTP status and a diagnostic ID. Authorization,
quota, RPM, invalid parameters and cancellation are never retried. Retry-After
is respected for transient upstream failures; hints beyond the task's automatic
waiting window end with an explicit cause instead of retrying early. Worker busy
admissions and demonstrably unsent connections have separate bounded pre-run
retries; an accepted SDK run is never replayed by Go. No request credentials are
logged. Encrypted terminal task events are retained for seven days (at most
10,000), scoped to the owning device, so precise failures survive service restarts.

Run `go test ./...` and `python -m unittest discover -s agent_runtime -p test_worker.py`.
For the real SDK integration, set `MIRRA_TEST_SDK_PYTHON` to the dedicated Python
executable and run `go test -run TestRealSDKWorkerThroughMeteredGoLease .`.
That test uses a local TLS fixture and makes no paid external model requests.

Visual research uses OpenAI's `search_content_types: [image, text]` and raw
`web_search_call.results`. Only image_result URLs seen on Go's actual completed
provider responses may be downloaded. SDK `inspect_visual_references` and
`select_visual_references` function tools call the lease-authenticated loopback
`/internal/agent/v1/reference-images` route. Go validates public HTTPS addresses,
image bytes and pixel limits, retains device ownership, and appends only verified
selected assets to image edits. No API credential is attached to image hosts.
Original references and inspected pictures reach the Agent as bounded JPEG
previews; their combined count obeys the tier. Sources, selected images, actual
turns and request/tool counts are returned separately and saved with task results.
SVG permits allowed visual research but cannot adopt raster references.

Planning budgets follow the selected depth with at most two extra visual I/O
turns and one continuation turn, capped at 32. Worker input is limited to 4 MiB
and the private Responses bridge to 16 MiB. Each preview is at most 96 KiB.
Reference callbacks use cancellable async HTTP, without a blocking executor.
Run `go test -run TestRealSDKVisualToolLoopAdoptsVerifiedImageThroughGo .` with
the SDK Python variable for a real SDK inspect/select/image-multipart test that
uses only local TLS fixtures. Worker tests include `test_visual.py` as well.

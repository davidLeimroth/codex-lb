The heartbeat wrapper owns one upstream read. A timeout yields a
heartbeat without cancelling that read. Previously cleanup only awaited reads
that were unfinished; a read that finished during the yield could retain an
unobserved StopAsyncIteration or upstream error. Always awaiting the owned task
in the existing cleanup block fixes this race without changing the transport.

Source CI also exercised Python 3.14, whose cancelled shield reports a reader
exception even when the owner later observes it. Use asyncio.wait's timeout
without cancelling a shield wrapper. Upstream TimeoutError must propagate as
an upstream error rather than being confused with an idle heartbeat timeout.

For example, upstream EOF after a heartbeat followed by a client disconnect
must close quietly rather than log an asyncio application error. The change
does not close an unrelated iterator, alter account affinity, retry requests,
or change reservation settlement. Existing cancellation handling is retained.

The production image preserves native-thread patches. Deliver this fix with a
separate exact-context patch rather than replacing the proxy service modules.

Release checks also found fixed HIGH CVEs in zlib1g and dnspython. The standard
image adds zlib1g to its existing named security upgrades and locks dnspython to
2.9.2. A scan of the actual preserved image also found inherited fixable High/Critical
findings in apt, gzip, PCRE2, SQLite, OpenSSL, Perl, aiohttp, anyio, Brotli, certifi
and urllib3. The runtime upgrades those named Debian packages and six named Python
packages (including dnspython) to the existing uv.lock versions, with locked wheel
hashes, no dependency resolution and the explicit /opt/venv interpreter. It removes
pip and restores USER app. The image workflow applies the existing standard-image
Trivy gate to this actual preserved runtime before publication. The 590-file
application-source comparison excludes site-packages intentionally.

Production promotion remains held. Talos policy at da7423e83cd1ebf27004de1b7f3de35d737ad11c
requires codex-lb's isolated PostgreSQL/volume restore, migration, login, protocol
and telemetry qualification. The fixed recipe registry does not register codex-lb
and the stateful executor refuses it. Source/image regression results are not
staging qualification. Complete the reviewed stateful qualification recipe and
its isolated run before a reviewed image-only GitOps promotion. No production,
storage, routing credentials, billing settings or network policies were mutated.

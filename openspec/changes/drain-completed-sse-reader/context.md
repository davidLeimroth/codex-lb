The heartbeat wrapper owns one shielded upstream read. A timeout yields a
heartbeat without cancelling that read. Previously cleanup only awaited reads
that were unfinished; a read that finished during the yield could retain an
unobserved StopAsyncIteration or upstream error. Always awaiting the owned task
in the existing cleanup block fixes this race without changing the transport.

For example, upstream EOF after a heartbeat followed by a client disconnect
must close quietly rather than log an asyncio application error. The change
does not close an unrelated iterator, alter account affinity, retry requests,
or change reservation settlement. Existing cancellation handling is retained.

The production image preserves native-thread patches. Deliver this fix with a
separate exact-context patch rather than replacing the proxy service modules.

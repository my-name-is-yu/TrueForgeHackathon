# Asset Autopsy storage contract

Asset Autopsy accepts one size ceiling for every content-addressed object:
`MAX_OBJECT_BYTES = 67,108,864` bytes (64 MiB). This covers the fixed MJCF,
canonical manifests and results, bounded experiment traces, partial-run evidence,
and the existing 64 MiB decoded-image ceiling. Object kinds do not have separate
limits.

Writes hash and count bytes while copying them to a temporary file in the object
tree. A write at the limit is accepted. A larger write raises `ValidationError`;
an expected-digest mismatch raises `ObjectIntegrityError`. Both failures remove
the temporary file and publish no new canonical object. The canonical path is the
lowercase SHA-256 of the exact bytes.

Reads return bytes only after enforcing the same size ceiling and exact path hash.
Integrity-only checks stream the object to compute its digest and size, so ledger
artifact verification does not materialize the object. Artifact references are
canonicalized into immutable ledger snapshots and must bind both the digest and
the exact byte count.

## Local durability operations

For a new object, the implementation performs these local filesystem operations:

1. flush and `fsync` the temporary file contents;
2. verify the streamed size and digest;
3. atomically rename the temporary file to its hash path; and
4. `fsync` the destination directory and its object-tree ancestors through the
   configured object root.

It does not traverse or claim synchronization of ancestors above the configured
object root. On-disk SQLite connections use WAL journal mode and
`synchronous = FULL` for ledger transactions.

The object store and SQLite are separate stores. Asset Autopsy does not provide an atomic
commit across them: an interruption may leave an unreferenced valid object.
Before a ledger mutation commits, every referenced object is streamed and checked,
and later corruption is detected on verification or read. These operations do not
claim distributed durability, replication, quota enforcement, garbage collection,
or recovery from loss of the local filesystem or database.

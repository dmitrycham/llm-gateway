# ADR-015: `BlobStore` for source files, a volume in Phase 1 and versioned S3 later

- Status: Accepted
- Date: 2026-11-26

## Context

Uploaded source files are written by the AI Service and read by the worker during
indexing (spec §4.5). The database stores `content_hash` (`sha256`) and
`storage_path` for each document version (spec §8.1), and indexes refer to versions,
not to file paths.

In Phase 1 both processes run in Compose and can share a named volume. In Kubernetes
(Phases 2–3) the AI Service and the worker are separate pods, and a disk shared
between pods (`ReadWriteMany`) is not available on a single-node k3s with local
storage and is fragile elsewhere.

Phase 2 also requires a document recovery demo: restore the database to a time before
a document was deleted, and bring back the file as it was at that time. A database
restore alone gives a row that points at a file that no longer exists.

## Decision

Python accesses source files only through a `BlobStore` interface with two
implementations.

- **Interface.** `put(key, stream)`, `open(key)`, `delete(key)`, `exists(key)` and
  `iter_objects(prefix)`, which yields one `(key, last_modified)` pair per object
  (file mtime on the volume, `LastModified` on S3). The S3 implementation follows
  `NextContinuationToken` through every page, because one `ListObjectsV2` call
  returns at most 1,000 keys. Callers never build file paths.
- **Blob registry.** The table `ai.blobs` (`id`, `workspace_id`, `content_hash`,
  `key`, `state` `live`/`retired`, `created_at`) records every physical copy.
  `ai.document_versions` references `blob_id` instead of `storage_path`; versions with
  the same content in one workspace share one live blob. Both are changes to spec
  §8.1.
- **Key.** `workspaces/{workspace_id}/sha256/{content_hash}/{blob_id}`. Each physical
  copy gets a new key, and a key is never written again once its blob is retired. A
  late or retried delete can only hit the copy it was meant for, so on S3 a delete
  marker never hides a newer object.
- **Content lock.** Steps that pick, create or retire a blob take
  `pg_advisory_xact_lock(hashtextextended(workspace_id::text || ':' || content_hash, 0))`
  in their transaction, so an upload and a cleanup of the same content never
  interleave.
- **Upload.** The upload takes a session advisory lock on its `upload_id`, on a
  connection it keeps for itself and does not return to the pool. It streams the file
  to the temporary key `tmp/{upload_id}` and hashes it, outside any transaction.
  Then: begin → content lock →
  1. if the workspace has a non-deleted document with this `content_hash` (or the
     same version of the same document), drop the temporary file and return its
     `document_id`, `version_id` and `job_id` (spec §4.5 step 4). A retry after a lost
     response creates nothing new;
  2. otherwise reference the `live` blob with this hash if there is one and drop the
     temporary file, or insert a new `live` blob and move the file to its key;
  3. if no target `document_id` was given, create the document; otherwise lock the
     target row with `SELECT ... FOR UPDATE` before taking the next `version` number,
     because two concurrent uploads with different content take different content
     locks and could pick the same number → insert the version under that document →
     enqueue the job → commit. The previous active version stays searchable until
     the new one is activated (spec §4.5 step 7).

  The `upload_id` lock is released with `pg_advisory_unlock` in a `finally` block on
  the same connection, on success, failure and cancellation; if the connection breaks,
  PostgreSQL releases it. A committed version always has its object.
- **Cleanup.** After a document change commits: begin → content lock → check that no
  live version references the blob → set it to `retired` → commit. `DeleteObject`
  runs after the commit, outside the lock, and the blob row is removed once it
  succeeds. A failed delete leaves a `retired` row for the janitor to retry.
- **Janitor.** Runs cleanup for `live` blobs that no live version references and
  retries deletes of `retired` blobs. It also walks `iter_objects("tmp/")` and
  `iter_objects("workspaces/")` and removes two kinds of leftover files, both only
  when `last_modified` is older than a grace period:
  - a temporary file: begin → `pg_try_advisory_xact_lock(upload_id)` → delete the
    file only if the lock was acquired → commit. An upload in progress holds the lock,
    so its file is never removed; a dead uploader holds nothing. The transaction lock
    is released at commit and cannot leak;
  - a final key with no blob row, left by a crash between the move and the commit:
    begin → content lock → recheck `ai.blobs` by exact `key` → delete the file if no
    row has it → commit. An upload that is still committing holds the content lock, so
    the janitor waits and then sees its row.
- **Phase 1: `FilesystemBlobStore`** on the named volume shared by `ai-service` and
  `ai-worker`. The final step is an atomic rename on the same file system. There is no
  object versioning in Phase 1; the volume is backed up with the database volume.
- **Phases 2–3: `S3BlobStore`** against an S3-compatible API with bucket versioning
  on. On S3 the final step is a copy from the temporary key and a delete of it.
  Phase 2 uses local S3-compatible storage, Phase 3 Amazon S3 (ADR-012).
- **Storage requirement.** The local service must implement bucket versioning,
  `ListObjectVersions` and lifecycle rules for noncurrent versions. Garage does not
  implement bucket versioning, so it is not an option. MinIO is used if its licence
  and image status are acceptable when Phase 2 starts; otherwise another service that
  passes these checks is chosen then. This replaces "otherwise Garage" in spec §13.
- **Retention.** A lifecycle rule expires noncurrent object versions no earlier than
  the PITR window of the database backups (ADR-014), so every point the database can
  be restored to has its files.
- **Recovery.** After a database PITR to time `T` (ADR-014), each file is restored from
  the object version that was current at `T`. The demo checks that its `sha256` equals
  the restored `content_hash`.

## Consequences

Trade-off: document recovery becomes a database PITR plus the object version from the
same moment, and the AI Service and worker do not share a disk, in exchange for an
interface and two implementations, an orphan janitor, and storage that is paid for as
long as old versions are kept.

Positive:

- No `ReadWriteMany` volume in Kubernetes; pods scale and move independently.
- Deleted or overwritten files can be restored to the same moment as the database.
- The application code is the same for local S3-compatible storage and Amazon S3.
- A re-upload of the same content reuses the live blob and stores nothing new.

Negative:

- Two stores are written without a shared transaction. The content lock, blob states
  and write order keep rows valid, but leftover objects appear and need the janitor.
- An extra table and blob states to maintain, and spec §8.1 changes.
- An upload holds a database transaction open while the file moves to its final key
  (a rename, or a server-side copy of up to 10 MB), and uploads and cleanups of the
  same content wait for each other.
- Each upload in progress holds one database connection for its `upload_id` lock,
  from the first byte to the commit. Concurrent uploads must stay well below the AI
  Service pool size (20, spec §10).
- Each janitor run lists every final key to find files without a blob row. This is
  cheap at the project's volume (up to 200 MB per workspace) but grows with storage.
- Old versions use space until the lifecycle rule expires them.
- Phase 1 has no point-in-time file recovery.
- The same content in two workspaces is stored twice, because the key includes the
  workspace. This is deliberate: deleting one workspace's data never touches another's.

## Alternatives considered

- **Files in PostgreSQL (`bytea` or large objects) (rejected).** One transaction and
  PITR for free, but documents up to 10 MB inflate the database, its WAL archive and
  every backup, and slow restores.
- **A shared `ReadWriteMany` volume in Kubernetes (rejected).** Needs NFS or a
  distributed file system that single-node k3s does not have, and it has no point-in-time
  recovery.
- **One content-addressed key per hash, without a blob ID (rejected).** A delete sent
  just before a process crash can reach S3 after a new upload of the same content has
  committed, and its delete marker hides the new object.
- **Versioning in the application (a new key per write, nothing deleted) (rejected).**
  Possible, but it reimplements bucket versioning and still needs a cleanup policy.
- **S3 from Phase 1 (rejected).** One more container in the base stack for a feature
  Phase 1 does not need. The interface keeps the switch cheap.
- **Garage as the local S3 service (rejected).** It does not implement bucket
  versioning, which the recovery demo depends on.

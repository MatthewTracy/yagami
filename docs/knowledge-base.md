# Folder-indexed knowledge base

Yagami can index `.pdf`, `.md`, `.markdown`, `.txt`, and `.log` files from an
approved local root. It chunks and embeds those files with the configured
Ollama embedding model and stores them separately from conversation memory.
The `kb.recall` skill retrieves up to five relevant passages during a tool
turn.

Set the roots the service is allowed to read:

```powershell
$env:YAGAMI_KB_ROOTS = "C:\Users\you\Documents\project-docs"
yagami serve
```

For a service deployment, set the equivalent environment value to one or more
absolute paths separated by the platform path separator. Yagami rejects index
requests outside these roots.

## Index and manage documents

Index a folder recursively:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/kb/index `
  -H "Authorization: Bearer your-yagami-project-key" `
  -H "Content-Type: application/json" `
  -d '{"path":"C:\\Users\\you\\Documents\\project-docs","wait":true}'
```

List indexed sources:

```powershell
curl.exe http://127.0.0.1:8000/api/kb `
  -H "Authorization: Bearer your-yagami-project-key"
```

Delete one source:

```powershell
curl.exe -X DELETE "http://127.0.0.1:8000/api/kb/source?path=C%3A%5CUsers%5Cyou%5CDocuments%5Cproject-docs%5Creadme.md" `
  -H "Authorization: Bearer your-yagami-project-key"
```

Re-indexing a file replaces its chunks rather than duplicating them. Indexing
is serialized, file sizes and extracted text are bounded, and symbolic-link or
path traversal outside configured roots is rejected.

## Privacy considerations

Index only material authorized for the service account and deployment. The
retrieval itself is local, but a retrieved passage becomes model context and
therefore inherits the current request's model route.

The built-in `kb.recall` skill has a conservative sensitivity ceiling and
will not run in a turn already labeled sensitive. That cannot determine the
sensitivity of every document passage in advance. For confidential corpora,
use caller-declared sensitivity and a local-only policy so retrieved content
cannot be sent to a cloud backend. Validate the behavior with representative
documents before production use.

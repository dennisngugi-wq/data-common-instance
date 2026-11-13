# data/inbox

Drop files here for feeds that read a local file (`fetch: {type: excel|csv, path: "data/inbox/<file>"}`).
The nightly run re-reads them, so replacing a file with a newer version updates the site the next night
(or now: `bash scripts/refresh-now.sh`). Files here are git-ignored. Prefer a URL or a Google Sheet when the
publisher has one, so the data stays in sync without anyone copying files.

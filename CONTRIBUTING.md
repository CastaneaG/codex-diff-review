# Contributing

Use Python 3.10+ and Git. The runtime has no third-party packages and the browser app has no build step.

Run `python -m unittest discover -s tests -v`. For UI changes, create a disposable repository with `python scripts/create_demo.py`, start a session, and check both diff layouts, full-file content, old/new line selection, Shift ranges, saved comments, reload, and report copying.

Keep snapshot creation read-only with respect to source repositories. Do not add project snapshots, local session directories, tokens, private code or user reports to this repository. Screenshots and fixtures must use the public demo.

Line anchors and report hashes refer to a frozen snapshot. Changes must preserve that contract and must not silently apply old comments or reviewed flags to new content.

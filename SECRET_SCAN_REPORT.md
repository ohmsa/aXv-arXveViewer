# Secret scan report

Scan date: 2026-09-09

Scope: all tracked files and untracked files proposed for publication.

Checks performed:

- Common private-key headers
- AWS, Google, GitHub, OpenAI, and Slack token formats
- Generic API key, access token, client secret, and password assignments
- User-specific absolute paths
- Secret-like filenames and binary/model extensions
- Release archive entry inventory
- Local usernames, drive paths, archive names, and previously supplied sample paths

Result: no embedded secret, credential file, user-specific absolute path, AI model, external executable, or DLL was detected.

The scan matched password-handling variable names in viewer.py. Those are program logic and do not contain credentials. Runtime settings, logs, password lists, AI assets, ONNX/bin/param model files, and optional EXE/DLL binaries are excluded by `.gitignore`. The AI download helper contains only public GitHub repository identifiers and no credentials.

# Friction log
1. **mcp 2.x broke `from mcp.server.fastmcp import FastMCP`.** Task: install `mcp`. Expected: v1 API. Actual: ModuleNotFoundError (FastMCP renamed MCPServer). Severity: high. Workaround: pin `mcp<2`. Suggestion: pin versions in templates and migrate deliberately.
2. **Mixed mcp installs.** `pip install` over a system Python left a half-upgraded package (ImportError TASK_STATUS_COMPLETED). Severity: medium. Workaround: clean venv.
3. **Windows nested folder after unzip** caused `requirements.txt not found`. Severity: low. Suggestion: document `dir` check in README.
4. **Linter caught real bugs in my own output:** generated add-on text never mentioned availability; linter assumed salon service ids; test server advertised wrong base URL. All fixed.
5. **Alexa+ docs search returned partial snippets**: manifest field details incomplete; marked TODO(verify).

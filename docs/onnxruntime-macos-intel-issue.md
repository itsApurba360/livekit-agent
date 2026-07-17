# Research: ONNX Runtime macOS Intel (x86_64) Installation Failure

## Problem Description
Running `uv sync` on an Intel Mac (`x86_64`) fails with the following error:
```
error: Distribution `onnxruntime==1.27.0 @ registry+https://pypi.org/simple` can't be installed because it doesn't have a source distribution or wheel for the current platform
```

---

## Root Cause Analysis

### 1. Host Environment Check
*   **Operating System**: macOS 15.7.7
*   **Architecture**: `x86_64` (Genuine Intel Core i7-8850H CPU)
*   **Python Virtual Environment**: Python 3.12.11 (x86_64)

### 2. ONNX Runtime Version History on PyPI (macOS Wheels)
Querying the PyPI API for releases shows that Microsoft changed its macOS packaging strategy starting with version `1.24.x`/`1.25.0`:
*   **1.19.0 to 1.22.0**: Published `universal2` wheels (supporting both `x86_64` and `arm64` architectures in a single binary).
*   **1.23.0**: Published separate `x86_64` and `arm64` wheels for macOS.
*   **1.24.3**: Published **only `arm64`** wheels for macOS.
*   **1.25.0 to 1.27.0**: Published **only `arm64`** wheels for macOS.

Since PyPI does not distribute a source package (`sdist`) for `onnxruntime`, `uv` cannot compile it dynamically and must fail.

### 3. Dependency Path
The `onnxruntime` package is required by the `livekit-plugins-silero` package:
```toml
# pyproject.toml
dependencies = [
    ...
    "livekit-plugins-silero",
    ...
]
```
For Python version `>= 3.11` (the environment runs 3.12), the dependency solver resolved and locked `onnxruntime` to `1.27.0` in `uv.lock`.

---

## Possible Ways to Fix

Here are the possible options to resolve the issue. *(Note: As requested, none of these have been implemented yet.)*

### Option 1: Remove the Unused Dependency (Recommended)
`livekit-plugins-silero` is currently declared in `pyproject.toml`, but it is **not imported or used anywhere** in the codebase.
*   **Why**: Outbound campaigns run on LiveKit Cloud (which handles its own VAD/worker environment), and the local call API does not require Silero VAD.
*   **How**: Remove `"livekit-plugins-silero"` from the `dependencies` list in `pyproject.toml`, and run `uv lock` / `uv sync`.

### Option 2: Constrain/Downgrade `onnxruntime` via UV Overrides
If you need to keep Silero but make it installable on Intel macOS, you can force `uv` to use an older version of `onnxruntime` that still compiles/releases `x86_64` or `universal2` wheels (e.g., `< 1.24.0`).
*   **How**: Add the following tool override in `pyproject.toml` and re-sync:
    ```toml
    [tool.uv.overrides]
    onnxruntime = "<1.24.0"
    ```

### Option 3: Compile `onnxruntime` Manually from Source
Compile `onnxruntime` 1.27.0 manually from Microsoft's source code and install it.
*   **How**: Clone `microsoft/onnxruntime`, compile it using CMake and compiler toolchains targeting `x86_64`, build a wheel locally, and instruct `uv` to use the local wheel.
*   **Downside**: High build complexity and compile times.

### Option 4: Switch Python Platform/Architecture (N/A)
If this were an Apple Silicon Mac running Intel Python via translation, switching to native `arm64` Python would resolve it. However, since the host CPU is physically a genuine Intel processor (`i7-8850H`), this option is not applicable.

# AIF Agent Sandbox

An overlay image package for [quicksand](https://github.com/microsoft/quicksand) that provides a Python 3.12 environment with common AI agent tools pre-installed. Built on top of `quicksand-ubuntu`.

## Installation

```bash
quicksand install aif-agent-sandbox
```

## Usage

### Simple (recommended)

```python
import asyncio
from aif_agent_sandbox import AIFAgentSandbox

async def main():
    async with AIFAgentSandbox() as sb:
        result = await sb.execute("python3 --version")
        print(result.stdout)

asyncio.run(main())
```

### With custom config

```python
from quicksand_core import Sandbox

async with Sandbox(image="aif-agent-sandbox", memory="4G", cpus=4) as sb:
    result = await sb.execute("pip list")
```

## What's Included

Built on Ubuntu 24.04, this overlay adds:

- **Python 3.12** virtual environment at `/opt/python` (with `pip`)
- **uv** fast Python package manager
- **build-essential** (gcc, make, etc.)
- **requests** HTTP library
- **pyyaml** YAML parsing
- **ddgs** DuckDuckGo Search API
- **markitdown** Markdown conversion tool

## License

MIT

# Reasoning Core - MCP Server Registry Submission

## Server Information

- **Name**: reasoning-core
- **Description**: A System 2 sidecar for structural reasoning in LLM-based software development
- **Version**: 1.0.0
- **Author**: Jakub Sikora
- **License**: MIT
- **Homepage**: https://github.com/jakubkrzysztofsikora/reasoning-core
- **Repository**: https://github.com/jakubkrzysztofsikora/reasoning-core

## MCP Implementation

### Server Type

- **Type**: HTTP Server
- **Endpoint**: `127.0.0.1:8765`
- **Protocol**: HTTP/1.1
- **Authentication**: None (local service)

### Tools

#### 1. gate_edit

**Description**: Gate edit operations based on structural analysis

**Input Schema**:
```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string",
      "description": "File path being edited"
    },
    "before_src": {
      "type": "string",
      "description": "Source code before edit"
    },
    "after_src": {
      "type": "string",
      "description": "Source code after edit"
    },
    "session_id": {
      "type": "string",
      "description": "Optional session identifier"
    }
  },
  "required": ["path", "before_src", "after_src"]
}
```

**Output**: ImpactReport with architectural impact score, risk vector, and recommendation

#### 2. get_health

**Description**: Get service health status

**Input Schema**: `{}`

**Output**: Health status including model loaded state, supported languages, and version

#### 3. list_languages

**Description**: List supported programming languages

**Input Schema**: `{}`

**Output**: Array of supported language identifiers

#### 4. get_metrics

**Description**: Get service performance metrics

**Input Schema**: `{}`

**Output**: Metrics including latency percentiles, error counts, and request statistics

## Features

### Structural Analysis

- **AST Parsing**: Tree-sitter based Abstract Syntax Tree extraction for multiple languages
- **Call Graph Analysis**: Builds and analyzes call graphs to understand dependencies
- **Semantic Embeddings**: Uses Mamba-130M State Space Model for code understanding

### Risk Assessment

- **8-Dimensional Risk Vector**: Computes risk scores for:
  - Cyclomatic complexity
  - Fan-in (in-degree)
  - Fan-out (out-degree)
  - Depth
  - Churn
  - Coupling
  - Cohesion
  - Novelty

- **Per-File-Kind Thresholds**: Different thresholds for source code, tests, plans, and documentation
- **Cold Start Handling**: Special handling for new files with no history
- **Impact Reports**: Human-readable summaries with repair hints

### Integration

- **Multi-CLI Support**: Works with:
  - Claude Code (native hooks)
  - Gemini CLI (Claude-compatible hooks)
  - GitHub Copilot CLI (MCP tool-based gating)
  - Mistral Vibe CLI (post-turn hooks + MCP)

- **9 Hook Layers**: L1-L9 for comprehensive interception of LLM agent actions

## Requirements

### System Requirements

- **OS**: macOS, Linux, Windows (WSL)
- **Python**: 3.11+
- **CPU**: Any modern CPU (no GPU required)
- **RAM**: 200MB minimum for model inference
- **Disk**: 250MB for Mamba checkpoint

### Dependencies

- tree-sitter
- transformers (HuggingFace)
- fastapi
- uvicorn
- torch (CPU only)
- mamba-ssm

## Installation

```bash
# Clone the repository
git clone https://github.com/jakubkrzysztofsikora/reasoning-core.git
cd reasoning-core

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Download Mamba checkpoint
huggingface-cli download state-spaces/mamba-130m-hf
```

## Usage

### Starting the Server

```bash
# Direct execution
python3 -m src.s2_core

# Or use the supervisor for automatic restart
bash scripts/start-sidecar.sh
```

The server will start on `127.0.0.1:8765` by default.

### Configuration

Configuration is done via environment variables and CLI-specific settings files.

### CLI Integration

Each supported CLI has specific integration instructions:

#### Claude Code

Add to `.claude/settings.json`:
```json
{
  "hooks": {
    "preToolUse": ["path/to/reasoning-core/hooks/pre_edit_guard.py"],
    "postToolUse": ["path/to/reasoning-core/hooks/post_bash_revive.py"]
  }
}
```

#### Other CLIs

See repository documentation for Gemini CLI, GitHub Copilot CLI, and Mistral Vibe CLI integration.

## Performance

- **Inference Time**: ~98 seconds per run (CPU)
- **Memory Usage**: ~200MB RAM
- **Throughput**: Depends on hardware, typically 1-2 requests per minute

## Evaluation

### Benchmark Results

- **Token Savings**: 8.2% average reduction across all tasks
- **Plan Quality**: +0.32 improvement (3.62 to 3.94 on 1-5 BARS scale)
- **Implementation Quality**: +0.20 improvement (3.80 to 4.00)
- **Test Pass Rate**: 100% on locked and rotated tests

### Evaluation Setup

- **Tasks**: 8 real-world engineering scenarios
- **Runs**: 48 total (n=3 per task x setup)
- **Judges**: 3 cross-family models (Gemini, Vibe, Qwen-Coder)
- **Decision Rule**: Lexicographic ordering with multiple gates

## Limitations

- Currently focused on Python ecosystem
- Adds latency to LLM agent runs
- Requires Tree-sitter grammars for each language
- No GPU acceleration (CPU only)

## Roadmap

- Support for more programming languages
- Improved calibration methodology
- Enhanced visualization of risk vectors
- Integration with additional CLI agents
- GPU acceleration support

## Security

- **Local Operation**: 100% local, no external telemetry
- **Data Privacy**: All code analysis happens on the developer's machine
- **No Internet Required**: Works offline after initial setup

## Support

- **Documentation**: https://github.com/jakubkrzysztofsikora/reasoning-core#readme
- **Issues**: https://github.com/jakubkrzysztofsikora/reasoning-core/issues
- **Discussions**: https://github.com/jakubkrzysztofsikora/reasoning-core/discussions
- **Email**: jakub.sikora@example.com

## License

MIT License - Copyright (c) 2026 Jakub Sikora

## Tags

- code-analysis
- llm
- system2
- structural-reasoning
- ast-parsing
- mamba
- tree-sitter
- quality-assurance
- software-engineering
- agentic-ai

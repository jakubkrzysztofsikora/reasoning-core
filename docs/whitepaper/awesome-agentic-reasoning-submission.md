# Reasoning Core - Awesome Agentic Reasoning Submission

## Project Information

- **Name**: reasoning-core
- **Description**: A System 2 sidecar that augments LLM-based coding agents with structural reasoning capabilities
- **Repository**: https://github.com/jakubkrzysztofsikora/reasoning-core
- **License**: MIT
- **Author**: Jakub Sikora
- **Stars**: (check GitHub)
- **Language**: Python

## Category

- **Primary**: Code Generation / Software Engineering
- **Secondary**: Agentic Workflows / Quality Assurance

## Key Features

### Architecture

- **Dual-Process Theory**: Implements System 1 (fast, linguistic) + System 2 (slow, structural) architecture
- **Sidecar Pattern**: Runs as a separate service alongside LLM coding agents
- **HTTP API**: RESTful API on 127.0.0.1:8765
- **MCP Server**: Model Context Protocol compatible

### Structural Analysis

- **AST Parsing**: Tree-sitter based Abstract Syntax Tree extraction
- **Call Graph Analysis**: Builds and analyzes call graphs
- **Semantic Embeddings**: Mamba-130M State Space Model for code understanding
- **Risk Vector**: 8-dimensional risk assessment (cyclomatic, fan-in/out, depth, churn, coupling, cohesion, novelty)

### Quality Gating

- **Per-File-Kind Thresholds**: Adaptive thresholds for different file types
- **Cold Start Handling**: Special handling for new files
- **Shadow Mode**: Observation mode before enforcement
- **Impact Reports**: Human-readable summaries and repair hints

### Integration

- **Multi-CLI Support**: Claude Code, Gemini CLI, GitHub Copilot CLI, Mistral Vibe CLI
- **9 Hook Layers**: L1-L9 for comprehensive action interception
- **CLI Tool**: `rc` command for diagnostics and control

## Technical Details

### Dependencies

- Python 3.11+
- Tree-sitter
- Transformers (HuggingFace)
- FastAPI
- PyTorch (CPU only)
- Mamba-SSM

### Performance

- **Inference**: ~98 seconds per run (CPU)
- **Memory**: ~200MB RAM for model
- **Disk**: ~250MB for Mamba checkpoint

### Evaluation

- **Tasks**: 8 real-world engineering scenarios
- **Runs**: 48 total (n=3 per task x setup)
- **Judges**: 3 cross-family models
- **Token Savings**: 8.2% average
- **Quality Improvement**: +0.32 plan quality, +0.20 implementation quality
- **Test Pass Rate**: 100% on locked/rotated tests

## Use Cases

1. **Code Review**: Structural analysis of proposed changes
2. **Code Generation**: Quality gating for generated code
3. **Refactoring**: Risk assessment for structural changes
4. **Bug Fixing**: Impact analysis of fixes
5. **Feature Development**: Maintaining architectural invariants

## Comparison with Other Projects

### Similar Projects

- **SWE-agent**: Focuses on software engineering tasks but lacks structural reasoning
- **Copilot**: Commercial product without structural analysis
- **Codeium**: Similar to Copilot, no structural reasoning
- **TabNine**: Local completion, no cross-file analysis

### Advantages

- **Structural Blind Spot**: Addresses the fundamental limitation of LLMs
- **Hybrid Architecture**: Combines LLM strengths with specialist analysis
- **Local Operation**: No external telemetry, full privacy
- **Extensible**: Supports any language with Tree-sitter grammar

### Disadvantages

- **Latency**: Adds ~98 seconds per run
- **Complexity**: Requires setup and configuration
- **Scope**: Currently focused on Python ecosystem

## Installation

```bash
git clone https://github.com/jakubkrzysztofsikora/reasoning-core.git
cd reasoning-core
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
huggingface-cli download state-spaces/mamba-130m-hf
```

## Usage Example

```bash
# Start the sidecar
python3 -m src.s2_core

# Configure your CLI agent to use reasoning-core hooks
# (See repository documentation for CLI-specific instructions)
```

## Documentation

- **README**: https://github.com/jakubkrzysztofsikora/reasoning-core#readme
- **Architecture**: https://github.com/jakubkrzysztofsikora/reasoning-core/blob/main/docs/ARCHITECTURE.md
- **Whitepaper**: https://github.com/jakubkrzysztofsikora/reasoning-core/tree/main/docs/whitepaper
- **Evaluation**: https://github.com/jakubkrzysztofsikora/reasoning-core/blob/main/docs/EVAL_DESIGN.md

## Contributing

- **Issues**: https://github.com/jakubkrzysztofsikora/reasoning-core/issues
- **Pull Requests**: https://github.com/jakubkrzysztofsikora/reasoning-core/pulls
- **Discussions**: https://github.com/jakubkrzysztofsikora/reasoning-core/discussions

## License

MIT License - Copyright (c) 2026 Jakub Sikora

## Contact

- **Author**: Jakub Sikora
- **Email**: jakub.sikora@example.com
- **GitHub**: https://github.com/jakubkrzysztofsikora

## Tags

#llm #code-generation #software-engineering #agentic-ai #structural-analysis #tree-sitter #mamba #system2 #quality-assurance

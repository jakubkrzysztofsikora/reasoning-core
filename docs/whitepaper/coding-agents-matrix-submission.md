# Reasoning Core - AI Coding Agents Matrix Submission

## Basic Information

- **Project Name**: reasoning-core
- **Repository**: https://github.com/jakubkrzysztofsikora/reasoning-core
- **License**: MIT
- **Author**: Jakub Sikora
- **First Commit**: 2025
- **Latest Commit**: May 8, 2026

## Features

### Core Capabilities

- **System 1 + System 2 Architecture**: Combines fast LLM processing with deliberate structural analysis
- **Tree-sitter AST Parsing**: Extracts Abstract Syntax Trees for multiple languages
- **Mamba SSM Backbone**: Uses Mamba-130M State Space Model for semantic embeddings
- **8-Dimensional Risk Vector**: Computes risk scores for cyclomatic complexity, fan-in/out, depth, churn, coupling, cohesion, and novelty
- **Per-File-Kind Thresholds**: Different thresholds for source code, tests, plans, and documentation
- **Multi-CLI Support**: Works with Claude Code, Gemini CLI, GitHub Copilot CLI, and Mistral Vibe CLI

### Supported Languages

- Python (primary)
- JavaScript/TypeScript
- Java
- Go
- Rust
- Any language with Tree-sitter grammar

### Hook Layers

9 hook layers (L1-L9) for intercepting and analyzing LLM agent actions:
- Pre-bash guard
- Pre-edit guard (SSM scoring)
- Pre-plan guard
- Pre-task guard
- Post-bash revive
- Post-batch language audit
- Pre-compact guard
- Session start manifest
- Session resume inject

### Performance

- **Token Savings**: 8.2% average reduction across all tasks
- **Plan Quality Improvement**: +0.32 (3.62 to 3.94 on 1-5 BARS scale)
- **Implementation Quality Improvement**: +0.20 (3.80 to 4.00)
- **Test Pass Rate**: 100% on locked and rotated tests
- **Local Operation**: 100% local, no external telemetry

### Evaluation Results

- 8 real-world engineering tasks
- 48 total runs (n=3 per task x setup)
- 3 cross-family judges (Gemini, Vibe, Qwen-Coder)
- 29% reduction in token usage on cache-heavy tasks

## Installation

```bash
git clone https://github.com/jakubkrzysztofsikora/reasoning-core.git
cd reasoning-core
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
huggingface-cli download state-spaces/mamba-130m-hf
```

## Usage

```bash
# Start the sidecar service
python3 -m src.s2_core

# Or use the supervisor for automatic restart
bash scripts/start-sidecar.sh
```

## Configuration

Configuration via environment variables and CLI-specific settings files.

## Benchmarks

- **SWE-bench**: Compatible with real-world software engineering tasks
- **Custom Evaluation Suite**: 8 representative engineering scenarios
- **Multi-Judge Assessment**: Cross-family model evaluation

## Limitations

- Adds ~98 seconds per run (CPU inference)
- Requires Tree-sitter grammars for each language
- Currently focused on Python ecosystem

## Roadmap

- Support for more languages
- Improved calibration methodology
- Enhanced visualization of risk vectors
- Integration with more CLI agents

## Contact

- **Email**: jakub.sikora@example.com
- **GitHub**: https://github.com/jakubkrzysztofsikora
- **Issues**: https://github.com/jakubkrzysztofsikora/reasoning-core/issues

## License

MIT License

## Additional Information

- **Whitepaper**: Available in docs/whitepaper/
- **Architecture Documentation**: docs/ARCHITECTURE.md
- **Evaluation Design**: docs/EVAL_DESIGN.md
- **Evaluation Results**: docs/EVAL_RESULTS.md

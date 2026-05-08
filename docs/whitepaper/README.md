# Reasoning Core Whitepaper - LaTeX Boilerplate

This directory contains a professional LaTeX boilerplate for the Reasoning Core scientific whitepaper. The template is designed for submission to arXiv, academic conferences, or as a standalone technical whitepaper.

## Structure

```
whitepaper/
├── reasoning_core_whitepaper.tex    # Main LaTeX document
├── abstract.tex                     # Abstract content
├── sections/
│   ├── introduction.tex             # Section 1: Introduction
│   ├── related_work.tex             # Section 2: Related Work
│   ├── architecture.tex              # Section 3: Architecture Overview
│   ├── scoring.tex                  # Section 4: Scoring System
│   ├── implementation.tex           # Section 5: Implementation Details
│   ├── methodology.tex              # Section 6: Evaluation Methodology
│   ├── results.tex                  # Section 7: Results
│   ├── discussion.tex               # Section 8: Discussion
│   ├── limitations.tex              # Section 9: Limitations and Future Work
│   └── conclusion.tex               # Section 10: Conclusion
├── appendices/
│   ├── specifications.tex           # Appendix A: Technical Specifications
│   ├── protocol.tex                 # Appendix B: Evaluation Protocol
│   └── additional_results.tex        # Appendix C: Additional Results
├── references.bib                   # Bibliography
└── README.md                        # This file
```

## Prerequisites

- TeX Live 2025 (recommended for arXiv compatibility)
- Python 3.x (for bibliography processing)
- bibtex or biber

## Compilation

### Basic Compilation

```bash
# First pass to generate references
pdflatex reasoning_core_whitepaper.tex

# Generate bibliography
bibtex reasoning_core_whitepaper.aux

# Second pass to resolve references
pdflatex reasoning_core_whitepaper.tex

# Third pass to resolve all cross-references
pdflatex reasoning_core_whitepaper.tex
```

### Using Makefile

Create a `Makefile`:

```makefile
all:
	pdflatex reasoning_core_whitepaper.tex
	bibtex reasoning_core_whitepaper.aux
	pdflatex reasoning_core_whitepaper.tex
	pdflatex reasoning_core_whitepaper.tex

clean:
	rm -f *.aux *.bbl *.blg *.log *.out *.toc
```

Then run:

```bash
make all
```

## arXiv Submission

1. Create a zip file of all source files:
   ```bash
   zip reasoning-core-whitepaper.zip reasoning_core_whitepaper.tex abstract.tex sections/*.tex appendices/*.tex references.bib
   ```

2. Submit to arXiv via the web interface

3. arXiv will automatically compile with TeX Live 2025

## Content Status

### Filled from Existing Resources

The following content has been populated from the reasoning-core repository:

- Project overview and motivation (from README.md)
- Architecture description (from README.md and docs/ARCHITECTURE.md)
- Scoring system details (from README.md and src/s2_core.py)
- Implementation details (from README.md and source code)
- Evaluation methodology (from thoughts/shared/research/2026-05-08-iter3-eval-whitepaper.md)
- Results (from iter-3 whitepaper draft)
- Technical specifications (from source code and documentation)
- Evaluation protocol (from iter-3 whitepaper draft)

### Placeholders for Missing Content

The following sections contain explicit placeholders that need to be filled before publication:

- Formal problem definition (Section 1)
- Research questions (Section 1)
- Paper organization overview (Section 1)
- Comparison to specific papers on LLM code review (Section 2)
- Discussion of concurrent work (Section 2)
- Positioning relative to commercial products (Section 2)
- Detailed scoring math derivations (Section 4)
- Calibration methodology (Section 4)
- Comparison with other scoring approaches (Section 4)
- Detailed statistical methodology (Appendix B)
- Power analysis (Appendix B)
- Multiple testing correction (Appendix B)
- Detailed roadmap with timelines (Section 9)
- Resource requirements for future work (Section 9)
- Expected impact of proposed improvements (Section 9)
- Summary of contributions (Section 10)
- Impact statement (Section 10)
- Call to action for the research community (Section 10)
- Funding acknowledgements (Acknowledgements)
- Personal acknowledgements (Acknowledgements)
- Conflict of interest statement (Acknowledgements)
- More references from existing whitepaper drafts (references.bib)
- References to evaluation methodology papers (references.bib)
- References to software engineering literature (references.bib)

## Figures

The following figures are referenced but need to be created:

- figures/architecture_diagram.pdf - System 1 + System 2 architecture diagram
- Additional diagrams as needed for clarity

## Customization

### Document Class Options

The main document uses:
```latex
\documentclass[10pt, twocolumn, letterpaper]{article}
```

For different formats:
- Single column: Remove `twocolumn`
- Different paper size: Change `letterpaper` to `a4paper`
- Different font size: Change `10pt` to `11pt` or `12pt`

### Packages

The template includes packages for:
- Mathematics (amsmath, amssymb, amsthm)
- Graphics (graphicx, float, caption, subcaption)
- Tables (booktabs, multirow, array, longtable)
- Bibliography (natbib, doi, url, hyperref)
- Code listings (listings, xcolor)
- Layout (geometry, microtype)

Add or remove packages as needed for your specific requirements.

## Style Guide

### Citations

Use `\cite{key}` for citations. References are stored in `references.bib`.

### Cross-References

Use `\label{sec:label}` and `\ref{sec:label}` for sections.
Use `\label{tab:label}` and `\ref{tab:label}` for tables.
Use `\label{fig:label}` and `\ref{fig:label}` for figures.

### Mathematics

Use inline math: `$...$` or `\(...)`
Use display math: `\[...\]` or `\begin{equation}...\end{equation}`

### Code Listings

Use the `lstlisting` environment for code:
```latex
\begin{lstlisting}[language=python, caption={Example Code}]
def hello():
    print("Hello, World!")
\end{lstlisting}
```

Supported languages: python, json, bash, diff, etc.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test compilation
5. Submit a pull request

## License

This LaTeX boilerplate is provided under the MIT License. See the reasoning-core repository for the project license.

## Contact

For questions about the whitepaper content, contact: jakub.sikora@example.com

For questions about the LaTeX template, refer to the Overleaf documentation or LaTeX community resources.
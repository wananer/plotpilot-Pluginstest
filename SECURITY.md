# Security Policy

## Reporting a Vulnerability

If you discover a security issue in this repository, please avoid opening a public issue with exploit details.

Recommended approach:
- send a private report to the repository maintainer first
- include reproduction steps, affected files, and impact assessment
- if possible, include a minimal patch suggestion

## Scope

This repository is a plugin platform skeleton for PlotPilot.

Security-sensitive areas include:
- plugin discovery / loading
- static asset exposure under `/plugins`
- installer patch logic that rewrites host files
- frontend runtime script injection behavior

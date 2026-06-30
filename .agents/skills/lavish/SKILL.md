---
name: lavish
description: Generate, serve, and poll interactive HTML artifacts for user planning, review, and feedback using lavish-axi.
---

# Lavish-AXI Custom Skill

Use this skill when the user asks for a visual artifact, HTML explainer, interactive prototype, review surface, product or technical plan, comparison, report, or browser-based feedback loop.

## Usage Guidelines

1. **Create HTML Artifacts**:
   - Unless specified otherwise, create HTML files under the `.lavish/` directory in the current working directory.
   - Design direction: (1) Match requested or project design systems (Tailwind, local CSS/tokens, UI component library). (2) Fallback: run `npx lavish-axi design` to get Tailwind CSS v4 + DaisyUI v5 CDN snippet.
   - Reference other assets (images, CSS, etc.) using relative paths from the HTML file directory. Do not prepend `/`.

2. **Launch Lavish Session**:
   - Serve the HTML file:
     ```bash
     npx -y lavish-axi <html-file-path>
     ```
     This opens the browser automatically for the user to review.

3. **Poll for Feedback**:
   - Run the poll command as a background task or in the foreground to collect user annotations and comments:
     ```bash
     npx -y lavish-axi poll <html-file-path>
     ```
   - Process the returned feedback (annotations, selections, prompt queue), update the HTML or codebase, and iterate.

4. **Shutdown**:
   - End the session:
     ```bash
     npx -y lavish-axi end <html-file-path>
     ```
   - Stop the server:
     ```bash
     npx -y lavish-axi stop
     ```

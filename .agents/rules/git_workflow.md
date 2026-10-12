# Git Workflow Rule

Always commit and push verified changes to GitHub upon completing tasks.

## Guidelines:
1. Ensure changes are tested and working before committing.
2. Stage modified files explicitly or appropriately (`git add <files>`). Avoid committing secrets (`.env`, credentials, tokens) or cache files.
3. Write clear, concise conventional commit messages (e.g. `feat: ...`, `fix: ...`, `refactor: ...`).
4. Push commits to the remote repository (`git push origin <branch>`) so changes are always synchronized with GitHub.

# Publishing to GitHub, one project at a time

## 1. Create the empty repo on GitHub
On github.com choose New repository, name it `ai-automation-portfolio`, and leave it empty (no README, no .gitignore).

## 2. First push: scaffold only
```bash
cd ai-automation-portfolio
git init -b main
git add README.md .gitignore .github docs scripts
git commit -m "Add repo scaffold, CI and project template"
git remote add origin https://github.com/<your-username>/ai-automation-portfolio.git
git push -u origin main
```

## 3. Push project 01
```bash
git add projects/01-ecom-order-fulfillment
git commit -m "Add project 01: order-to-fulfillment automation prototype"
git push
```
Open the **Actions** tab: the `tests` workflow should go green.

## 4. Every later project
```bash
git add projects/02-your-project README.md   # README.md: update the status row
git commit -m "Add project 02: <name>"
git push
```

## Tips
- If git asks who you are: `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"`.
- Use a Personal Access Token (or GitHub's browser sign-in) when pushing over HTTPS.
- One commit per project keeps your history readable as a progress log.

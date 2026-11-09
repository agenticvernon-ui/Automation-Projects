# Project template

Folder: `projects/NN-short-name/`

```
README.md            (sections below)
<module>.py          core logic, standard library where possible
test_<module>.py     tests, including every exception case
demo.py              a run a reviewer can watch in 10 seconds
sample_data.json     simulated data only, no real customer information
schema.sql           if the project uses a database
```

## README sections
1. **Problem**: one sentence on the business problem.
2. **Honest scope**: what is simulated and what is not built yet.
3. **Requirements**: what the workflow must do and must never do.
4. **Workflow logic**: numbered steps, including where a human approves or reviews.
5. **Exception handling**: each failure case, its code, and who resolves it.
6. **Run it**: exact commands.
7. **Integration notes**: how n8n, APIs or the CRM would connect.
8. **Troubleshooting**: symptom, likely cause, fix.
9. **Next steps**: what is not built yet.

## Rules
- Simulated data only; never commit real customer, financial or credential data (`.env` is git-ignored).
- No outcome or income claims in READMEs; describe what the code does.
- A project is "done" when its tests pass and its README matches what the code actually does.

# Put this repo on GitHub using only the website (no git commands)

You need a free GitHub account. Everything below happens in your browser.

## Before you start
1. **Unzip** `ai-automation-portfolio.zip` on your computer. GitHub does not unzip files for you.
2. Show hidden files so you can see the `.github` and `.gitignore` items:
   - Mac: in Finder press `Cmd + Shift + .`
   - Windows: File Explorer > View > Show > Hidden items

## Step 1: Create the repository
1. Go to github.com, click **+** (top right) > **New repository**.
2. Name: `ai-automation-portfolio`. Choose **Public** if you want employers and clients to see it.
3. Leave "Add a README", ".gitignore" and "license" **unchecked**. Click **Create repository**.

## Step 2: Upload the scaffold (commit 1)
1. On the empty repo page click **uploading an existing file**.
2. Drag these items from the unzipped folder into the browser: `README.md`, `.gitignore`, `.github`, `docs`, `scripts`.
   Do **not** drag `projects` yet.
3. Wait for the upload bar to finish. In "Commit changes" type: `Add repo scaffold, CI and project template`.
4. Choose **Commit directly to the main branch** and click **Commit changes**.

## Step 3: Upload project 01 (commit 2)
1. On the repo page click **Add file** > **Upload files**.
2. Drag the folder `projects/01-ecom-order-fulfillment` into the browser (the whole folder, so the path
   `projects/01-ecom-order-fulfillment/...` is kept).
3. Commit message: `Add project 01: order-to-fulfillment automation prototype`. Click **Commit changes**.

## Step 4: Upload project 04 (commit 3)
Same as step 3, with the folder `projects/04-pos-crm-sync` and the message
`Add project 04: POS-to-CRM sync prototype`.

## Step 5: Upload project 02 (commit 4)
Same as step 3, with the folder `projects/02-crm-retention` and the message
`Add project 02: CRM retention automation prototype`. Then open the root `README.md` online (pencil icon) to
confirm the table shows projects 01, 02 and 04 as prototypes.

## Step 6: Upload project 05 (commit 5)
Same as step 3, with the folder `projects/05-restaurant-inventory` and the message
`Add project 05: restaurant inventory and reorder prototype`. Do not upload an `out` folder if one appears; it only holds generated CSV files.

## Step 7: Upload project 03 (commit 6)
Same as step 3, with the folder `projects/03-invoice-automation` and the message
`Add project 03: invoice automation prototype`.

## Step 8: Check that the tests run
1. Click the **Actions** tab. If GitHub asks you to enable workflows, click the green button.
2. Open the latest "tests" run. A green check means every project's tests passed on GitHub's computers.
   A red X means open the run and read the failing step; it usually names the file.

## If dragging a folder does not work
Use **Add file > Create new file**. In the filename box type the full path, for example
`projects/04-pos-crm-sync/pos_crm.py` (typing `/` creates folders), paste the file's contents, and commit.
Repeat for each file. For `.github/workflows/tests.yml` type that exact path.

## Run the code from GitHub (without installing anything)
1. On the repo page click the green **Code** button > **Codespaces** tab > **Create codespace on main**.
2. A VS Code window opens in your browser. In its terminal run:
   ```bash
   cd projects/04-pos-crm-sync
   python3 demo.py
   python3 -m unittest -v
   ```
Codespaces has a free monthly allowance for personal accounts; check GitHub's current limits and stop the
codespace when you finish so it does not use your hours.

## Later: adding new work
Each time: **Add file > Upload files**, drag the new `projects/NN-name` folder, commit with a message like
`Add project 06: <name>`. Then edit the root `README.md` (pencil icon) to add its row, and commit that too.

## Safety checklist before making the repo public
- No real customer names, emails, phone numbers or financial data anywhere (the samples are fake).
- No passwords, API keys or `.env` files.
- README claims match what the code actually does.

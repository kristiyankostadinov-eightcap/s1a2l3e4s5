### **Step 1: Set Up Your GitHub Repository**

First, you need to get your project code into a GitHub repository.

1.  **Create a New Repository:**
    *   Go to [GitHub](https://github.com) and log in.
    *   Click the **"+"** icon in the top-right corner and select **"New repository"**.
    *   Give it a name (e.g., `automated-market-briefing`).
    *   Choose "Public" so that GitHub Pages can serve the website.
    *   Click **"Create repository"**.

2.  **Add Your Project Files:**
    *   On your local machine, navigate to your project folder.
    *   Initialize a git repository, add your files, and push them to GitHub. Open your terminal or command prompt and run:
        ```bash
        git init -b main
        git add .
        git commit -m "Initial commit of the project"
        git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY_NAME.git
        git push -u origin main
        ```
    *   **Important:** Make sure your `.env` file is **not** uploaded. To prevent this, create a file named `.gitignore` in your project's root directory.

3.  **Create a `.gitignore` file:**
    *   In the main folder of your project, create a new file named `.gitignore`.
    *   Add the following lines to it to ensure sensitive files and unnecessary folders are not tracked by Git:
        ```
        # Environment variables
        .env

        # Python cache
        __pycache__/
        *.pyc

        # IDE folders
        .idea/
        .vscode/
        ```
    *   Save the file. If you already accidentally committed your `.env` file, you will need to remove it from your Git history.

### **Step 2: Add Your API Key to GitHub Secrets**

To use your `OPENROUTER_API_KEY` securely in the automated workflow, you need to add it to GitHub's encrypted "Secrets."

1.  In your new GitHub repository, go to **"Settings"**.
2.  In the left sidebar, navigate to **"Secrets and variables"** > **"Actions"**.
3.  Click the **"New repository secret"** button.
4.  For the **"Name"**, enter `OPENROUTER_API_KEY`.
5.  For the **"Value"**, paste the actual API key from your local `.env` file.
6.  Click **"Add secret"**. Your script running in GitHub Actions will now have secure access to this key.

### **Step 3: Integrate MkDocs for Website Generation**

Now, you'll set up MkDocs to turn your Markdown reports into a website.

1.  **Install MkDocs:**
    *   It's a good practice to add MkDocs to your project's requirements. First, install it locally to create the configuration file:
        ```bash
        pip install mkdocs mkdocs-material
        ```
    *   The `mkdocs-material` theme is a popular and professional-looking theme.

2.  **Update `requirements.txt`:**
    *   Add `mkdocs` and `mkdocs-material` to your `requirements.txt` file so the automated workflow can install them:
        ```
        # your other libraries...
        mkdocs
        mkdocs-material
        ```

3.  **Create the `mkdocs.yml` Configuration File:**
    *   In the root directory of your project, create a file named `mkdocs.yml`. This file tells MkDocs how to build your site.
    *   Add the following configuration to it:
        ```yaml
        site_name: "Daily Market Briefings"
        theme:
          name: "material"
          palette:
            # Light mode
            - scheme: default
              toggle:
                icon: material/weather-night
                name: Switch to dark mode
            # Dark mode
            - scheme: slate
              toggle:
                icon: material/weather-sunny
                name: Switch to light mode
        nav:
          - 'Home': 'index.md'
        docs_dir: 'snapshots' # Tell MkDocs to look for Markdown files here
        ```

4.  **Create a Homepage for the Website:**
    *   MkDocs needs an `index.md` file to serve as the homepage.
    *   Inside your `/snapshots` folder, create a new file named `index.md`.
    *   Add some introductory text to it, for example:
        ```markdown
        # Welcome to the Automated Daily Market Briefings

        This site contains automatically generated daily analysis of key financial markets. Please use the navigation to browse reports by date.

        The latest briefing is always available at the top of the navigation bar.
        ```

### **Step 4: Create the GitHub Actions Workflow**

This is the core of the automation. You will create a workflow file that runs your script, builds the website, and deploys it.

1.  **Create the Workflow Directory:**
    *   In the root of your project, create a new folder named `.github`.
    *   Inside `.github`, create another folder named `workflows`.

2.  **Create the Workflow YAML File:**
    *   Inside the `.github/workflows` folder, create a new file named `daily-briefing.yml`.
    *   Paste the following code into this file:

        ```yaml
        name: Generate and Deploy Daily Market Briefing

        on:
          workflow_dispatch: # Allows manual triggering
          schedule:
            - cron: '30 5 * * *' # Runs every day at 05:30 UTC

        permissions:
          contents: write # Allows the job to commit to the repo and deploy to Pages

        jobs:
          build-and-deploy:
            runs-on: ubuntu-latest
            steps:
              - name: Checkout Repository
                uses: actions/checkout@v4

              - name: Set up Python
                uses: actions/setup-python@v5
                with:
                  python-version: '3.11' # Or your preferred Python version

              - name: Install Dependencies
                run: |
                  python -m pip install --upgrade pip
                  pip install -r requirements.txt

              - name: Run the Market Analysis Script
                env:
                  OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
                run: python main.py

              - name: Build the MkDocs Website
                run: mkdocs build

              - name: Deploy to GitHub Pages
                uses: peaceiris/actions-gh-pages@v3
                with:
                  github_token: ${{ secrets.GITHUB_TOKEN }}
                  publish_dir: ./site
        ```

### **Step 5: Configure GitHub Pages and Deploy**

The final step is to tell GitHub to serve a website from your repository's content.

1.  **Enable GitHub Pages:**
    *   In your GitHub repository, go to **"Settings"**.
    *   In the left sidebar, click on **"Pages"**.
    *   Under **"Build and deployment"**, for the **"Source"**, select **"Deploy from a branch"**.
    *   Under **"Branch"**, select the `gh-pages` branch. (The `actions-gh-pages` action we used will automatically create and push to this branch).
    *   Click **"Save"**.

2.  **Trigger the First Deployment:**
    *   The workflow is scheduled to run daily, but you can trigger it manually for the first time.
    *   Go to the **"Actions"** tab in your repository.
    *   In the left sidebar, click on **"Generate and Deploy Daily Market Briefing"**.
    *   You will see a message that this workflow has a `workflow_dispatch` event. Click the **"Run workflow"** dropdown, and then the **"Run workflow"** button.

3.  **View Your Live Website:**
    *   The action will now run. It may take a few minutes. You can click on it to watch the progress.
    *   Once the job is complete, go back to **"Settings"** > **"Pages"**.
    *   You will see a URL at the top of the page (e.g., `https://YOUR_USERNAME.github.io/YOUR_REPOSITORY_NAME/`). This is your live website. Click it to see your deployed market briefing.

Your project is now fully automated! Every day at 05:30 UTC, the GitHub Action will run your Python script, generate a new Markdown report, rebuild the website with the new report included, and deploy it to your public GitHub Pages site.
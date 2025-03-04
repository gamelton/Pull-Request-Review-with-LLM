# Pull Request Review Automation with LLM

This repository contains a Python script that automates the process of reviewing pull requests (PRs) in BitBucket using a Large Language Model (LLM) hosted on an OLLAMA server. The script fetches open PRs, analyzes the code changes using the LLM, and posts the review results as comments on the PRs.

## Features
- Fetches open pull requests from a BitBucket repository.
- Sends the code diff to an LLM for analysis via OLLAMA API.
- Posts the LLM's review as a comment on the corresponding PR.

## Prerequisites
- Python 3.x
- `requests` library (`pip install requests`)
- BitBucket API access token
- OLLAMA server with a compatible LLM model (e.g., `deepseek-r1:14b`)

## Setup
1. Clone this repository:
   ```bash
   git clone https://github.com/gamelton/Pull-Request-Review-with-LLM.git
   ```

2. Install the required Python library:
   ```bash
   pip install requests
   ```
3. Update the script with your BitBucket and OLLAMA server details:
   - Replace `<your-bitbucket-token>` with your BitBucket API token.
   - Replace `<project name>` and `<repository>` with your BitBucket project and repository names.
   - Replace `http://ollama.example.com:11434/api/chat` with your OLLAMA server URL.

4. Make the script executable:
   ```bash
   chmod +x /opt/pr_review.py
   ```

## Usage
Run the script manually:
```bash
python3 /opt/pr_review.py
```

### Scheduling with Cron
To run the script automatically every two hours, add the following line to your crontab:
```bash
0 */2 * * * root /usr/bin/python3 /opt/pr_review.py
```

## Script Workflow
1. Fetches open PRs created in the last two hours.
2. Retrieves the diff for each PR.
3. Sends the diff to the LLM for analysis.
4. Posts the LLM's review as a comment on the PR.

## Notes
- The LLM review is generated automatically and may not always be accurate. Use it as a supplementary tool, not a replacement for human review.
- Ensure your OLLAMA server is running and the specified model is available.
